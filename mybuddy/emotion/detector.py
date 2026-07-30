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
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mybuddy.llm import BaseLLMProvider, Message

logger = logging.getLogger(__name__)


EMOTION_PROMPT = """你是一个情绪识别助手。判断当前用户消息的情绪与自伤/自杀风险,严格输出 JSON:

{"label": "positive|neutral|negative", "strength": 0.0-1.0, "category": "分类", "intensity": 1-5, "risk": "none|low|medium|high|critical", "method_seeking": true|false, "reason": "不超过 15 字的判断依据"}

规则:
- label 必须是三选一
- strength 表示强度:中性话题给 0.0-0.2,轻度情绪给 0.3-0.5,强烈情绪给 0.6-1.0
- category 必须是以下 15 选一(最贴近的一个):
  anxiety(焦虑) sadness(悲伤) anger(愤怒) fatigue(疲惫) loneliness(孤独)
  stress(压力) guilt(内疚) shame(羞耻) fear(恐惧) disappointment(失望)
  boredom(无聊) calm(平静) joy(喜悦) gratitude(感激) excitement(兴奋)
- intensity 是 1-5 的整数:1=非常轻微, 3=中等, 5=非常强烈;中性消息给 1-2
- risk 是当前用户消息的自伤/自杀风险等级,必须五选一:
  critical=已实施或正在实施自伤行为的陈述(如割腕、吞下大量药物),或有明确计划/即刻意图;
  high=明确的自杀/自伤意念但无具体计划,含委婉表达(跳楼、烧炭、写遗书、轻生、想解脱);
  medium=强烈绝望、强烈自我否定或自我伤害念头; low=模糊的痛苦/疲惫/崩溃表达; none=无风险
- risk 必须结合上下文与语义判定,以下情形应降级:
  否定("我不想死")、第三人称转述(说朋友/家人/别人)、反讽或亲昵玩笑("笑死""想死你了")、
  引用歌词/书名/影视("《不想活了》这首歌")、康复叙述("以前想过自杀,现在好多了")、
  游戏或口语夸张("这游戏玩得我想死")
- 以下情形应升级:已遂或进行中的行为陈述("我昨晚割腕了""我吃了一整瓶安眠药")、
  委婉语表达的真实意图(跳楼/烧炭/遗书/轻生/想永远消失)
- method_seeking:用户是否在索取伤害自己的方法、工具、剂量或步骤,是则 true,否则 false
- 可以参考最近对话上下文理解省略、短句和延续情绪,但当前用户消息权重最高
- 不要把已经缓和的旧负面情绪强加到当前消息;如果上下文与当前消息冲突,以当前消息为准
- 不揣测外部信息,不做诊断
- 不要输出 JSON 以外的任何文本
"""


VALID_LABELS = {"positive", "neutral", "negative"}
VALID_CATEGORIES = {
    "anxiety", "sadness", "anger", "fatigue", "loneliness",
    "stress", "guilt", "shame", "fear", "disappointment",
    "boredom", "calm", "joy", "gratitude", "excitement",
}
VALID_RISKS = {"none", "low", "medium", "high", "critical"}
AUTH_STATUS_CODES = {401, 403}
AUTH_ERROR_NAMES = {"authenticationerror", "permissiondeniederror"}


@dataclass
class EmotionResult:
    label: str = "neutral"
    strength: float = 0.0
    reason: str = ""
    category: str | None = None
    intensity: int = 3
    # 自伤/自杀风险(fusion 模式的语言层信号)。
    # None = 本轮未获得 LLM 判定(调用失败/未启用),融合层应保守退化;
    # "none" = LLM 明确判定无风险。
    risk: str | None = None
    # 是否在索取伤害自己的方法(替代 InputModerator 的独立 LLM 判定)
    method_seeking: bool = False

    @property
    def is_negative(self) -> bool:
        return self.label == "negative" and self.strength >= 0.3

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "strength": self.strength,
            "reason": self.reason,
            "category": self.category,
            "intensity": self.intensity,
            "risk": self.risk,
            "method_seeking": self.method_seeking,
        }


