"""M3 记忆系统测试:短期记忆、长期记忆、用户画像、事实抽取、记忆工具。

MockEmbedding 仅保留为旧构造参数兼容测试;当前 LongTermMemory 使用结构化文本检索。
"""

from __future__ import annotations

import hashlib
import json
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
from mybuddy.storage import init_db

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
    ltm.add("偏好:用户可能喜欢早起", mem_type="preference")

    hits_mem = ltm.search("用户名字", top_k=5, mem_type="memory")
    assert all(h["metadata"].get("type") == "memory" for h in hits_mem)

    hits_pref = ltm.search("早起习惯", top_k=5, mem_type="preference")
    assert all(h["metadata"].get("type") == "preference" for h in hits_pref)


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
    # 用底层 ltm.add 直接造一张已过期的卡(add_or_merge 会把过去的 expires_at 丢弃 —— R4);
    # 这里要测的是 refresh 把已到期的 active 转 stale。
    ltm.add(
        "用户昨天要处理报告开头",
        mem_type="open_thread",
        extra_meta={"status": "active", "expires_at": "2000-01-01T00:00:00"},
    )

    assert governance.refresh_open_thread_lifecycle() == 1
    item = ltm.list_all(mem_type="open_thread")[0]
    assert item["metadata"]["status"] == "stale"
    assert item["metadata"]["stale_at"]


def test_governance_resolve_open_thread(ltm) -> None:
    governance = MemoryGovernance(ltm)
    uid = governance.add_or_merge("用户要写周五报告开头", mem_type="open_thread").memory_id

    assert governance.resolve_open_thread(uid, reason="用户说搞定了") is True
    item = ltm.list_all(mem_type="open_thread")[0]
    assert item["metadata"]["status"] == "resolved"
    assert item["metadata"]["resolved_reason"] == "用户说搞定了"
    # resolved 非 active → 不再被检索召回
    assert ltm.search("报告", mem_type="open_thread") == []
    # 不存在的 id 不误操作
    assert governance.resolve_open_thread("nonexistent") is False


def test_governance_snooze_then_auto_wake(ltm) -> None:
    governance = MemoryGovernance(ltm)
    uid = governance.add_or_merge("用户下周要交材料", mem_type="open_thread").memory_id

    # 压到未来:进入 snoozed,不被召回,也不被 refresh 唤醒
    governance.snooze_open_thread(uid, "2099-01-01T00:00:00")
    assert ltm.list_all(mem_type="open_thread")[0]["metadata"]["status"] == "snoozed"
    assert ltm.search("材料", mem_type="open_thread") == []
    governance.refresh_open_thread_lifecycle()
    assert ltm.list_all(mem_type="open_thread")[0]["metadata"]["status"] == "snoozed"

    # 压到过去 → refresh 自动唤醒为 active
    governance.snooze_open_thread(uid, "2000-01-01T00:00:00")
    assert governance.refresh_open_thread_lifecycle() == 1
    assert ltm.list_all(mem_type="open_thread")[0]["metadata"]["status"] == "active"


def test_governance_deletes_long_stale_open_thread(ltm) -> None:
    governance = MemoryGovernance(ltm)
    governance.add_or_merge(
        "一个早就 stale 的旧话题",
        mem_type="open_thread",
        extra_meta={"status": "stale", "stale_at": "2000-01-01T00:00:00"},
    )
    assert len(ltm.list_all(mem_type="open_thread")) == 1
    assert governance.refresh_open_thread_lifecycle() == 1  # 超 TTL → 删除
    assert ltm.list_all(mem_type="open_thread") == []


