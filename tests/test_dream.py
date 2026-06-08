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
        ["[]", "[]"]  # conflict, nudges 都返回空
    )
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.merged_memories >= 1
    # 剩下的 memory 条数 <= 2(去掉了重复)
    remaining = ltm.list_all(mem_type="memory")
    assert len(remaining) <= 2


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

    # 只剩 nudges 这一步会调 LLM(返回 2 条)
    provider = ScriptedProvider(
        ['["最近在上海还习惯吗?", "说起养猫的事,有什么进展没~"]']
    )

    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)
    report = await job.run()

    assert report.nudges_enqueued == 2
    pending = list_undelivered(engine)
    assert len(pending) == 2
    assert all(p["source"] == "nudge" for p in pending)


@pytest.mark.asyncio
async def test_nudges_skip_stale_and_respect_cooldown(dream_env) -> None:
    """nudge 只取 active 话题,且冷却期内不重复打扰同一件事。"""
    from mybuddy.learning.dream import DreamReport

    engine, cfg, ltm, profile = dream_env
    ltm.add(
        "用户说周五要交报告,还没动。",
        mem_type="open_thread",
        uid="ot_active",
        extra_meta={"title": "报告", "status": "active", "contact_reason": "周五截止"},
    )
    ltm.add(
        "一个已经过期的旧话题。",
        mem_type="open_thread",
        uid="ot_stale",
        extra_meta={"title": "旧", "status": "stale", "contact_reason": "x"},
    )
    provider = ScriptedProvider(['["在想你那个报告,开头开了吗~"]'])
    job = DreamJob(engine=engine, config=cfg, provider=provider, ltm=ltm, profile=profile)

    r1 = DreamReport()
    await job._generate_nudges(r1)
    assert r1.nudges_enqueued == 1  # 只 nudge active,跳过 stale
    active = next(i for i in ltm.list_all(mem_type="open_thread") if i["id"] == "ot_active")
    stale = next(i for i in ltm.list_all(mem_type="open_thread") if i["id"] == "ot_stale")
    assert active["metadata"].get("last_nudged_at")  # 被打上冷却戳
    assert not stale["metadata"].get("last_nudged_at")  # stale 完全没被碰

    # 冷却期内再跑:同一话题被跳过,不再 nudge
    r2 = DreamReport()
    await job._generate_nudges(r2)
    assert r2.nudges_enqueued == 0


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

    # 补齐前置数据,让 LLM 相关步骤(nudges / dynamics)至少会被调用到
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

    # LLM 相关步骤(nudges / dynamics)应该进 errors,但不崩
    assert len(report.errors) >= 2
    assert all("LLM" in e or "RuntimeError" in e for e in report.errors)
