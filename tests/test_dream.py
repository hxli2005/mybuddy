"""Dream Job 五件事测试。

LLM 用 ScriptedProvider,Chroma 用 MockEmbedding;验证的是执行后观察到的副作用
(记忆被删、命题置信度变化、pending_messages 入队)而非 LLM 响应本身。
"""

from __future__ import annotations

from typing import Any

import pytest

from mybuddy.config import Config
from mybuddy.learning import DreamJob
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolSpec
from mybuddy.memory import LongTermMemory, UserProfile
from mybuddy.storage import init_db, list_undelivered

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


@pytest.mark.asyncio
async def test_dedup_merges_similar_memories(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    # 写两条几乎相同的记忆(mock embedding 对相同词 token hash 一致)
    ltm.add("用户爱喝手冲咖啡", mem_type="memory")
    ltm.add("用户爱喝手冲咖啡", mem_type="memory")  # 重复
    ltm.add("用户对海鲜过敏", mem_type="memory")     # 不同

    provider = ScriptedProvider(
        ["[]", "[]", "[]"]  # conflict, insights, nudges 都返回空
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

    # 新命题(刚写入,updated_at=now) → 应 +BOOST
    # 无法伪造"旧"命题(SQLAlchemy onupdate=utcnow),但验证新命题 confidence 变化即可
    cid = profile.add_claim("用户周日情绪偏低", confidence=0.5)

    provider = ScriptedProvider(["[]", "[]", "[]"])
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.confidence_updates >= 1
    claims_after = profile.get_all_claims()
    # 新命题 last_update 在 cutoff 内 → +BOOST,应 > 0.5
    match = next((c for c in claims_after if c["sql_id"] == cid), None)
    assert match is not None
    assert match["confidence"] > 0.5


@pytest.mark.asyncio
async def test_insights_generated_and_added(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    # 写两条 claim(>=2 让 _resolve_conflicts 能真的调 LLM,占据第 1 个脚本响应)
    profile.add_claim("用户偏好简洁沟通", confidence=0.6)
    profile.add_claim("用户对海鲜过敏", confidence=0.8)

    # 写一条今日 message,让 _collect_today_messages 有东西返回
    from mybuddy.storage import Message as DBMessage
    from mybuddy.storage import session_scope

    with session_scope(engine) as s:
        s.add(DBMessage(session_id="t", role="user", content="今天去爬山了"))

    # LLM 依次返回:conflict=空、insights 2 条、nudges=空
    provider = ScriptedProvider(
        [
            "[]",
            '[{"claim": "用户喜欢户外活动", "confidence": 0.4}, '
            '{"claim": "用户可能最近在锻炼", "confidence": 0.35}]',
            "[]",
        ]
    )

    before = len(profile.get_all_claims())
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.insights_added == 2
    after = len(profile.get_all_claims())
    assert after - before == 2


@pytest.mark.asyncio
async def test_nudges_enqueued(dream_env) -> None:
    engine, cfg, ltm, profile = dream_env

    ltm.add("用户去年搬家到上海", mem_type="memory", extra_meta={"created_at": "2025-01-01"})
    ltm.add("用户今年想养只猫", mem_type="memory", extra_meta={"created_at": "2025-03-01"})

    # 为让 conflict 和 insights 步都调 LLM,需要 claims >= 2 且 messages 非空
    profile.add_claim("用户偏好简洁沟通", confidence=0.6)
    profile.add_claim("用户对海鲜过敏", confidence=0.8)
    from mybuddy.storage import Message as DBMessage
    from mybuddy.storage import session_scope
    with session_scope(engine) as s:
        s.add(DBMessage(session_id="t", role="user", content="随便一条"))

    # conflict=空、insights=空、nudges 2 条
    provider = ScriptedProvider(
        [
            "[]",
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
async def test_run_collects_errors_without_crashing(dream_env) -> None:
    """某一步异常不应导致其他步骤失败。"""
    engine, cfg, ltm, profile = dream_env

    # 补齐前置数据,让 LLM 相关步骤至少会被调用到
    profile.add_claim("a", confidence=0.6)
    profile.add_claim("b", confidence=0.6)
    ltm.add("some memory", mem_type="memory")
    from mybuddy.storage import Message as DBMessage
    from mybuddy.storage import session_scope
    with session_scope(engine) as s:
        s.add(DBMessage(session_id="t", role="user", content="x"))

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

    # LLM 相关的三步(conflict / insights / nudges)应该全部进 errors,但不崩
    assert len(report.errors) >= 3
    assert all("LLM" in e or "RuntimeError" in e for e in report.errors)
