"""Dream Job 五件事测试。

LLM 用 ScriptedProvider,Chroma 用 MockEmbedding;验证的是执行后观察到的副作用
(记忆被删、命题置信度变化、pending_messages 入队)而非 LLM 响应本身。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from mybuddy._time import utcnow
from mybuddy.config import Config
from mybuddy.learning import DreamJob
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolSpec
from mybuddy.memory import LongTermMemory, UserProfile
from mybuddy.storage import ProfileClaim, init_db, list_undelivered, session_scope

from .test_memory import mock_embed


class ScriptedProvider(BaseLLMProvider):
    """按调用次序返回预设 text。"""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages)})
        text = self._texts.pop(0) if self._texts else "[]"
        return LLMResponse(text=text, finish_reason="stop")


@pytest.fixture
def dream_env(tmp_path):
    engine = init_db(str(tmp_path / "d.db"))
    cfg = Config()
    cfg.paths.db_file = str(tmp_path / "d.db")
    cfg.paths.chroma_dir = str(tmp_path / "chroma")

    (tmp_path / "chroma").mkdir()
    ltm = LongTermMemory(
        persist_dir=str(tmp_path / "chroma"),
        collection_name=f"dream_{tmp_path.name}",
        embedding_fn=mock_embed,
    )
    profile = UserProfile(engine, ltm)
    return engine, cfg, ltm, profile


def _make_claim_eligible(engine, claim_id: int, *, category: str = "boundary") -> None:
    with session_scope(engine) as s:
        row = s.query(ProfileClaim).filter_by(id=claim_id).one()
        row.status = "active"
        row.category = category
        row.evidence_count = 3
        row.evidence_days_json = json.dumps(["2026-06-02", "2026-06-03"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_dedup_merges_similar_memories(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    # 写两条几乎相同的记忆(mock embedding 对相同词 token hash 一致)
    ltm.add("用户爱喝手冲咖啡", mem_type="memory")
    ltm.add("用户爱喝手冲咖啡", mem_type="memory")  # 重复
    ltm.add("用户对海鲜过敏", mem_type="memory")     # 不同

    provider = ScriptedProvider(
        ["[]", "[]"]  # conflict, nudges 都返回空
    )
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.merged_memories >= 1
    # 剩下的 memory 条数 <= 2(去掉了重复)
    remaining = ltm.list_all(mem_type="memory")
    assert len(remaining) <= 2


@pytest.mark.asyncio
async def test_recompute_confidence_decays_old_claims(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    cid = profile.add_claim("用户周日情绪偏低", confidence=0.5, evidence_ids=["turn_1"])
    old_seen = utcnow() - timedelta(days=30)
    with session_scope(engine) as s:
        row = s.query(ProfileClaim).filter_by(id=cid).one()
        row.last_seen_at = old_seen
        row.evidence_days_json = json.dumps([old_seen.date().isoformat()], ensure_ascii=False)

    provider = ScriptedProvider([])
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.confidence_updates >= 1
    claims_after = profile.get_all_claims()
    match = next((c for c in claims_after if c["sql_id"] == cid), None)
    assert match is not None
    assert match["confidence"] < 0.5
    assert match["last_seen_at"].startswith(old_seen.date().isoformat())


@pytest.mark.asyncio
async def test_recompute_confidence_boosts_recent_evidence(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    cid = profile.add_claim("用户周日情绪偏低", confidence=0.5, evidence_ids=["turn_1"])

    provider = ScriptedProvider([])
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.confidence_updates >= 1
    match = next((c for c in profile.get_all_claims() if c["sql_id"] == cid), None)
    assert match is not None
    assert match["confidence"] > 0.5


@pytest.mark.asyncio
async def test_insights_are_not_generated_by_default(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    # 写两条 claim(>=2 让 _resolve_conflicts 能真的调 LLM,占据第 1 个脚本响应)
    profile.add_claim("用户偏好简洁沟通", confidence=0.6)
    profile.add_claim("用户对海鲜过敏", confidence=0.8)

    # LLM 依次返回:conflict=空、nudges=空。nightly job 不再主动制造洞察命题。
    provider = ScriptedProvider(
        [
            "[]",
            "[]",
        ]
    )

    before = len(profile.get_all_claims())
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.insights_added == 0
    after = len(profile.get_all_claims())
    assert after == before


@pytest.mark.asyncio
async def test_insights_generation_is_skipped_even_with_many_candidates(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    profile.add_claim("用户偏好简洁沟通", confidence=0.6)
    profile.add_claim("用户对海鲜过敏", confidence=0.8)

    items = [
        {"claim": f"用户观察 {i}", "confidence": 0.4}
        for i in range(5)
    ]
    provider = ScriptedProvider(["[]", json.dumps(items, ensure_ascii=False)])

    before = len(profile.get_all_claims())
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.insights_added == 0
    assert len(profile.get_all_claims()) == before


@pytest.mark.asyncio
async def test_stable_claim_promoted_to_long_term_memory(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    cid = profile.add_claim(
        "用户不喜欢空泛鼓励",
        confidence=0.8,
        evidence_ids=["turn_1", "turn_2", "turn_3"],
    )
    _make_claim_eligible(engine, cid, category="boundary")

    provider = ScriptedProvider([])
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.promoted_claims == 1
    claim = next(c for c in profile.get_all_claims() if c["sql_id"] == cid)
    assert claim["status"] == "promoted"
    assert claim["promoted_memory_id"]
    assert profile.get_all_claims(include_hidden=False) == []

    promoted = ltm.list_all(mem_type="preference")
    assert len(promoted) == 1
    assert promoted[0]["metadata"]["promoted_from_claim_id"] == cid


@pytest.mark.asyncio
async def test_conflicted_claim_is_not_promoted(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    cid1 = profile.add_claim("用户喜欢直接建议", confidence=0.8, evidence_ids=["a", "b", "c"])
    cid2 = profile.add_claim("用户不喜欢别人立刻给建议", confidence=0.8, evidence_ids=["d", "e", "f"])
    _make_claim_eligible(engine, cid1, category="preference")
    _make_claim_eligible(engine, cid2, category="boundary")

    provider = ScriptedProvider([json.dumps([{"a": cid1, "b": cid2}], ensure_ascii=False)])
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.conflicts_resolved == 1
    assert report.promoted_claims == 0
    claims = profile.get_all_claims()
    assert all(c["status"] != "promoted" for c in claims)
    assert any(c["conflict_ids"] for c in claims)


@pytest.mark.asyncio
async def test_nudges_enqueued(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    ltm.add(
        "用户说明天要开始写报告开头,但担心自己又拖着不动。",
        mem_type="open_thread",
        extra_meta={
            "title": "报告开头还没写",
            "contact_reason": "用户昨天说“明天别再拖了”",
            "triggers": ["报告", "拖延"],
        },
    )

    # 为让 conflict 步调 LLM,需要 claims >= 2
    profile.add_claim("用户偏好简洁沟通", confidence=0.6)
    profile.add_claim("用户对海鲜过敏", confidence=0.8)
    # conflict=空、nudges 2 条
    provider = ScriptedProvider(
        [
            "[]",
            '["最近在上海还习惯吗?", "说起养猫的事,有什么进展没~"]',
        ]
    )

    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.nudges_enqueued == 2
    pending = list_undelivered(engine)
    assert len(pending) == 2
    assert all(p["source"] == "nudge" for p in pending)


@pytest.mark.asyncio
async def test_character_dynamic_enqueued(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    ltm.add(
        "用户接受了把任务缩小到一个最小动作的低压陪伴方式。",
        mem_type="shared_moment",
        extra_meta={"title": "低压启动"},
    )

    provider = ScriptedProvider(['["我把昨晚那张便签折了一下,放在桌角。今天先拿最小那一步。"]'])

    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.dynamics_enqueued == 1
    pending = list_undelivered(engine)
    assert len(pending) == 1
    assert pending[0]["source"] == "dynamic"


@pytest.mark.asyncio
async def test_run_collects_errors_without_crashing(dream_env) -> None:
    """某一步异常不应导致其他步骤失败。"""
    engine, cfg, ltm, profile = dream_env

    # 补齐前置数据,让 LLM 相关步骤至少会被调用到
    profile.add_claim("a", confidence=0.6)
    profile.add_claim("b", confidence=0.6)
    ltm.add("some memory", mem_type="memory")
    ltm.add("some open thread", mem_type="open_thread", extra_meta={"contact_reason": "test"})
    ltm.add("some shared moment", mem_type="shared_moment")
    class BrokenProvider(BaseLLMProvider):
        async def generate(self, messages, tools=None, **kwargs):  # noqa: ANN001
            raise RuntimeError("LLM 挂了")

    job = DreamJob(
        engine=engine,
        config=cfg,
        provider=BrokenProvider(),
        ltm=ltm,
        profile=profile,
    )
    report = await job.run()

    # LLM 相关步骤(conflict / nudges / dynamics)应该进 errors,但不崩
    assert len(report.errors) >= 3
    assert all("LLM" in e or "RuntimeError" in e for e in report.errors)
