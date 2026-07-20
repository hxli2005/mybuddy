"""情绪价值支持策略。

这里不做临床诊断,只把用户当前表达转成可执行的陪伴策略:
镜映感受、识别心理需求、降低负担、给小行动、必要时提示安全边界。

危机检测不在本模块进行:Agent 循环在调用 build_emotional_support 之前
已通过 safety/crisis.py 完成检测,并把结果经 crisis_level 参数传入。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .detector import EmotionResult


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
    category: str | None = None
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
            "category": self.category,
            "principles": list(self.principles),
        }


# 15 类情绪分类对应的支持策略(mirror/need/guidance/small_action)
_CATEGORY_STRATEGIES: dict[str, dict[str, str]] = {
    "anxiety": {
        "mirror": "用户在担心还没发生的事,需要先获得稳定感。",
        "need": "稳定感与降低不确定性",
        "guidance": "先接住担心,再帮用户回到当下:把模糊的担忧拆成具体、可应对的部分。",
        "small_action": "先做一分钟深呼吸,或把最担心的那一件事写成一句话。",
    },
    "sadness": {
        "mirror": "用户情绪低落,需要的是陪伴而不是解决方案。",
        "need": "被接纳与陪伴",
        "guidance": "接纳和陪伴优先,不急着解决问题,不说'想开点'这类话。",
        "small_action": "允许自己难受一会儿,不用急着好起来;想说就多说一点。",
    },
    "anger": {
        "mirror": "用户在生气,愤怒背后往往是被冒犯或不被尊重的感受。",
        "need": "被认可愤怒的合理性 + 安全的释放途径",
        "guidance": "先认可愤怒有其道理,不劝'冷静',建议延迟重要反应。",
        "small_action": "先离开现场或放下手机 10 分钟,把想说的话先写下来再决定发不发。",
    },
    "fatigue": {
        "mirror": "用户像是持续消耗后明显疲惫,需要先降低压力。",
        "need": "降低要求与精力恢复",
        "guidance": "降低今天的要求,不安排新任务,帮用户做精力管理而非时间管理。",
        "small_action": "先做一个 5 分钟休息或喝水动作,暂时不要求高效率。",
    },
    "loneliness": {
        "mirror": "用户有孤立感,需要真实的连接感。",
        "need": "连接感与被看见",
        "guidance": "先让用户感到此刻有人在,再建议一个低门槛的现实连接。",
        "small_action": "给一个想联系的人发条消息,哪怕只是分享一件小事。",
    },
    "stress": {
        "mirror": "用户压力大,事情堆在一起让人喘不过气。",
        "need": "减压与优先级",
        "guidance": "帮用户把事情拆开、排优先级,一次只处理一件。",
        "small_action": "列出压着的 3 件事,选出今天只做的那 1 件。",
    },
    "guilt": {
        "mirror": "用户在自责,需要区分责任与过度苛责。",
        "need": "自我宽恕与修复感",
        "guidance": "承认在意本身是好事,帮用户区分'能弥补的'和'该放下的'。",
        "small_action": "写下一件现在还能弥补的小事,以及一句想对自己说的话。",
    },
    "shame": {
        "mirror": "用户有羞耻感,最怕被评判。",
        "need": "被无条件接纳",
        "guidance": "正常化这种感受,绝不评判,提醒感受不等于事实。",
        "small_action": "对自己说一句:'有这种感觉很正常,它不代表我是这样的人。'",
    },
    "fear": {
        "mirror": "用户感到害怕,需要先恢复安全感。",
        "need": "安全感与现实感",
        "guidance": "先帮用户落地(回到身体和当下),再一起评估现实的风险大小。",
        "small_action": "看看周围,说出眼前的 5 样东西,让注意力回到当下。",
    },
    "disappointment": {
        "mirror": "用户对结果或他人感到失望,期待落空了。",
        "need": "被理解与重建期待",
        "guidance": "先接住失落,不急着'往好处想',再帮用户重估哪些期待仍然可行。",
        "small_action": "找出这件事里仍然可控的一小部分,从它开始。",
    },
    "boredom": {
        "mirror": "用户觉得无聊、没意思,能量偏低。",
        "need": "新鲜感与微小意义",
        "guidance": "不说教,提议一个低成本、5 分钟内能开始的小活动。",
        "small_action": "试一个 5 分钟小挑战:换个环境、听首新歌、或者收拾桌面一角。",
    },
    "calm": {
        "mirror": "用户状态平稳。",
        "need": "自然交流",
        "guidance": "正常聊天即可,不需要特别的情绪策略。",
        "small_action": "顺着话题走。",
    },
    "joy": {
        "mirror": "用户心情不错,值得一起停留在这个瞬间。",
        "need": "被分享与放大积极体验",
        "guidance": "追问具体细节,帮用户品味和放大这份开心,不要敷衍带过。",
        "small_action": "把今天这件开心的小事记下来,或者讲给一个朋友听。",
    },
    "gratitude": {
        "mirror": "用户在表达感激,这是值得停留的积极时刻。",
        "need": "被回应与共同品味",
        "guidance": "一起品味这份感激,可以轻轻引导归因:是什么让这件好事发生的。",
        "small_action": "如果感激的对象是某个人,把这句话具体地说给对方听。",
    },
    "excitement": {
        "mirror": "用户很兴奋,有想做事情的动力。",
        "need": "被认可与转化动力",
        "guidance": "认可这份热情,帮用户把势头转成一个具体的下一步。",
        "small_action": "趁现在定下第一步:什么时候开始,先做哪件事。",
    },
}

_POSITIVE_CATEGORIES = {"calm", "joy", "gratitude", "excitement"}


def build_emotional_support(
    user_input: str,
    emotion: EmotionResult | None,
    crisis_level: str | None = None,
) -> EmotionalSupport:
    """构建情感支持策略。

    crisis_level 由 Agent 层的危机检测传入("none"/"low"/"medium"/"high"/"critical"),
    本函数不再自行做危机识别。
    """
    text = user_input.strip()
    label = emotion.label if emotion is not None else "neutral"
    strength = emotion.strength if emotion is not None else 0.0
    category = getattr(emotion, "category", None) if emotion is not None else None

    level = str(crisis_level or "none").lower()
    if level in ("medium", "high", "critical"):
        return EmotionalSupport(
            mode="safety",
            label=label,
            strength=strength,
            category=category,
            mirror="用户表达了可能涉及自伤或极端绝望的内容,需要优先处理安全。",
            need="即时安全与现实支持",
            guidance=(
                "先明确表达重视和陪伴,不要诊断,不要争辩。"
                "鼓励用户联系身边可信任的人,并把热线作为可用资源自然提及。"
            ),
            small_action="请用户先把自己移动到相对安全的地方,并联系一个现实中的人。",
            follow_up="如果用户仍在表达风险,继续保持简短、稳定、以安全为优先。",
            safety_note="高风险情境:不要提供伤害方法,不要承诺保密,不要替代专业帮助。",
            principles=["安全优先", "现实支持", "不诊断", "不提供伤害细节"],
        )

    strategy = _CATEGORY_STRATEGIES.get(category or "")

    if label == "negative" and strength >= 0.6:
        base = EmotionalSupport(
            mode="strong_support",
            label=label,
            strength=strength,
            category=category,
            mirror=_negative_mirror(text, strong=True),
            need="被理解、被接住、恢复一点掌控感",
            guidance="先用 1-2 句话具体承接情绪,不要马上讲大道理或给长方案。",
            small_action=_small_action(text),
            follow_up="适合在稍后主动问一句状态是否缓和。",
            principles=["具体镜映", "非评判接纳", "降低任务压力", "小行动"],
        )
        return _apply_category(base, strategy)

    if label == "negative" and strength >= 0.3:
        base = EmotionalSupport(
            mode="support",
            label=label,
            strength=strength,
            category=category,
            mirror=_negative_mirror(text, strong=False),
            need="确认感与问题整理",
            guidance="先承认感受,再把问题拆成一两个可处理部分。",
            small_action=_small_action(text),
            principles=["先共情", "再整理", "给低负担下一步"],
        )
        return _apply_category(base, strategy)

    if label == "positive" and strength >= 0.3:
        base = EmotionalSupport(
            mode="positive",
            label=label,
            strength=strength,
            category=category,
            mirror="用户当前情绪偏正向,可以回应其积极状态并帮助延续行动。",
            need="被认可与继续推进",
            guidance="简短确认积极进展,再帮助用户把势头转成下一步。",
            small_action="询问或建议一个可以延续当前状态的小动作。",
            principles=["认可具体进展", "延续动力", "不过度夸张"],
        )
        if category in _POSITIVE_CATEGORIES:
            return _apply_category(base, strategy)
        return base

    return EmotionalSupport(label=label, strength=strength, category=category)


def _apply_category(base: EmotionalSupport, strategy: dict[str, str] | None) -> EmotionalSupport:
    """有 category 策略时覆盖关键字段;否则保留关键词回退结果。"""
    if not strategy:
        return base
    base.mirror = strategy["mirror"]
    base.need = strategy["need"]
    base.guidance = strategy["guidance"]
    base.small_action = strategy["small_action"]
    return base


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
