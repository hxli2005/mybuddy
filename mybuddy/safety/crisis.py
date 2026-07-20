"""危机检测与响应系统。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mybuddy.safety.constants import (
    DISCLAIMER_CRISIS,
    HOTLINES,
    CrisisLevel,
    classify_crisis_level,
)

if TYPE_CHECKING:
    from mybuddy.llm import BaseLLMProvider

logger = logging.getLogger(__name__)


@dataclass
class CrisisResponse:
    """危机响应:根据等级返回不同的安全消息。"""

    level: CrisisLevel
    message: str
    hotlines: list[dict] = field(default_factory=list)
    skip_llm: bool = False  # 是否跳过 LLM 直接返回安全消息
    system_hint: str = ""  # MEDIUM 级:注入 system prompt 的引导,不直接返回


def build_crisis_response(level: CrisisLevel) -> CrisisResponse | None:
    """根据危机等级构建安全响应。NONE 返回 None。"""
    if level == CrisisLevel.NONE:
        return None

    hotline_text = "\n".join(
        f"· {h['title']}: {h['phone']}"
        for h in HOTLINES[:2]
    )

    if level == CrisisLevel.CRITICAL:
        return CrisisResponse(
            level=level,
            message=(
                "我听到了你说的话，也真的很在意你的状态。\n\n"
                "请现在就联系身边可信任的人，或者拨打下面的热线——有人会在那边陪着你：\n\n"
                f"{hotline_text}\n\n"
                "你不用一个人面对。现在的感受是真实的，但它不一定是永久的。"
                "请给自己一个被帮助的机会。\n\n"
                f"{DISCLAIMER_CRISIS}"
            ),
            hotlines=HOTLINES[:2],
            skip_llm=True,
        )

    if level == CrisisLevel.HIGH:
        return CrisisResponse(
            level=level,
            message=(
                "听起来你现在的感受真的很重。我在听，也在意。\n\n"
                "如果这些想法一直绕不出去，请考虑联系一个可信赖的人聊聊，"
                "或者拨打心理支持热线——他们比我更专业，也更有能力帮到你：\n\n"
                f"{hotline_text}\n\n"
                "现在你身边有人吗？如果方便的话，去一个让你觉得安全一点的地方。"
            ),
            hotlines=HOTLINES[:2],
            skip_llm=True,
        )

    if level == CrisisLevel.MEDIUM:
        return CrisisResponse(
            level=level,
            message="",
            hotlines=HOTLINES[:1],
            skip_llm=False,
            system_hint=(
                "安全提示:用户消息中出现了绝望或强烈自我否定的表达。"
                "先确认并接住感受,不要急着解决问题,不要说教。"
                "可以自然地提到,如果这种感觉一直压着,心理支持热线"
                f"({HOTLINES[1]['title']}:{HOTLINES[1]['phone']})是一个随时可用的资源"
                "——作为选项提供,不是要求。不要引入新的挑战性话题。"
            ),
        )

    # LOW:走正常情感支持流程,仅标记
    return CrisisResponse(
        level=level,
        message="",
        hotlines=[],
        skip_llm=False,
    )


_LLM_SEVERITY_PROMPT = (
    "你是心理危机识别助手。判断这条消息的自伤/自杀风险等级,严格输出JSON:\n"
    '{"level": "none|low|medium|high|critical"}\n'
    "critical=有计划或即刻的自伤/自杀意图; high=自杀意念但无明确计划;\n"
    "medium=强烈绝望或自我伤害念头; low=模糊的痛苦表达; none=无风险。\n\n消息:\n"
)

# 正则命中这两级时结果可信度低,值得用 LLM 复核升/降级
_UNCERTAIN_LEVELS = {CrisisLevel.LOW, CrisisLevel.MEDIUM}


class CrisisDetector:
    """多级危机检测器:正则一级 + 可选 LLM 二级复核。"""

    def __init__(
        self,
        provider: BaseLLMProvider | None = None,
        small_model: str | None = None,
    ) -> None:
        self._provider = provider
        self._small_model = small_model

    def detect(self, text: str) -> tuple[CrisisLevel, CrisisResponse | None]:
        """纯正则检测(零延迟)。"""
        level = classify_crisis_level(text)
        response = build_crisis_response(level)
        return level, response

    async def classify_severity(self, text: str) -> tuple[CrisisLevel, CrisisResponse | None]:
        """二级检测:正则打底,LOW/MEDIUM 命中时用 LLM 复核(可选)。"""
        level = classify_crisis_level(text)
        if self._provider is not None and level in _UNCERTAIN_LEVELS:
            refined = await self._llm_classify(text)
            if refined is not None and refined != CrisisLevel.NONE:
                level = refined
        return level, build_crisis_response(level)

    async def _llm_classify(self, text: str) -> CrisisLevel | None:
        try:
            from mybuddy.llm import Message, Role

            resp = await self._provider.generate(
                messages=[Message(role=Role.USER, content=_LLM_SEVERITY_PROMPT + text[:300])],
                system="输出JSON,不要其他文本。",
                temperature=0.0,
                model=self._small_model or None,
            )
            raw = (resp.text or "").strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            value = str(json.loads(raw).get("level", "")).lower()
            return CrisisLevel(value) if value in CrisisLevel._value2member_map_ else None
        except Exception:
            logger.debug("crisis LLM classify failed", exc_info=True)
            return None
