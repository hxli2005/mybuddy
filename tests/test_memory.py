"""M3 记忆系统测试:短期记忆、长期记忆、用户画像、事实抽取、记忆工具。

MockEmbedding 仅保留为旧构造参数兼容测试;当前 LongTermMemory 使用结构化文本检索。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

import pytest

from mybuddy.config import Config
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, Role, ToolSpec
from mybuddy.memory import (
    FactExtractResult,
    LongTermMemory,
    MemoryGovernance,
    MemoryManager,
    ShortTermMemory,
    UserProfile,
)
from mybuddy.storage import ProfileClaim, init_db, session_scope

# =============================================================================
# Mock embedding function:旧接口兼容,当前文本存储不使用
# =============================================================================

VEC_DIM = 128


def _tokenize(text: str) -> list[str]:
    """简单分词:按空白+标点切分,取 1-4 字片段。"""
    import re

    tokens: list[str] = []
    chars = re.findall(r"[一-鿿]+", text)
    for chunk in chars:
        for length in (1, 2):
            for i in range(len(chunk) - length + 1):
                tokens.append(chunk[i : i + length])
    words = re.findall(r"[a-zA-Z0-9]+", text)
    tokens.extend(w.lower() for w in words)
    if not tokens:
        tokens = [text.strip().lower()]
    return tokens


def _token_hash(token: str) -> int:
    h = hashlib.md5(token.encode()).digest()
    return int.from_bytes(h[:4], "big") % VEC_DIM


class MockEmbedding:
    """Chroma-compatible embedding function。

    Chroma 要求 embedding function 有 name() 方法和 __call__,不能是裸函数。
    """

    def name(self) -> str:
        return "mock_embed"

    def __call__(self, input: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in input:
            vec = [0.0] * VEC_DIM
            tokens = _tokenize(text)
            for t in tokens:
                idx = _token_hash(t)
                vec[idx] = 1.0
            norm = sum(v * v for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors

    def embed_query(self, input: Any) -> list[list[float]]:
        """Chroma query 路径调用此方法而非 __call__。

        Chroma 传过来的 input 可能是 str 或 list[str],返回必须是 list[list[float]]
        (Rust backend 要求二维),即使只有一个 query。
        """
        texts = input if isinstance(input, list) else [input]
        return self.__call__(texts)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        """Chroma add 路径可能调用此方法。"""
        return self.__call__(input)


mock_embed = MockEmbedding()


class DummyProvider(BaseLLMProvider):
    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        return LLMResponse(text="{}", finish_reason="stop")


class StaticProvider(BaseLLMProvider):
    def __init__(self, text: str) -> None:
        self._text = text

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        return LLMResponse(text=self._text, finish_reason="stop")


# =============================================================================
# ShortTermMemory
# =============================================================================


def test_short_term_add_and_get() -> None:
    stm = ShortTermMemory(capacity=3)
    stm.add(Message(role=Role.USER, content="hi"))
    stm.add(Message(role=Role.ASSISTANT, content="hello"))
    assert len(stm) == 2
    msgs = stm.get_all()
    assert msgs[0].content == "hi"
    assert msgs[1].content == "hello"


def test_short_term_wraps_capacity() -> None:
    stm = ShortTermMemory(capacity=2)
    stm.add(Message(role=Role.USER, content="a"))
    stm.add(Message(role=Role.USER, content="b"))
    stm.add(Message(role=Role.USER, content="c"))
    msgs = stm.get_all()
    assert len(msgs) == 2
    assert msgs[0].content == "b"
    assert msgs[1].content == "c"


def test_short_term_clear() -> None:
    stm = ShortTermMemory(capacity=10)
    stm.add(Message(role=Role.USER, content="a"))
    stm.clear()
    assert len(stm) == 0


# =============================================================================
# LongTermMemory (三层结构化文本)
# =============================================================================


@pytest.fixture
def ltm(tmp_path) -> LongTermMemory:
    chroma_dir = tmp_path / "chroma_test"
    chroma_dir.mkdir()
    return LongTermMemory(
        persist_dir=str(chroma_dir),
        collection_name="test_ltm",
        embedding_fn=mock_embed,
    )


def test_long_term_add_and_search(ltm) -> None:
    mid = ltm.add("用户小明喜欢喝美式咖啡")
    assert mid

    hits = ltm.search("咖啡")
    assert len(hits) >= 1
    content_lower = hits[0]["content"].lower()
    assert "美式" in content_lower or "咖啡" in content_lower
    meta = hits[0]["metadata"]
    assert meta["memory_key"].startswith("memory:")
    assert meta["source"] == "manual"
    assert meta["created_at"]
    assert meta["updated_at"]
    assert meta["observed_at"]


def test_long_term_three_layer_files(ltm, tmp_path) -> None:
    mid = ltm.add("用户正在准备项目汇报", mem_type="memory")
    tid = ltm.record_conversation_turn(
        session_id="s1",
        turn_id="turn_1",
        user_text="我在准备项目汇报",
        assistant_text="可以先整理架构和成果。",
    )

    assert mid
    assert tid == "turn_1"
    assert list((tmp_path / "chroma_test" / "archive").glob("*.md"))
    assert list((tmp_path / "chroma_test" / "conversations").glob("*.jsonl"))
    assert list((tmp_path / "chroma_test" / "raw").glob("*.jsonl"))


def test_long_term_search_with_type_filter(ltm) -> None:
    ltm.add("事实:用户叫小明", mem_type="memory")
    ltm.add("命题:用户可能喜欢早起", mem_type="claim")

    hits_mem = ltm.search("用户名字", top_k=5, mem_type="memory")
    assert all(h["metadata"].get("type") == "memory" for h in hits_mem)

    hits_claim = ltm.search("早起习惯", top_k=5, mem_type="claim")
    assert all(h["metadata"].get("type") == "claim" for h in hits_claim)


def test_long_term_delete(ltm) -> None:
    mid = ltm.add("测试记忆")
    assert ltm.count() >= 1
    ltm.delete(mid)
    hits = ltm.search("测试记忆")
    assert all(h["id"] != mid for h in hits)


def test_long_term_custom_uid(ltm) -> None:
    uid = ltm.add("custom id test", uid="my_custom_id")
    assert uid == "my_custom_id"
    hits = ltm.search("custom id test")
    assert any(h["id"] == "my_custom_id" for h in hits)


def test_long_term_update_metadata(ltm) -> None:
    uid = ltm.add("metadata test", extra_meta={"count": 1})
    hits_before = ltm.search("metadata test")
    assert hits_before[0]["metadata"].get("count") == 1

    ltm.update_metadata(uid, {"type": "memory", "count": 2})
    hits_after = ltm.search("metadata test")
    assert hits_after[0]["metadata"].get("count") == 2


def test_long_term_normalize_metadata_backfills_legacy_cards(ltm) -> None:
    legacy_path = ltm._archive_dir / "legacy.md"
    legacy_path.write_text(
        "---\nid: legacy\ntype: memory\n---\n\n旧记忆内容\n",
        encoding="utf-8",
    )

    assert ltm.normalize_metadata() == 1

    item = next(i for i in ltm.list_all() if i["id"] == "legacy")
    meta = item["metadata"]
    assert meta["source"] == "legacy"
    assert meta["status"] == "active"
    assert meta["memory_key"].startswith("memory:")
    assert meta["created_at"]
    assert meta["observed_at"]
    assert meta["last_seen_at"]


def test_memory_governance_merges_duplicate_memory(ltm) -> None:
    governance = MemoryGovernance(ltm)

    first = governance.add_or_merge(
        "用户正在准备项目汇报",
        mem_type="memory",
        source="fact_extraction",
    )
    second = governance.add_or_merge(
        "用户正在准备项目汇报",
        mem_type="memory",
        source="fact_extraction",
    )

    assert first.memory_id == second.memory_id
    assert second.action == "merged"
    items = ltm.list_all(mem_type="memory")
    assert len(items) == 1
    assert items[0]["metadata"]["occurrence_count"] == 2
    assert items[0]["metadata"]["last_seen_at"]


def test_memory_governance_marks_expired_open_thread_stale(ltm) -> None:
    governance = MemoryGovernance(ltm)
    governance.add_or_merge(
        "用户昨天要处理报告开头",
        mem_type="open_thread",
        extra_meta={"expires_at": "2000-01-01T00:00:00"},
    )

    assert governance.refresh_open_thread_lifecycle() == 1
    item = ltm.list_all(mem_type="open_thread")[0]
    assert item["metadata"]["status"] == "stale"
    assert item["metadata"]["stale_at"]


# =============================================================================
# UserProfile
# =============================================================================


@pytest.fixture
def profile_with_ltm(tmp_path):
    engine = init_db(str(tmp_path / "profile_test.db"))
    chroma_dir = tmp_path / "chroma_profile"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir),
        collection_name="test_profile",
        embedding_fn=mock_embed,
    )
    profile = UserProfile(engine, ltm)
    return profile


def test_profile_set_and_get_field(tmp_path) -> None:
    engine = init_db(str(tmp_path / "pf.db"))
    profile = UserProfile(engine, None)

    profile.set_field("名字", "小明")
    assert profile.get_field("名字") == "小明"

    profile.set_field("名字", "小红")  # overwrite
    assert profile.get_field("名字") == "小红"


def test_profile_get_all_fields(tmp_path) -> None:
    engine = init_db(str(tmp_path / "pf2.db"))
    profile = UserProfile(engine, None)
    profile.set_field("名字", "小明")
    profile.set_field("咖啡偏好", "美式")

    fields = profile.get_all_fields()
    assert fields == {"名字": "小明", "咖啡偏好": "美式"}


def test_profile_delete_field(tmp_path) -> None:
    engine = init_db(str(tmp_path / "pf3.db"))
    profile = UserProfile(engine, None)
    profile.set_field("测试", "值")
    assert profile.delete_field("测试") is True
    assert profile.get_field("测试") is None
    assert profile.delete_field("不存在") is False


def test_profile_add_and_search_claims(profile_with_ltm) -> None:
    profile = profile_with_ltm
    cid = profile.add_claim("用户周日晚上情绪较低", confidence=0.7, evidence_ids=["msg_1"])
    assert cid > 0

    hits = profile.search_claims("周末心情", top_k=5, min_confidence=0.5)
    assert len(hits) >= 1
    assert any("周日" in h["claim"] for h in hits)


def test_profile_add_claim_merges_duplicate_claim(profile_with_ltm) -> None:
    profile = profile_with_ltm

    cid1 = profile.add_claim("用户不喜欢空泛鼓励", confidence=0.5, evidence_ids=["turn_1"])
    cid2 = profile.add_claim("用户不喜欢空泛鼓励", confidence=0.8, evidence_ids=["turn_2"])

    assert cid1 == cid2
    claims = profile.get_all_claims()
    assert len(claims) == 1
    assert claims[0]["confidence"] == 0.8
    assert claims[0]["evidence_ids"] == ["turn_1", "turn_2"]


def test_profile_claim_lifecycle_metadata(profile_with_ltm) -> None:
    profile = profile_with_ltm

    cid = profile.add_claim(
        "用户不喜欢空泛鼓励",
        confidence=0.8,
        evidence_ids=["turn_1", "turn_2", "turn_3"],
    )

    claim = next(c for c in profile.get_all_claims() if c["sql_id"] == cid)
    assert claim["status"] == "active"
    assert claim["category"] == "boundary"
    assert claim["evidence_count"] == 3
    assert claim["evidence_days"]
    assert claim["first_seen_at"]
    assert claim["last_seen_at"]


def test_init_db_backfills_legacy_claim_evidence_metadata(tmp_path) -> None:
    db_path = tmp_path / "legacy_claims.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE profile_claims ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "claim TEXT, "
        "confidence FLOAT, "
        "evidence_ids_json TEXT, "
        "updated_at DATETIME)"
    )
    conn.execute(
        "INSERT INTO profile_claims "
        "(claim, confidence, evidence_ids_json, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (
            "用户不喜欢空泛鼓励",
            0.7,
            json.dumps(["turn_1", "turn_2"], ensure_ascii=False),
            "2026-05-01 09:00:00",
        ),
    )
    conn.commit()
    conn.close()

    engine = init_db(str(db_path))

    with session_scope(engine) as s:
        claim = s.query(ProfileClaim).one()
        assert claim.status == "active"
        assert claim.category == "general"
        assert claim.evidence_count == 2
        assert json.loads(claim.evidence_days_json or "[]") == ["2026-05-01"]
        assert claim.first_seen_at is not None
        assert claim.last_seen_at is not None


def test_profile_update_confidence(profile_with_ltm) -> None:
    profile = profile_with_ltm
    cid = profile.add_claim("测试命题", confidence=0.5)
    assert profile.update_confidence(cid, delta=0.2, new_evidence_id="msg_2") is True

    claims = profile.get_all_claims()
    test_claim = [c for c in claims if c["sql_id"] == cid][0]
    assert test_claim["confidence"] == 0.7
    assert "msg_2" in test_claim["evidence_ids"]


def test_profile_prune_low_confidence(profile_with_ltm) -> None:
    profile = profile_with_ltm
    profile.add_claim("低置信度命题", confidence=0.1)
    profile.add_claim("正常命题", confidence=0.8)
    deleted = profile.prune_low_confidence(threshold=0.3)
    assert deleted == 1

    claims = profile.get_all_claims()
    assert len(claims) == 1
    assert claims[0]["claim"] == "正常命题"


def test_profile_claims_fallback_without_ltm(tmp_path) -> None:
    """无 Chroma 时 search_claims 降级为 SQL 扫描。"""
    engine = init_db(str(tmp_path / "pf_fallback.db"))
    profile = UserProfile(engine, None)
    profile.add_claim("用户喜欢早起", confidence=0.7)
    profile.add_claim("低置信度命题", confidence=0.2)

    hits = profile.search_claims("不相关的查询", top_k=5, min_confidence=0.5)
    assert len(hits) == 1
    assert hits[0]["claim"] == "用户喜欢早起"


def test_memory_manager_prioritizes_relationship_context(tmp_path) -> None:
    engine = init_db(str(tmp_path / "manager.db"))
    cfg = Config()
    chroma_dir = tmp_path / "manager_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir),
        collection_name="manager_ctx",
        embedding_fn=mock_embed,
    )
    ltm.add(
        "用户说明天要写报告开头,但担心自己拖着不动。",
        mem_type="open_thread",
        extra_meta={
            "title": "报告开头还没写",
            "contact_reason": "用户说过明天别再拖",
            "triggers": ["报告", "拖延"],
        },
    )
    ltm.add(
        "用户上次不想写代码时,接受了把任务缩到一个最小动作的低压启动方式。",
        mem_type="shared_moment",
        extra_meta={
            "title": "那个没有催的晚上",
            "callback_style": "轻轻提起",
            "keywords": ["拖延", "报告"],
        },
    )
    ltm.add("用户不喜欢被强行打鸡血", mem_type="anti_preference")
    ltm.add("用户拖延了报告开头,报告开头还是没写", mem_type="memory")

    manager = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=DummyProvider())
    text, related_claim_ids = manager.build_context_section("我又拖延了,报告开头还是没写")

    assert related_claim_ids == []
    assert text.index("## 未完成话题") < text.index("## 关于用户")
    assert "那个没有催的晚上" in text
    assert "不喜欢被强行打鸡血" in text
    assert "## 关系线索" not in text


@pytest.mark.asyncio
async def test_memory_manager_extract_uses_governance_to_merge(tmp_path) -> None:
    engine = init_db(str(tmp_path / "manager_governance.db"))
    cfg = Config()
    cfg.memory.extract_after_turns = 1
    chroma_dir = tmp_path / "manager_governance_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir),
        collection_name="manager_governance",
        embedding_fn=mock_embed,
    )
    payload = json.dumps(
        {
            "facts": ["用户正在准备项目汇报"],
            "profile_fields": {},
            "claims": [{"claim": "用户担心项目汇报开头", "confidence": 0.7}],
            "relationship_memories": {
                "open_thread": [
                    {
                        "title": "项目汇报开头",
                        "content": "用户还没处理项目汇报开头。",
                        "contact_reason": "用户担心开头讲不顺",
                        "triggers": ["项目汇报", "开头"],
                    }
                ]
            },
        },
        ensure_ascii=False,
    )
    manager = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=StaticProvider(payload))

    manager.record_turn("我在准备项目汇报", "我们先看开头", turn_id="turn_1")
    assert await manager.maybe_extract() is True
    manager.record_turn("我还是在准备项目汇报", "那继续处理开头", turn_id="turn_2")
    assert await manager.maybe_extract() is True

    memories = ltm.list_all(mem_type="profile")
    open_threads = ltm.list_all(mem_type="open_thread")
    claims = manager.profile.get_all_claims()
    assert len(memories) == 1
    assert memories[0]["metadata"]["occurrence_count"] == 2
    assert len(open_threads) == 1
    assert open_threads[0]["metadata"]["occurrence_count"] == 2
    assert len(claims) == 1
    assert claims[0]["evidence_ids"] == ["turn_1", "turn_2"]


# =============================================================================
# FactExtractor
# =============================================================================


def test_fact_extract_result() -> None:
    r = FactExtractResult()
    assert r.is_empty() is True

    r = FactExtractResult(facts=["用户叫小明"], profile_fields={"名字": "小明"})
    assert r.is_empty() is False


def test_extractor_parse_valid_json() -> None:
    from mybuddy.memory.extractor import FactExtractor

    # 用 __new__ 绕过 __init__,只测 parse
    extractor = FactExtractor.__new__(FactExtractor)

    result = extractor._parse(
        '{"facts": ["用户叫小明"], "profile_fields": {"名字": "小明"}, '
        '"claims": [{"claim": "用户可能喜欢早起", "confidence": 0.6}]}'
    )
    assert len(result.facts) == 1
    assert result.facts[0] == "用户叫小明"
    assert result.profile_fields == {"名字": "小明"}
    assert len(result.claims) == 1
    assert result.claims[0]["confidence"] == 0.6


def test_extractor_parse_relationship_memories() -> None:
    from mybuddy.memory.extractor import FactExtractor

    extractor = FactExtractor.__new__(FactExtractor)
    result = extractor._parse(
        json.dumps(
            {
                "facts": [],
                "profile_fields": {},
                "claims": [],
                "relationship_memories": {
                    "shared_moment": [
                        {
                            "title": "那个没有催的晚上",
                            "content": "用户不想写代码时,小布陪用户把任务缩小到一个低压开头。",
                            "triggers": ["不想动", "拖延"],
                            "callback_style": "轻轻提起",
                        }
                    ],
                    "open_thread": [
                        {
                            "title": "报告开头",
                            "content": "用户明天要写报告开头。",
                            "contact_reason": "用户说明天别再拖",
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )
    )

    assert len(result.relationship_memories["shared_moment"]) == 1
    assert result.relationship_memories["shared_moment"][0]["title"] == "那个没有催的晚上"
    assert len(result.relationship_memories["open_thread"]) == 1
    assert result.is_empty() is False


def test_extractor_maps_legacy_anti_preference_only() -> None:
    from mybuddy.memory.extractor import FactExtractor

    extractor = FactExtractor.__new__(FactExtractor)
    result = extractor._parse(
        json.dumps(
            {
                "relationship_memories": {
                    "anti_preference": ["用户不喜欢空泛鼓励"],
                    "character_note": ["这类角色侧线索不进入最简记忆"],
                }
            },
            ensure_ascii=False,
        )
    )

    assert len(result.relationship_memories["preference"]) == 1
    assert result.relationship_memories["character_note"] == []


def test_extractor_parse_markdown_wrapped_json() -> None:
    from mybuddy.memory.extractor import FactExtractor

    extractor = FactExtractor.__new__(FactExtractor)
    result = extractor._parse(
        '```json\n{"facts": ["用户说他不爱吃香菜"], "profile_fields": {"香菜": "不喜欢"}, "claims": []}\n```'
    )
    assert len(result.facts) == 1
    assert result.profile_fields == {"香菜": "不喜欢"}


def test_extractor_parse_empty_input_returns_empty() -> None:
    from mybuddy.memory.extractor import FactExtractor

    extractor = FactExtractor.__new__(FactExtractor)
    result = extractor._parse("not json at all")
    assert result.is_empty() is True


# =============================================================================
# recall_memory 工具
# =============================================================================


@pytest.mark.asyncio
async def test_recall_memory_tool(tmp_path) -> None:
    from mybuddy.tools import set_context
    from mybuddy.tools.memory_tool import recall_memory, setup_memory_tool

    cfg = Config()
    engine = init_db(str(tmp_path / "recall.db"))
    set_context(engine=engine, config=cfg)

    chroma_dir = tmp_path / "chroma_recall"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir),
        collection_name="test_recall",
        embedding_fn=mock_embed,
    )
    ltm.add("用户小明喜欢在周日早上跑步", mem_type="memory")

    setup_memory_tool(ltm)

    from mybuddy.tools.registry import ToolRegistry as TR

    isolated = TR()
    isolated.register(recall_memory, name="recall_memory", description="搜索记忆")

    out = await isolated.execute("recall_memory", {"query": "跑步"})
    data = json.loads(out)
    assert len(data) >= 1
    assert any("跑步" in item["content"] for item in data)


@pytest.mark.asyncio
async def test_recall_memory_no_results(tmp_path) -> None:
    from mybuddy.tools import set_context
    from mybuddy.tools.memory_tool import recall_memory, setup_memory_tool

    cfg = Config()
    engine = init_db(str(tmp_path / "recall2.db"))
    set_context(engine=engine, config=cfg)

    chroma_dir = tmp_path / "chroma_recall2"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir),
        collection_name="test_recall2",
        embedding_fn=mock_embed,
    )

    setup_memory_tool(ltm)

    from mybuddy.tools.registry import ToolRegistry as TR

    isolated = TR()
    isolated.register(recall_memory, name="recall_memory", description="搜索记忆")

    out = await isolated.execute("recall_memory", {"query": "不存在的东西"})
    assert "没有找到" in out


def test_relevant_profile_fields_always_keeps_stable_identity_facts() -> None:
    from mybuddy.memory.manager import _relevant_profile_fields

    fields = {"昵称": "阿航", "咖啡偏好": "美式无糖"}
    # 话题无关的输入:身份类事实(昵称,即便不是规范键名)仍要保留,
    # 非身份偏好按词面相关性裁剪掉。
    selected = _relevant_profile_fields(fields, "今天天气不错", limit=2)
    assert selected.get("昵称") == "阿航"
    assert "咖啡偏好" not in selected

    # 输入与偏好相关时,偏好也应被带出。
    selected2 = _relevant_profile_fields(fields, "想喝咖啡了,有什么偏好", limit=2)
    assert "咖啡偏好" in selected2


def test_relevant_profile_fields_does_not_treat_topical_keys_as_stable() -> None:
    from mybuddy.memory.manager import _is_stable_profile_key, _relevant_profile_fields

    # 同义身份键(子串匹配)算稳定;整键身份词也算稳定。
    assert _is_stable_profile_key("出生日期") is True
    assert _is_stable_profile_key("过敏源") is True
    assert _is_stable_profile_key("工作") is True
    # 话题性字段(通用词 + 额外字)不能被误判成身份事实而无条件注入。
    for topical in ("工作进度", "当前工作流", "城市天气", "学校作业", "专业书单", "当前项目"):
        assert _is_stable_profile_key(topical) is False

    fields = {"当前项目": "周五项目报告", "工作进度": "完成 60%"}
    # 话题无关输入时,这些字段不应出现(回归:子串匹配曾把它们当稳定身份无界注入)。
    assert _relevant_profile_fields(fields, "晚上吃什么好呢", limit=2) == {}