def test_refresh_handles_timezone_aware_expires_at(ltm) -> None:
    """回归(评审 C1):带时区的 expires_at 不应让 refresh 抛 TypeError 打断对话。

    用底层 ltm.add 注入未归一的 aware 字符串(add_or_merge 会在写入时归一/丢弃),模拟历史
    遗留卡直接进到 refresh 的比较路径。
    """
    governance = MemoryGovernance(ltm)
    ltm.add(
        "用户周五要交报告",
        mem_type="open_thread",
        uid="ot_tz",
        extra_meta={"title": "报告", "status": "active", "expires_at": "2000-01-01T00:00:00+08:00"},
    )
    # 不抛 + 已过期 → stale(aware 被规整成 naive-UTC 后能正常比较)
    assert governance.refresh_open_thread_lifecycle() == 1
    assert ltm.list_all(mem_type="open_thread")[0]["metadata"]["status"] == "stale"


def test_open_thread_expires_at_normalized(ltm) -> None:
    """回归(评审 Y3/R2):可解析的显式截止归一为 ISO;解析不了/缺失**不写兜底默认**
    (否则会盖掉用户后补的真实截止),永生由 refresh 按 last_seen+TTL 兜底(见下条测试)。"""
    from mybuddy.memory.governance import _parse_iso

    governance = MemoryGovernance(ltm)
    governance.add_or_merge(
        "用户明天要做的事", mem_type="open_thread", uid="ot_nl",
        extra_meta={"expires_at": "明天下午3点"},
    )
    governance.add_or_merge("用户在准备一件没说截止的事", mem_type="open_thread", uid="ot_none")
    by_id = {i["id"]: i["metadata"] for i in ltm.list_all(mem_type="open_thread")}
    # 可解析 → 归一为 ISO
    assert _parse_iso(by_id["ot_nl"].get("expires_at")) is not None
    assert by_id["ot_nl"]["expires_at"] != "明天下午3点"
    # 缺失 → 不写兜底默认值
    assert "expires_at" not in by_id["ot_none"]


def test_open_thread_without_expires_still_bounded(ltm) -> None:
    """回归(评审 R8):无显式 expires_at 的话题不永生——refresh 按 last_seen_at + TTL 兜底回收。"""
    ltm.add(
        "一个很久没提、也没说截止的旧话题",
        mem_type="open_thread",
        uid="ot_old",
        extra_meta={
            "status": "active",
            "last_seen_at": "2000-01-01T00:00:00",
            "created_at": "2000-01-01T00:00:00",
        },
    )
    governance = MemoryGovernance(ltm)
    assert governance.refresh_open_thread_lifecycle() == 1  # last_seen+TTL 已过 → stale
    assert ltm.list_all(mem_type="open_thread")[0]["metadata"]["status"] == "stale"


def test_stale_open_thread_revives_when_rementioned(ltm) -> None:
    """回归(评审 R1):自动 stale 的话题被用户再次提起 → 复活为 active 且不会立刻又 stale。"""
    governance = MemoryGovernance(ltm)
    # 直接造一张 stale 卡(add_or_merge 不再存过去的 expires_at)
    ltm.add(
        "用户周五要交报告", mem_type="open_thread", uid="ot_s",
        extra_meta={"status": "stale", "stale_at": "2000-01-01T00:00:00",
                    "expires_at": "2000-01-01T00:00:00"},
    )
    assert ltm.list_all(mem_type="open_thread")[0]["metadata"]["status"] == "stale"

    governance.add_or_merge("用户周五要交报告", mem_type="open_thread")  # 再次提到 → 复活
    card = ltm.list_all(mem_type="open_thread")[0]
    assert card["id"] == "ot_s"  # 合并进同一张卡
    assert card["metadata"]["status"] == "active" and not card["metadata"].get("stale_at")
    governance.refresh_open_thread_lifecycle()  # 过去截止已换成未来兜底
    assert ltm.list_all(mem_type="open_thread")[0]["metadata"]["status"] == "active"

    # snoozed 不被再次提到唤醒(C3 仍成立)
    sid = governance.add_or_merge("用户下月计划", mem_type="open_thread").memory_id
    governance.snooze_open_thread(sid, "2099-01-01T00:00:00")
    governance.add_or_merge("用户下月计划", mem_type="open_thread")
    assert next(
        i["metadata"]["status"] for i in ltm.list_all(mem_type="open_thread") if i["id"] == sid
    ) == "snoozed"