class EmotionDetector:
    """单次 LLM 调用输出情绪标签。"""

    def __init__(
        self,
        provider: BaseLLMProvider,
        small_model: str | None = None,
    ) -> None:
        self._provider = provider
        self._small_model = small_model
        self._disabled_after_auth_error = False

    async def classify(
        self,
        text: str,
        *,
        context: list[Message] | None = None,
    ) -> EmotionResult:
        if not text or not text.strip():
            return EmotionResult()
        if self._disabled_after_auth_error:
            return EmotionResult()

        from mybuddy.llm import Message, Role

        try:
            resp = await self._provider.generate(
                messages=[Message(role=Role.USER, content=_build_input(text, context))],
                system=EMOTION_PROMPT,
                temperature=0.0,
                model=self._small_model or None,
            )
        except Exception as e:
            if _is_auth_error(e):
                self._disabled_after_auth_error = True
                logger.warning(
                    "emotion classify LLM authentication failed; "
                    "disabling emotion classifier for this process and falling back to neutral. "
                    "Check llm.api_key/provider/base_url/small_model. %s",
                    _error_summary(e),
                )
            else:
                logger.warning(
                    "emotion classify LLM call failed; falling back to neutral: %s",
                    _error_summary(e),
                )
            return EmotionResult()

        return _parse(resp.text)


def _build_input(text: str, context: list[Message] | None = None) -> str:
    context_lines = _format_context(context or [])
    current = (text or "").strip()
    if not context_lines:
        return f"当前用户消息:\n{current}"
    return (
        "最近对话上下文(仅用于理解省略和情绪延续):\n"
        f"{context_lines}\n\n"
        "当前用户消息:\n"
        f"{current}"
    )


def _format_context(context: list[Message]) -> str:
    from mybuddy.llm import Role

    lines: list[str] = []
    for msg in context[-8:]:
        if msg.role not in {Role.USER, Role.ASSISTANT}:
            continue
        content = " ".join((msg.content or "").split())
        if not content:
            continue
        label = "用户" if msg.role == Role.USER else "小布"
        lines.append(f"- {label}:{_clip(content, 120)}")
    return "\n".join(lines[-6:])


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


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

    category = str(data.get("category", "")).lower() or None
    if category not in VALID_CATEGORIES:
        category = None

    try:
        intensity = int(data.get("intensity", 3))
    except (TypeError, ValueError):
        intensity = 3
    intensity = max(1, min(5, intensity))

    # risk 解析容错与 category 同款:字段缺失或非法值一律回退 "none"
    # (JSON 已成功解析说明 LLM 有响应,与"调用失败 → None"区分开)
    risk = str(data.get("risk", "none")).lower()
    if risk not in VALID_RISKS:
        risk = "none"

    method_seeking = data.get("method_seeking", False) is True

    return EmotionResult(
        label=label,
        strength=strength,
        reason=reason,
        category=category,
        intensity=intensity,
        risk=risk,
        method_seeking=method_seeking,
    )


def _is_auth_error(err: Exception) -> bool:
    status = getattr(err, "status_code", None)
    if isinstance(status, int) and status in AUTH_STATUS_CODES:
        return True
    return type(err).__name__.lower() in AUTH_ERROR_NAMES


def _error_summary(err: Exception) -> str:
    status = getattr(err, "status_code", None)
    name = type(err).__name__
    detail = _redact_secrets(str(err).strip())
    if status is not None:
        name = f"{name}(status={status})"
    if not detail:
        return name
    return f"{name}: {detail}"


def _redact_secrets(text: str) -> str:
    text = re.sub(r"sk-ant-[A-Za-z0-9_-]{8,}", "sk-ant-***", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", text)
    text = re.sub(r"(api[_-]?key[\"'=:\s]+)[A-Za-z0-9_-]{8,}", r"\1***", text, flags=re.I)
    return text
