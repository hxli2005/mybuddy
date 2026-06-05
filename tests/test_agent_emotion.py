"""Agent + 情绪系统集成测试。

验证:
  - 每轮都能拿到 emotion 结果且写进 trajectory.meta
  - 连续 2 轮 negative 触发离线 nudge 入 pending_messages
"""

from __future__ import annotations

from typing import Any

import pytest

from mybuddy.agent import Agent
from mybuddy.config import Config
from mybuddy.emotion import EmotionDetector, EmotionTracker
from mybuddy.learning import TrajectoryLogger
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolSpec
from mybuddy.memory import MemoryManager, ShortTermMemory, UserProfile
from mybuddy.storage import init_db, list_undelivered
from mybuddy.tools import ToolRegistry, set_context


class StubProvider(BaseLLMProvider):
    """可控脚本:按调用次序从 queue 取响应。支持按"模式"区分情绪 vs 对话。"""

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
        # 情绪检测的 system prompt 必有"情绪识别助手"关键字
        if system and "情绪识别" in system:
            text = self.emotion_responses.pop(0) if self.emotion_responses else "{}"
            return LLMResponse(text=text, finish_reason="stop")
        # 其余走对话脚本
        if self.chat_responses:
            return self.chat_responses.pop(0)
        return LLMResponse(text="好的", finish_reason="stop")


def _make_memory(engine, cfg, provider) -> MemoryManager:
    """不依赖 Chroma 的最小 MemoryManager。"""
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
    mm.build_context_section = lambda _: ("", [])  # type: ignore[method-assign]

    async def _noop() -> bool:
        return False

    mm.maybe_extract = _noop  # type: ignore[method-assign]
    return mm


@pytest.mark.asyncio
async def test_emotion_written_to_trajectory_meta(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "e.db"))
    set_context(engine=engine, config=cfg)

    provider = StubProvider()
    provider.emotion_responses = [
        '{"label": "negative", "strength": 0.7, "reason": "累"}'
    ]
    provider.chat_responses = [LLMResponse(text="辛苦啦", finish_reason="stop")]

    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=memory,
        trajectory_logger=logger,
        emotion_detector=EmotionDetector(provider),
        emotion_tracker=EmotionTracker(),
        engine=engine,
    )

    result = await agent.run("今天好累")

    assert result.emotion is not None
    assert result.emotion.label == "negative"
    assert result.trajectory.meta.get("emotion") == {
        "label": "negative",
        "strength": 0.7,
        "reason": "累",
    }
    assert result.emotional_support is not None
    assert result.emotional_support["mode"] == "strong_support"
    assert "emotional_support" in result.trajectory.meta
    chat_system = provider.calls[-1]["system"]
    assert "## 当前场景" in chat_system
    assert "内部情绪提示" not in chat_system
    assert "内部情绪场景线索" not in chat_system


@pytest.mark.asyncio
async def test_consecutive_negative_enqueues_nudge(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "n.db"))
    set_context(engine=engine, config=cfg)

    provider = StubProvider()
    # 两轮都 negative,strength 够强
    provider.emotion_responses = [
        '{"label": "negative", "strength": 0.7}',
        '{"label": "negative", "strength": 0.8}',
    ]
    provider.chat_responses = [
        LLMResponse(text="嗯嗯", finish_reason="stop"),
        LLMResponse(text="抱抱", finish_reason="stop"),
    ]

    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=memory,
        trajectory_logger=logger,
        emotion_detector=EmotionDetector(provider),
        emotion_tracker=EmotionTracker(),
        engine=engine,
    )

    await agent.run("心情不好")
    assert list_undelivered(engine) == []  # 第一轮还不会触发

    await agent.run("真的好累")

    pending = list_undelivered(engine)
    assert len(pending) == 1
    assert pending[0]["source"] == "nudge"


@pytest.mark.asyncio
async def test_single_negative_doesnt_trigger_nudge(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "s.db"))
    set_context(engine=engine, config=cfg)

    provider = StubProvider()
    provider.emotion_responses = [
        '{"label": "negative", "strength": 0.8}',
        '{"label": "positive", "strength": 0.5}',  # 打断连续
    ]
    provider.chat_responses = [
        LLMResponse(text="嗯", finish_reason="stop"),
        LLMResponse(text="好呀", finish_reason="stop"),
    ]

    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=memory,
        trajectory_logger=logger,
        emotion_detector=EmotionDetector(provider),
        emotion_tracker=EmotionTracker(),
        engine=engine,
    )

    await agent.run("烦")
    await agent.run("哈哈算了")

    assert list_undelivered(engine) == []


@pytest.mark.asyncio
async def test_agent_passes_recent_dialogue_to_emotion_detector(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "ctx.db"))
    set_context(engine=engine, config=cfg)

    provider = StubProvider()
    provider.emotion_responses = [
        '{"label": "negative", "strength": 0.6, "reason": "受挫"}',
        '{"label": "negative", "strength": 0.7, "reason": "延续"}',
    ]
    provider.chat_responses = [
        LLMResponse(text="先停一下。", finish_reason="stop"),
        LLMResponse(text="我懂。", finish_reason="stop"),
    ]

    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=memory,
        trajectory_logger=logger,
        emotion_detector=EmotionDetector(provider),
        emotion_tracker=EmotionTracker(),
        engine=engine,
    )

    await agent.run("昨天汇报又卡住了")
    await agent.run("算了")

    emotion_calls = [
        call for call in provider.calls
        if "情绪识别" in call["system"]
    ]
    second_input = emotion_calls[1]["messages"][0].content
    assert "最近对话上下文" in second_input
    assert "昨天汇报又卡住了" in second_input
    assert "先停一下。" in second_input
    assert "当前用户消息" in second_input
    assert "算了" in second_input


@pytest.mark.asyncio
async def test_agent_without_emotion_system_still_works(tmp_path) -> None:
    """没传 emotion_detector 时,Agent 依然能正常 run(兼容旧行为)。"""
    cfg = Config()
    engine = init_db(str(tmp_path / "x.db"))
    set_context(engine=engine, config=cfg)

    provider = StubProvider()
    provider.chat_responses = [LLMResponse(text="hi", finish_reason="stop")]

    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=memory,
        trajectory_logger=logger,
    )

    result = await agent.run("你好")
    assert result.emotion is None
    assert "emotion" not in result.trajectory.meta