def test_later_real_deadline_replaces_missing(ltm) -> None:
    """回归(评审 R2):先建无截止话题、后补真实"明天"截止 → 采用真实截止,不被旧值盖掉。"""
    from mybuddy.memory.governance import _parse_iso

    governance = MemoryGovernance(ltm)
    governance.add_or_merge("用户在准备项目汇报", mem_type="open_thread", uid="ot")
    assert "expires_at" not in ltm.list_all(mem_type="open_thread")[0]["metadata"]
    governance.add_or_merge(
        "用户在准备项目汇报", mem_type="open_thread", extra_meta={"expires_at": "明天下午3点"}
    )
    assert _parse_iso(ltm.list_all(mem_type="open_thread")[0]["metadata"].get("expires_at")) is not None


def test_choose_content_preserves_newer_update() -> None:
    """回归(评审 Y4/[7]):合并不再"谁长留谁",更新但更短的关键信息不被丢。"""
    from mybuddy.memory.governance import _choose_content

    # entity 的更新("最近生病住院了"更短)与旧描述都保留
    merged = _choose_content("是一只很黏人的英短,喜欢晒太阳", "最近生病住院了")
    assert "英短" in merged and "生病住院" in merged
    # 新是旧的子串 → 留旧(无增量)
    assert _choose_content("用户喜欢喝美式咖啡", "美式咖啡") == "用户喜欢喝美式咖啡"
    # 新是旧的超集 → 留新
    assert _choose_content("咖啡", "用户喜欢喝咖啡") == "用户喜欢喝咖啡"


def test_merge_does_not_revive_snoozed_thread(ltm) -> None:
    """回归(评审 C3):再次提到一个 snoozed 话题(同 memory_key)不该被合并复活成 active。"""
    governance = MemoryGovernance(ltm)
    uid = governance.add_or_merge("用户下周要交材料", mem_type="open_thread").memory_id
    governance.snooze_open_thread(uid, "2099-01-01T00:00:00")
    governance.add_or_merge("用户下周要交材料", mem_type="open_thread")  # 再次提到 → merge
    assert ltm.list_all(mem_type="open_thread")[0]["metadata"]["status"] == "snoozed"


def test_build_context_do_refresh_flag(tmp_path) -> None:
    """回归(评审 Y2/[3]):do_refresh=False(供 to_thread 只读)跳过生命周期刷新,
    刷新改由 agent 在事件循环上先做,避免与后台 run_extract 跨线程写竞态。"""
    engine = init_db(str(tmp_path / "refresh.db"))
    cfg = Config()
    chroma_dir = tmp_path / "refresh_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir), collection_name="refresh", embedding_fn=mock_embed
    )
    ltm.add(
        "旧的未完成话题",
        mem_type="open_thread",
        uid="ot_exp",
        extra_meta={"title": "x", "expires_at": "2000-01-01T00:00:00"},
    )
    mm = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=DummyProvider())

    # do_refresh=False:不写盘,过期卡仍 active(纯只读,可安全丢进 to_thread)
    mm.build_context_section("随便说点", do_refresh=False)
    assert ltm.list_all(mem_type="open_thread")[0]["metadata"].get("status", "active") == "active"

    # 默认 / 显式 refresh_lifecycle:过期 → stale
    mm.refresh_lifecycle()
    assert ltm.list_all(mem_type="open_thread")[0]["metadata"]["status"] == "stale"


# =============================================================================
# UserProfile
# =============================================================================


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
    text = manager.build_context_section("我又拖延了,报告开头还是没写")

    assert text.index("## 未完成话题") < text.index("## 关于用户")
    assert "那个没有催的晚上" in text
    assert "不喜欢被强行打鸡血" in text
    assert "## 关系线索" not in text


