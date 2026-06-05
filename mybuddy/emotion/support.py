"""情绪价值支持策略。

这里不做临床诊断,只把用户当前表达转成可执行的陪伴策略:
镜映感受、识别心理需求、降低负担、给小行动、必要时提示安全边界。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .detector import EmotionResult

CRISIS_RE = re.compile(
    r"自杀|轻生|不想活|结束生命|活不下去|自残|伤害自己|想死|去死|"
    r"suicide|kill myself|self harm",
    re.I,
)


@dataclass
class EmotionalSupport:
    mode: str = "neutral"
    label: str = "neutral"
    strength: float = 0.0
    mirror: str = "这更像是一条普通信息,可以直接回应核心事项。"
    need: str = "清晰回应"
    guidance: str = "直接回答用户问题,保持简洁。"
    small_action: str = "如果需要行动,给一个低成本的下一步。"
    follow_up: str = ""
    safety_note: str = ""
    principles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "label": self.label,
            "strength": self.strength,
            "mirror": self.mirror,
            "need": self.need,
            "guidance": self.guidance,
            "small_action": self.small_action,
            "follow_up": self.follow_up,
            "safety_note": self.safety_note,
            "principles": list(self.principles),
        }


def build_emotional_support(user_input: str, emotion: EmotionResult | None) -> EmotionalSupport:
    text = user_input.strip()
    label = emotion.label if emotion is not None else "neutral"
    strength = emotion.strength if emotion is not None else 0.0

    if _is_crisis(text):
        return EmotionalSupport(
            mode="safety",
            label=label,
            strength=strength,
            mirror="用户表达了可能涉及自伤或极端绝望的内容,需要优先处理安全。",
            need="即时安全与现实支持",
            guidance=(
                "先明确表达重视和陪伴,不要诊断,不要争辩。"
                "鼓励用户立刻联系身边可信任的人或当地紧急服务。"
            ),
            small_action="请用户先把自己移动到相对安全的地方,并联系一个现实中的人。",
            follow_up="如果用户仍在表达风险,继续保持简短、稳定、以安全为优先。",
            safety_note="高风险情境:不要提供伤害方法,不要承诺保密,不要替代专业帮助。",
            principles=["安全优先", "现实支持", "不诊断", "不提供伤害细节"],
        )

    if label == "negative" and strength >= 0.6:
        return EmotionalSupport(
            mode="strong_support",
            label=label,
            strength=strength,
            mirror=_negative_mirror(text, strong=True),
            need="被理解、被接住、恢复一点掌控感",
            guidance="先用 1-2 句话具体承接情绪,不要马上讲大道理或给长方案。",
            small_action=_small_action(text),
            follow_up="适合在稍后主动问一句状态是否缓和。",
            principles=["具体镜映", "非评判接纳", "降低任务压力", "小行动"],
        )

    if label == "negative" and strength >= 0.3:
        return EmotionalSupport(
            mode="support",
            label=label,
            strength=strength,
            mirror=_negative_mirror(text, strong=False),
            need="确认感与问题整理",
            guidance="先承认感受,再把问题拆成一两个可处理部分。",
            small_action=_small_action(text),
            principles=["先共情", "再整理", "给低负担下一步"],
        )

    if label == "positive" and strength >= 0.3:
        return EmotionalSupport(
            mode="positive",
            label=label,
            strength=strength,
            mirror="用户当前情绪偏正向,可以回应其积极状态并帮助延续行动。",
            need="被认可与继续推进",
            guidance="简短确认积极进展,再帮助用户把势头转成下一步。",
            small_action="询问或建议一个可以延续当前状态的小动作。",
            principles=["认可具体进展", "延续动力", "不过度夸张"],
        )

    return EmotionalSupport(label=label, strength=strength)


def support_system_hint(
    support: EmotionalSupport,
    *,
    consecutive_negative: bool = False,
) -> str:
    if support.mode == "neutral":
        return ""
    state = _scene_state(support, consecutive_negative=consecutive_negative)
    lines = [
        "## 当前场景",
        f"- 用户状态:{state}",
        f"- 回应策略:{support.guidance}",
        f"- 可用动作:{support.small_action}",
    ]
    if support.safety_note:
        lines.append(f"- 安全边界:{support.safety_note}")
    if consecutive_negative and support.mode != "safety":
        lines.append("- 连续低落:先放轻靠近,不要急着安排任务。")
    lines.append(
        "- 避免:不要明示这些字段名;不要套用'我理解你/你现在感到/可以试试'的固定三段式。"
    )
    return "\n".join(lines)


def _scene_state(
    support: EmotionalSupport,
    *,
    consecutive_negative: bool,
) -> str:
    if support.mode == "safety":
        return "高风险安全场景,优先现实安全与可信任的人。"
    if consecutive_negative:
        return f"连续低落,可能需要{support.need}。"
    return f"{support.need}; {support.mirror}"


def _is_crisis(text: str) -> bool:
    return bool(CRISIS_RE.search(text))


def _negative_mirror(text: str, *, strong: bool) -> str:
    if any(k in text for k in ("焦虑", "紧张", "怕", "担心")):
        return "用户像是在担心结果或评价,需要先获得稳定感。" if strong else "用户有焦虑或担心,需要先被理解。"
    if any(k in text for k in ("累", "疲惫", "撑不住", "不想做")):
        return "用户像是持续消耗后明显疲惫,需要先降低压力。" if strong else "用户有疲惫感,需要低压力回应。"
    if any(k in text for k in ("委屈", "难受", "没人理解")):
        return "用户像是有委屈和孤立感,需要被看见。" if strong else "用户可能觉得委屈,需要确认感。"
    return "用户情绪偏低,需要先承接感受再处理事情。"


def _small_action(text: str) -> str:
    if any(k in text for k in ("汇报", "答辩", "演示", "导师")):
        return "先列出要讲的 3 个要点,不要一次处理完整汇报。"
    if any(k in text for k in ("睡", "累", "疲惫")):
        return "先做一个 5 分钟休息或喝水动作,暂时不要求高效率。"
    if any(k in text for k in ("乱", "不知道", "怎么办")):
        return "先把问题写成 1 句话,再拆出最小下一步。"
    return "先做一个 5-10 分钟内能完成的小动作。"
