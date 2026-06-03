"""情绪检测 + 情绪窗口测试。"""

from __future__ import annotations

import pytest

from mybuddy.emotion import (
    EmotionDetector,
    EmotionResult,
    EmotionTracker,
    build_emotional_support,
    support_system_hint,
)
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolSpec


class ScriptedProvider(BaseLLMProvider):
    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)

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
        text = self._texts.pop(0) if self._texts else "{}"
        return LLMResponse(text=text, finish_reason="stop")


# ---- detector ----

@pytest.mark.asyncio
async def test_detector_parses_valid_json() -> None:
    provider = ScriptedProvider([
        '{"label": "negative", "strength": 0.7, "reason": "沮丧"}'
    ])
    det = EmotionDetector(provider)
    r = await det.classify("今天好累啊")
    assert r.label == "negative"
    assert r.strength == 0.7
    assert r.is_negative


@pytest.mark.asyncio
async def test_detector_strips_code_fence() -> None:
    provider = ScriptedProvider([
        '```json\n{"label": "positive", "strength": 0.8, "reason": "开心"}\n```'
    ])
    det = EmotionDetector(provider)
    r = await det.classify("太棒啦!")
    assert r.label == "positive"


@pytest.mark.asyncio
async def test_detector_returns_neutral_on_bad_json() -> None:
    provider = ScriptedProvider(["这不是 JSON"])
    det = EmotionDetector(provider)
    r = await det.classify("嗯")
    assert r.label == "neutral"
    assert r.strength == 0.0


@pytest.mark.asyncio
async def test_detector_clamps_strength() -> None:
    provider = ScriptedProvider([
        '{"label": "negative", "strength": 1.5, "reason": "x"}'
    ])
    det = EmotionDetector(provider)
    r = await det.classify("烦死了")
    assert r.strength == 1.0


@pytest.mark.asyncio
async def test_detector_rejects_invalid_label() -> None:
    provider = ScriptedProvider([
        '{"label": "angry", "strength": 0.9}'
    ])
    det = EmotionDetector(provider)
    r = await det.classify("x")
    assert r.label == "neutral"


@pytest.mark.asyncio
async def test_detector_empty_text_no_llm_call() -> None:
    provider = ScriptedProvider([])  # 空脚本,被调就炸
    det = EmotionDetector(provider)
    r = await det.classify("   ")
    assert r.label == "neutral"


@pytest.mark.asyncio
async def test_detector_swallows_llm_exception() -> None:
    class BrokenProvider(BaseLLMProvider):
        async def generate(self, *a, **k):
            raise RuntimeError("boom")

    det = EmotionDetector(BrokenProvider())
    r = await det.classify("test")
    assert r.label == "neutral"


# ---- tracker ----

def _r(label: str, strength: float = 0.5) -> EmotionResult:
    return EmotionResult(label=label, strength=strength)


def test_tracker_consecutive_negative_false_when_too_short() -> None:
    t = EmotionTracker()
    t.add(_r("negative", 0.6))
    assert not t.is_consecutive_negative(n=2)


def test_tracker_consecutive_negative_true() -> None:
    t = EmotionTracker()
    t.add(_r("negative", 0.6))
    t.add(_r("negative", 0.8))
    assert t.is_consecutive_negative(n=2)


def test_tracker_consecutive_negative_broken_by_positive() -> None:
    t = EmotionTracker()
    t.add(_r("negative", 0.6))
    t.add(_r("positive", 0.5))
    t.add(_r("negative", 0.7))
    # 最近两条:positive + negative → 不满足
    assert not t.is_consecutive_negative(n=2)


def test_tracker_weak_negative_doesnt_count() -> None:
    """strength < 0.3 不算有效负面(避免草木皆兵)。"""
    t = EmotionTracker()
    t.add(_r("negative", 0.2))
    t.add(_r("negative", 0.2))
    assert not t.is_consecutive_negative(n=2)


def test_tracker_window_bounded() -> None:
    t = EmotionTracker(window=3)
    for _i in range(5):
        t.add(_r("neutral", 0.0))
    assert len(t) == 3


# ---- emotional support ----

def test_support_for_negative_anxiety() -> None:
    support = build_emotional_support(
        "我很焦虑,怕明天汇报讲不好",
        EmotionResult(label="negative", strength=0.7, reason="焦虑"),
    )

    assert support.mode == "strong_support"
    assert "稳定感" in support.mirror
    assert "3 个要点" in support.small_action
    assert "具体镜映" in support.principles


def test_support_crisis_mode_has_safety_note() -> None:
    support = build_emotional_support(
        "我不想活了",
        EmotionResult(label="negative", strength=1.0, reason="极端"),
    )

    assert support.mode == "safety"
    assert support.safety_note
    assert "现实支持" in support.principles


def test_support_hint_not_empty_for_negative() -> None:
    support = build_emotional_support(
        "今天好累",
        EmotionResult(label="negative", strength=0.5, reason="累"),
    )

    hint = support_system_hint(support)
    assert "内部情绪场景线索" in hint
    assert "低压行动" in hint
    assert "固定三段式" in hint