def test_memory_manager_injects_user_notes(tmp_path) -> None:
    """用户主动存的笔记必须能在聊天里被自然想起。

    回归:build_context_section 之前不检索 mem_type="note",
    导致"创建笔记 地点在上海 → 聊天立刻问地点"答不上来。
    """
    engine = init_db(str(tmp_path / "manager_notes.db"))
    cfg = Config()
    chroma_dir = tmp_path / "manager_notes_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir),
        collection_name="manager_notes",
        embedding_fn=mock_embed,
    )
    # 模拟 write_note / create_note 落档(mem_type="note", importance=0.85)
    ltm.add(
        "地点在上海",
        mem_type="note",
        uid="note_1",
        extra_meta={"sql_id": 1, "title": "地点", "source": "user_note", "importance": 0.85},
    )
    manager = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=DummyProvider())

    # 相关提问:笔记进聊天上下文
    text = manager.build_context_section("我的地点在哪来着")
    assert "你帮我记下的笔记" in text
    assert "地点在上海" in text

    # 无关提问:不误注入
    other = manager.build_context_section("今天晚饭想吃什么")
    assert "地点在上海" not in other


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
    assert len(memories) == 1
    assert memories[0]["metadata"]["occurrence_count"] == 2
    assert len(open_threads) == 1
    assert open_threads[0]["metadata"]["occurrence_count"] == 2


@pytest.mark.asyncio
async def test_memory_manager_supersedes_corrected_fact(tmp_path) -> None:
    """用户显式改口 → 匹配的旧卡标 superseded、不再被召回;新事实照常写入。"""
    engine = init_db(str(tmp_path / "supersede.db"))
    cfg = Config()
    cfg.memory.extract_after_turns = 1
    chroma_dir = tmp_path / "supersede_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir), collection_name="supersede", embedding_fn=mock_embed
    )
    ltm.add("用户喜欢喝美式咖啡", mem_type="profile", uid="old_coffee")

    payload = json.dumps(
        {
            "facts": ["用户现在不喝咖啡了,改喝茶"],
            "corrections": [{"old": "用户喜欢喝美式咖啡", "reason": "用户说现在不喝咖啡了"}],
        },
        ensure_ascii=False,
    )
    manager = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=StaticProvider(payload))
    manager.record_turn("我现在不喝咖啡了,改喝茶", "好的", turn_id="t1")
    assert await manager.maybe_extract() is True

    old = next(i for i in ltm.list_all(mem_type="profile") if i["id"] == "old_coffee")
    assert old["metadata"]["status"] == "superseded"
    assert old["metadata"].get("superseded_reason")
    assert all(h["id"] != "old_coffee" for h in ltm.search("咖啡", mem_type="profile"))
    assert any("茶" in i["content"] for i in ltm.list_all(mem_type="profile"))


@pytest.mark.asyncio
async def test_memory_manager_correction_without_strong_match_is_noop(tmp_path) -> None:
    """纠正找不到强匹配旧卡时不误废、不崩(corrections 非空仍走流程)。"""
    engine = init_db(str(tmp_path / "supersede2.db"))
    cfg = Config()
    cfg.memory.extract_after_turns = 1
    chroma_dir = tmp_path / "supersede2_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir), collection_name="supersede2", embedding_fn=mock_embed
    )
    ltm.add("用户养了一只叫煤球的猫", mem_type="profile", uid="cat")

    payload = json.dumps(
        {"corrections": [{"old": "用户之前提过的某个工作职位", "reason": "改口"}]},
        ensure_ascii=False,
    )
    manager = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=StaticProvider(payload))
    manager.record_turn("随便聊聊", "嗯", turn_id="t1")
    assert await manager.maybe_extract() is True  # corrections 非空 → 不算 empty

    cat = next(i for i in ltm.list_all(mem_type="profile") if i["id"] == "cat")
    assert cat["metadata"].get("status", "active") == "active"  # 不相关旧卡没被动


