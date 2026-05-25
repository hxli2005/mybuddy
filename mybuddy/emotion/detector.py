"""情绪检测器:LLM 二级分类(label + strength)。

设计原则:
  - 用 small_model 控成本,每轮对话开头跑一次
  - 输出严格 JSON,容错策略与 FactExtractor 同款(```json 围栏剥离 + 失败回退 neutral)
  - 失败时返回 neutral,不阻塞主对话流

输出结构:
  {label: "positive"|"neutral"|"negative", strength: 0.0-1.0, reason: "简述"}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mybuddy.llm import BaseLLMProvider

logger = logging.getLogger(__name__)


EMOTION_PROMPT = """你是一个情绪识别助手。判断以下用户消息的情绪,严格输出 JSON:

{"label": "positive|neutral|negative", "strength": 0.0-1.0, "reason": "不超过 15 字的判断依据"}

规则:
- label 必须是三选一
- strength 表示强度:中性话题给 0.0-0.2,轻度情绪给 0.3-0.5,强烈情绪给 0.6-1.0
- 只看用户当前这条消息,不揣测外部信息
- 不要输出 JSON 以外的任何文本
"""


VALID_LABELS = {"positive", "neutral", "negative"}


@dataclass
class EmotionResult:
    label: str = "neutral"
    strength: float = 0.0
    reason: str = ""

    @property
    def is_negative(self) -> bool:
        return self.label == "negative" and self.strength >= 0.3

    def to_dict(self) -> dict:
        return {"label": self.label, "strength": self.strength, "reason": self.reason}


class EmotionDetector:
    """单次 LLM 调用输出情绪标签。"""

    def __init__(
        self,
        provider: BaseLLMProvider,
        small_model: str | None = None,
    ) -> None:
        self._provider = provider
        self._small_model = small_model

    async def classify(self, text: str) -> EmotionResult:
        if not text or not text.strip():
            return EmotionResult()

        from mybuddy.llm import Message, Role

        try:
            resp = await self._provider.generate(
                messages=[Message(role=Role.USER, content=text)],
                system=EMOTION_PROMPT,
                temperature=0.0,
                model=self._small_model or None,
            )
        except Exception as e:
            logger.warning(
                "emotion classify LLM call failed; falling back to neutral: %s",
                type(e).__name__,
            )
            return EmotionResult()

        return _parse(resp.text)


def _parse(text: str) -> EmotionResult:
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean = "\n".join(lines)

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        return EmotionResult()

    if not isinstance(data, dict):
        return EmotionResult()

    label = str(data.get("label", "neutral")).lower()
    if label not in VALID_LABELS:
        label = "neutral"

    try:
        strength = float(data.get("strength", 0.0))
    except (TypeError, ValueError):
        strength = 0.0
    strength = max(0.0, min(1.0, strength))

    reason = str(data.get("reason", ""))[:50]

    return EmotionResult(label=label, strength=strength, reason=reason)
