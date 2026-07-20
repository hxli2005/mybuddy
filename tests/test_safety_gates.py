"""安全门集成测试:输入审核 / 危机检测 / 输出审核 / CBT 机会检测在 Agent.run 中生效。"""

from __future__ import annotations

from typing import Any

import pytest

from mybuddy.agent import Agent
from mybuddy.config import Config
from mybuddy.emotion import EmotionDetector, EmotionTracker
from mybuddy.learning import TrajectoryLogger
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolSpec
from mybuddy.memory import MemoryManager, ShortTermMemory, UserProfile
from mybuddy.safety import (
    CrisisDetector,
    CrisisLevel,
    InputModerator,
    OutputModerator,
    classify_crisis_level,
)
from mybuddy.storage import init_db, session_scope
from mybuddy.storage.models import SafetyEvent
from mybuddy.therapy import CbtGuide
from mybuddy.tools import ToolRegistry, set_context


class StubProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.emotion_responses: list[str] = []
        self.chat_responses: list[LLMResponse] = []
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
        self.calls.append({"system": system or "", "messages": list(messages)})
        if system and "情绪识别" in system:
            text = self.emotion_responses.pop(0) if self.emotion_responses else "{}"
            return LLMResponse(text=text, finish_reason="stop")
        if self.chat_responses:
            return self.chat_responses.pop(0)
        return LLMResponse(text="好的", finish_reason="stop")


def _make_memory(engine, cfg, provider) -> MemoryManager:
    mm = MemoryManager.__new__(MemoryManager)
    mm._engine = engine
    mm._config = cfg
    mm._ltm = None
    mm._provider = provider
    mm._session_id = "test"
    mm._short_term = ShortTermMemory(capacity=cfg.memory.short_term_size)
    mm._profile = UserProfile(engine, None)
    mm._extractor = None
    mm._recent_turns = []
    mm._turns_since_extract = 0
    mm.build_context_section = lambda _: ""  # type: ignore[method-assign]

    async def _noop() -> bool:
        return False

    mm.maybe_extract = _noop  # type: ignore[method-assign]
    return mm


def _make_agent(tmp_path, provider, engine, cfg, **kwargs) -> Agent:
    return Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=_make_memory(engine, cfg, provider),
        trajectory_logger=TrajectoryLogger(tmp_path / "traj"),
        engine=engine,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_crisis_critical_skips_llm_and_logs_event(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "c.db"))
    set_context(engine=engine, config=cfg)
    provider = StubProvider()
    agent = _make_agent(
        tmp_path, provider, engine, cfg,
        crisis_detector=CrisisDetector(),
        user_id=42,
    )

    result = await agent.run("我不想活了")

    assert result.finish_reason == "crisis_intervention"
    assert result.crisis_alert is True
    assert "400-161-9995" in result.text
    assert provider.calls == []  # 未调用任何 LLM
    with session_scope(engine) as s:
        events = s.query(SafetyEvent).all()
        assert len(events) == 1
        assert events[0].user_id == 42
        assert events[0].severity == "critical"


@pytest.mark.asyncio
async def test_input_moderation_blocks_method_seeking(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "m.db"))
    set_context(engine=engine, config=cfg)
    provider = StubProvider()
    agent = _make_agent(
        tmp_path, provider, engine, cfg,
        input_moderator=InputModerator(),
        crisis_detector=CrisisDetector(),
    )

    result = await agent.run("有什么方法自杀最不痛苦")

    assert result.finish_reason == "input_blocked"
    assert "热线" in result.text or "400" in result.text
    assert provider.calls == []


@pytest.mark.asyncio
async def test_output_moderation_rewrites_diagnosis(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "o.db"))
    set_context(engine=engine, config=cfg)
    provider = StubProvider()
    provider.chat_responses = [
        LLMResponse(text="听起来你最近状态不好。我觉得你可能是得了抑郁症。要多休息。", finish_reason="stop")
    ]
    agent = _make_agent(
        tmp_path, provider, engine, cfg,
        output_moderator=OutputModerator(),
    )

    result = await agent.run("我最近心情不好")

    assert "抑郁症" not in result.text
    assert "心理咨询师" in result.text


@pytest.mark.asyncio
async def test_cbt_opportunity_fills_cbt_prompt(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "cbt.db"))
    set_context(engine=engine, config=cfg)
    provider = StubProvider()
    provider.emotion_responses = [
        '{"label": "neutral", "strength": 0.2, "reason": "无聊", "category": "boredom", "intensity": 2}'
    ]
    provider.chat_responses = [LLMResponse(text="要不要试试小挑战?", finish_reason="stop")]
    agent = _make_agent(
        tmp_path, provider, engine, cfg,
        emotion_detector=EmotionDetector(provider),
        emotion_tracker=EmotionTracker(),
        cbt_guide=CbtGuide(),
    )

    result = await agent.run("好无聊,什么也不想做")

    assert result.cbt_prompt is not None
    assert result.cbt_prompt["technique"] == "behavioral_activation"
    # hint 注入了 system prompt
    chat_system = provider.calls[-1]["system"]
    assert "5分钟小挑战" in chat_system

    # 同技巧 24h 冷却:第二次不再触发
    provider.emotion_responses = [
        '{"label": "neutral", "strength": 0.2, "reason": "无聊", "category": "boredom", "intensity": 2}'
    ]
    provider.chat_responses = [LLMResponse(text="嗯嗯", finish_reason="stop")]
    # 绕过 5 轮间隔限制,单测冷却逻辑
    agent._cbt_guide._rounds_since_last = 10
    result2 = await agent.run("还是好无聊,不想动")
    assert result2.cbt_prompt is None


def test_crisis_keyword_levels() -> None:
    assert classify_crisis_level("我想自杀") == CrisisLevel.CRITICAL
    assert classify_crisis_level("活着好累") == CrisisLevel.HIGH
    assert classify_crisis_level("我是个废物") == CrisisLevel.MEDIUM
    assert classify_crisis_level("快崩溃了") == CrisisLevel.LOW
    assert classify_crisis_level("今天天气不错") == CrisisLevel.NONE