@pytest.mark.asyncio
async def test_correction_does_not_supersede_same_batch_new_fact(tmp_path) -> None:
    """回归(评审 C2):无历史旧卡时,corrections 不该把本批刚写入的'改正后正确卡'自己作废。"""
    engine = init_db(str(tmp_path / "supersede3.db"))
    cfg = Config()
    chroma_dir = tmp_path / "supersede3_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir), collection_name="supersede3", embedding_fn=mock_embed
    )
    # 没有预先存在的"喜欢咖啡"旧卡;新事实与 correction.old 词面高度相似
    payload = json.dumps(
        {
            "facts": ["用户现在不喝美式咖啡了,改喝拿铁"],
            "corrections": [{"old": "用户喜欢喝美式咖啡", "reason": "用户改口"}],
        },
        ensure_ascii=False,
    )
    manager = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=StaticProvider(payload))
    assert await manager.run_extract(["USER: 我改喝拿铁了", "AI: 好"], ["t1"]) is True

    cards = ltm.list_all(mem_type="profile")
    assert cards, "新事实应被写入"
    assert all(c["metadata"].get("status", "active") == "active" for c in cards)  # 没被自己作废
    assert any("拿铁" in c["content"] for c in cards)


@pytest.mark.asyncio
async def test_concurrent_run_extract_merges_without_lost_update(tmp_path) -> None:
    """同一用户并发抽取写同一事实:合并成一张、occurrence_count 正确,且无残留临时文件。"""
    import asyncio as _asyncio

    engine = init_db(str(tmp_path / "concurrent.db"))
    cfg = Config()
    chroma_dir = tmp_path / "concurrent_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir), collection_name="concurrent", embedding_fn=mock_embed
    )
    payload = json.dumps({"facts": ["用户喜欢喝美式咖啡"]}, ensure_ascii=False)
    manager = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=StaticProvider(payload))

    await _asyncio.gather(
        manager.run_extract(["USER: 我爱美式", "AI: 好"], ["t1"]),
        manager.run_extract(["USER: 我爱美式", "AI: 好"], ["t2"]),
    )

    cards = ltm.list_all(mem_type="profile")
    assert len(cards) == 1  # 合并成一张,而非两张重复
    assert cards[0]["metadata"]["occurrence_count"] == 2
    # 原子写不留临时文件:archive 目录只剩 .md
    leftovers = [p.name for p in (chroma_dir / "archive").iterdir() if not p.name.endswith(".md")]
    assert leftovers == []


@pytest.mark.asyncio
async def test_memory_manager_extracts_and_injects_entities(tmp_path) -> None:
    """抽取的人/宠物落成 entity 卡,同名合并不堆重复,且能注入聊天上下文。"""
    engine = init_db(str(tmp_path / "entity.db"))
    cfg = Config()
    chroma_dir = tmp_path / "entity_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir), collection_name="entity", embedding_fn=mock_embed
    )

    p1 = json.dumps(
        {
            "entities": [
                {"name": "煤球", "relation": "猫", "note": "用户养的橘猫,三岁,很黏人"},
                {"name": "小敏", "relation": "妹妹", "note": "用户的妹妹,在读高三"},
            ]
        },
        ensure_ascii=False,
    )
    m1 = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=StaticProvider(p1))
    assert await m1.run_extract(["USER: 煤球和小敏的事", "AI: 嗯"], ["t1"]) is True
    assert {e["metadata"].get("entity_name") for e in ltm.list_all(mem_type="entity")} == {
        "煤球",
        "小敏",
    }

    # 同名实体再次提到 → 合并(occurrence_count++),不堆第二张
    p2 = json.dumps(
        {"entities": [{"name": "煤球", "relation": "猫", "note": "煤球最近胖了不少"}]},
        ensure_ascii=False,
    )
    m2 = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=StaticProvider(p2))
    assert await m2.run_extract(["USER: 煤球胖了", "AI: 哈"], ["t2"]) is True
    cats = [e for e in ltm.list_all(mem_type="entity") if e["metadata"].get("entity_name") == "煤球"]
    assert len(cats) == 1
    assert cats[0]["metadata"]["occurrence_count"] == 2

    # 注入聊天上下文
    text = m1.build_context_section("煤球最近怎么样")
    assert "你身边的人和宠物" in text
    assert "煤球" in text


@pytest.mark.asyncio
async def test_entity_same_name_different_relation_not_merged(tmp_path) -> None:
    """同名但不同关系(人 vs 宠物)不该被合并成一张卡、属性混淆。"""
    engine = init_db(str(tmp_path / "ent_collide.db"))
    cfg = Config()
    chroma_dir = tmp_path / "ent_collide_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir), collection_name="ec", embedding_fn=mock_embed
    )
    payload = json.dumps(
        {
            "entities": [
                {"name": "小明", "relation": "朋友", "note": "大学同学,在北京"},
                {"name": "小明", "relation": "猫", "note": "用户养的布偶猫"},
            ]
        },
        ensure_ascii=False,
    )
    m = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=StaticProvider(payload))
    assert await m.run_extract(["USER: 小明的事", "AI: 嗯"], ["t1"]) is True

    ents = ltm.list_all(mem_type="entity")
    assert len(ents) == 2  # 同名不同关系 → 两张卡
    assert {e["metadata"].get("relation") for e in ents} == {"朋友", "猫"}


def _golden_recall_store(tmp_path) -> MemoryManager:
    """搭一个贴近真实的小记忆库,覆盖 note/偏好/避雷/entity/open_thread 各类型。"""
    engine = init_db(str(tmp_path / "golden.db"))
    cfg = Config()
    chroma_dir = tmp_path / "golden_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir), collection_name="golden", embedding_fn=mock_embed
    )
    ltm.add(
        "地点在上海",
        mem_type="note",
        uid="g_note",
        extra_meta={"title": "地点", "source": "user_note", "importance": 0.85},
    )
    ltm.add("用户喜欢喝美式咖啡", mem_type="preference", extra_meta={"title": "咖啡偏好"})
    ltm.add("用户不喜欢被打鸡血式鼓励", mem_type="preference", extra_meta={"title": "鼓励方式"})
    ltm.add(
        "煤球(猫):用户养的橘猫,很黏人",
        mem_type="entity",
        extra_meta={"entity_name": "煤球", "relation": "猫", "keywords": ["煤球", "猫"]},
    )
    ltm.add(
        "用户在准备周五的项目汇报",
        mem_type="open_thread",
        extra_meta={"title": "项目汇报", "contact_reason": "周五要交"},
    )
    return MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=DummyProvider())


def test_recall_golden_set(tmp_path) -> None:
    """召回金标准回归集:锁死 T1/T2 的召回行为,后续调阈值/权重一旦破坏立刻报警。

    每条 (query, 必须召回, 不该召回)。覆盖:note 按词召回、偏好正价、避雷负价标注、
    entity 按名召回、open_thread 召回、无关 query 不乱注入话题卡。
    """
    mm = _golden_recall_store(tmp_path)
    cases = [
        ("我的地点是哪", ["上海"], []),
        ("想喝美式咖啡", ["美式咖啡", "【偏好】"], []),
        ("给我加油鼓励一下", ["打鸡血", "【避开】"], []),
        ("煤球今天乖不乖", ["煤球"], []),
        ("项目汇报准备好了吗", ["汇报"], []),
        # 无关 query:话题性卡(note/正向偏好/entity 无 recency 兜底)不该乱入;
        # open_thread 与"避雷"会按设计经 recency 兜底出现(主动回响 + 安全栏),不在此断言。
        ("今天股市大涨", [], ["上海", "美式咖啡", "煤球"]),
    ]
    for query, must, must_not in cases:
        text = mm.build_context_section(query)
        for m in must:
            assert m in text, f"{query!r} 应召回 {m!r},实际:\n{text}"
        for n in must_not:
            assert n not in text, f"{query!r} 不该召回 {n!r},实际:\n{text}"


def test_preference_valence_prefers_structured_polarity() -> None:
    """回归(评审 #9/#10):极性优先用结构化 polarity,而非脆弱的正文正则。"""
    from mybuddy.memory.manager import _preference_valence

    # 正向诉求却含"不要" —— 纯正则会误判成避开 → 模型反着做;polarity=like 纠正为偏好
    assert (
        _preference_valence({"metadata": {"polarity": "like"}, "content": "希望被鼓励,不要打鸡血"})
        == "偏好"
    )
    # 避雷但不含那 6 个关键词 —— 正则漏判(漏进安全栏);polarity=avoid 纠正为避开
    assert (
        _preference_valence({"metadata": {"polarity": "avoid"}, "content": "被催进度会焦虑,请慢慢来"})
        == "避开"
    )
    # 无 polarity(旧卡/直接写入)退回正则兜底
    assert _preference_valence({"metadata": {}, "content": "用户不喜欢香菜"}) == "避开"
    assert _preference_valence({"metadata": {}, "content": "用户喜欢喝咖啡"}) == "偏好"
    # 回归(评审 R6):LLM 没严格输出 like/avoid 时,中文/近义词也能识别,不跌回脆弱正则
    assert _preference_valence({"metadata": {"polarity": "讨厌"}, "content": "随便"}) == "避开"
    assert _preference_valence({"metadata": {"polarity": "dislike"}, "content": "随便"}) == "避开"
    assert _preference_valence({"metadata": {"polarity": "想要"}, "content": "随便"}) == "偏好"


def test_normalize_expires_at_timezone_and_past() -> None:
    """回归(评审 R4):中文相对时间按本地壁钟解析再转 UTC(不偏一个时区);已过去的截止丢弃。"""
    from datetime import UTC

    from mybuddy._time import utcnow
    from mybuddy.memory.governance import _normalize_expires_at, _parse_iso

    now_iso = utcnow().isoformat(timespec="seconds")
    iso = _normalize_expires_at("明天下午3点", now_iso)
    assert iso is not None
    # 存的是 UTC;转回本地应为"明天 15:00"(机器本地时区,tz 无关断言)
    assert _parse_iso(iso).replace(tzinfo=UTC).astimezone().hour == 15
    # 已过去的显式截止 → 丢弃(不让话题刚建就 stale),解析不了的也丢弃
    assert _normalize_expires_at("2000-01-01T00:00:00", now_iso) is None
    assert _normalize_expires_at("下周五", now_iso) is None


def test_choose_content_dedups_entity_prefix_and_keeps_recent() -> None:
    """回归(评审 R5):合并不重复拼 entity 前缀;超长保留最新片段而非整段丢弃。"""
    from mybuddy.memory.governance import _choose_content

    merged = _choose_content("煤球(猫):是只英短", "煤球(猫):最近生病住院了")
    assert merged == "煤球(猫):是只英短;最近生病住院了"
    assert merged.count("煤球(猫):") == 1  # 前缀不重复
    # 多段累积超长:保留最新片段,不整段丢光
    long_combined = _choose_content("a:" + "旧" * 100, "a:中间")
    out = _choose_content(long_combined, "a:最新")
    assert "最新" in out and len(out) <= 240


def test_preference_merge_keeps_polarity_consistent_with_content(ltm) -> None:
    """回归(评审 R3):合并保留旧正文时 polarity 也跟旧的,避免'标签 like、正文不喜欢'矛盾卡。"""
    governance = MemoryGovernance(ltm)
    governance.add_or_merge(
        "用户不喜欢被打鸡血式鼓励", mem_type="preference", extra_meta={"polarity": "avoid"}
    )
    # 同正文(必合并),但新卡把 polarity 误标成 like
    governance.add_or_merge(
        "用户不喜欢被打鸡血式鼓励", mem_type="preference", extra_meta={"polarity": "like"}
    )
    cards = ltm.list_all(mem_type="preference")
    assert len(cards) == 1  # 合并成一张
    assert cards[0]["metadata"].get("polarity") == "avoid"  # 与保留的否定正文一致,未被 like 盖掉


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
        '{"facts": ["用户叫小明"], "profile_fields": {"名字": "小明"}}'
    )
    assert len(result.facts) == 1
    assert result.facts[0] == "用户叫小明"
    assert result.profile_fields == {"名字": "小明"}


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


def test_extractor_parse_prose_wrapped_json() -> None:
    """小模型常加客套话/解释:括号配平要能从中捞出 JSON,而非整批丢弃。"""
    from mybuddy.memory.extractor import FactExtractor

    extractor = FactExtractor.__new__(FactExtractor)
    # 前有客套话、后有解释、无 markdown 围栏
    result = extractor._parse(
        '好的,以下是我提取的结果:\n'
        '{"facts": ["用户在上海工作"], "profile_fields": {"城市": "上海"}}\n'
        '希望对你有帮助!'
    )
    assert result.facts == ["用户在上海工作"]
    assert result.profile_fields == {"城市": "上海"}


def test_extractor_balanced_object_skips_braces_in_strings() -> None:
    """括号配平要正确跳过字符串字面量里的花括号,不被提前截断。"""
    from mybuddy.memory.extractor import _first_balanced_object

    src = 'noise {"facts": ["这条含 } 和 { 符号"], "profile_fields": {}} trailing'
    obj = _first_balanced_object(src)
    assert obj is not None
    import json

    data = json.loads(obj)
    assert data["facts"] == ["这条含 } 和 { 符号"]


def test_extractor_parse_empty_input_returns_empty() -> None:
    from mybuddy.memory.extractor import FactExtractor

    extractor = FactExtractor.__new__(FactExtractor)
    result = extractor._parse("not json at all")
    assert result.is_empty() is True


def test_extractor_normalizes_nonstring_field_values() -> None:
    """回归(评审 #14):模型把字段值返成 list / 把 fact 返成 dict 时,
    不应写成 Python repr 垃圾再注入提示词。"""
    from mybuddy.memory.extractor import FactExtractor

    extractor = FactExtractor.__new__(FactExtractor)
    result = extractor._parse(
        '{"profile_fields": {"过敏": ["花生", "海鲜"]}, "facts": [{"text": "用户叫小明"}]}'
    )
    assert result.profile_fields == {"过敏": "花生、海鲜"}  # list → 顿号拼接,非 "['花生',...]"
    assert result.facts == ["用户叫小明"]  # dict → 取 text 键,非 "{'text': ...}"


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


def test_tokenize_drops_stopwords_but_keeps_content() -> None:
    from mybuddy.memory.long_term import _tokenize

    tokens = set(_tokenize("我今天有点累，不想动"))
    # 功能词/语气助词被过滤
    assert "我" not in tokens
    assert "有点" not in tokens
    # 内容字/词保留(召回不受影响)
    assert "累" in tokens
    assert "今天" in tokens
    assert "想" in tokens


def test_rehydrate_short_term_from_messages(tmp_path) -> None:
    from mybuddy.storage import append_message

    engine = init_db(str(tmp_path / "rehydrate.db"))
    cfg = Config()
    cfg.memory.short_term_size = 4
    chroma_dir = tmp_path / "rehydrate_chroma"
    chroma_dir.mkdir()
    ltm = LongTermMemory(persist_dir=str(chroma_dir), embedding_fn=mock_embed)
    sid = "user-1"

    append_message(engine, session_id=sid, role="user", content="第一句")
    append_message(engine, session_id=sid, role="assistant", content="回应一")
    append_message(engine, session_id=sid, role="tool", content="工具结果")  # 跳过
    append_message(engine, session_id=sid, role="assistant", content="")  # 空跳过
    append_message(engine, session_id=sid, role="user", content="第二句")
    append_message(engine, session_id="other-session", role="user", content="别人的")  # 别的 session

    mm = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=DummyProvider(), session_id=sid)
    restored = mm.rehydrate_short_term()

    assert restored == 3  # tool + 空内容被跳过,别的 session 不计
    contents = [m.content for m in mm.get_recent_messages()]
    assert contents == ["第一句", "回应一", "第二句"]
    # 已有内容时不重复填充
    assert mm.rehydrate_short_term() == 0
