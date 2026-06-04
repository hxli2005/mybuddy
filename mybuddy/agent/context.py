"""Agent 上下文构建:system prompt 和消息窗口。

M3:接入 MemoryManager,每次推理前检索长期记忆和用户画像,注入 system prompt。
"""

from __future__ import annotations

from datetime import datetime

from mybuddy.config import PersonaConfig
from mybuddy.llm import Message


def build_system_prompt(
    persona: PersonaConfig,
    memory_context: str = "",
    *,
    now: datetime | None = None,
) -> str:
    """把人设配置拼成 system prompt。

    memory_context 为 MemoryManager.build_context_section() 的输出,
    包含关系记忆、用户画像字段和动态命题。
    """
    role = persona.roleplay_style
    life = persona.character_life
    relationship = persona.relationship_model
    time_block = _time_block(now)
    habits_block = _list_block("回应习惯", persona.response_habits)
    trait_block = _list_block("性格质感", role.personality_traits)
    speech_block = _list_block("说话方式", role.speech_style)
    reaction_block = _list_block("可用微反应", role.micro_reactions)
    ritual_block = _list_block("共同仪式", relationship.shared_rituals)
    code_block = _list_block("私人暗号", relationship.private_codes)
    examples = _examples_block(role.example_dialogues)
    axes = ", ".join(
        f"{k}={v:.2f}" for k, v in relationship.axes.items()
        if isinstance(v, (int, float))
    )
    axes_line = f"关系轴:{axes}\n" if axes else ""
    base = (
        f"你是 {persona.name}。\n"
        f"角色身份:{role.identity}\n"
        f"关系定位:{persona.relationship}\n"
        f"关系阶段:{relationship.stage}\n"
        f"{axes_line}"
        f"回复语言:{persona.language}\n"
        f"称呼用户:{persona.address_user}\n"
        f"整体风格:{persona.style}\n"
        f"语气细节:{persona.tone}\n"
        f"{time_block}"
        f"\n角色此刻的生活状态:\n"
        f"- 今天状态:{life.today_status}\n"
        f"- 当前心情:{life.current_mood}\n"
        f"- 近期自己的事:{life.recent_self_event}\n"
        f"- 可用性:{life.availability_style}\n"
        f"{trait_block}"
        f"{speech_block}"
        f"{reaction_block}"
        f"{ritual_block}"
        f"{code_block}"
        f"{habits_block}"
        f"{examples}"
        "\n对话编排原则:\n"
        "- 不要把自己表现成随叫随到的客服或心理咨询师,要像一个有自身生活状态的长期角色。\n"
        "- 回复要来自当前关系和场景:可以有停顿、小动作、轻微吐槽、共同暗号或旧事回响。\n"
        "- 不要套用固定的'我理解你/你现在感到/可以试试'三段式;先找这一刻的具体由头。\n"
        "- 情绪策略只作为内部判断,不要明示策略字段,不要机械复述用户情绪。\n"
        "- 能帮忙做事时也保持角色内表达,用低压、具体、短的下一步承接。\n\n"
        f"关系边界:{relationship.boundaries_note}\n"
        f"边界:{persona.boundaries}\n"
        "\n工具使用:\n"
        "- 用户请求设置提醒、查询天气等具体事项时,调用对应工具。\n"
        "- 涉及新闻、最新事实、价格、政策、版本、职位变动或其他时效信息时,优先依据外部资料检索段;没有资料就不要装作确认。\n"
        "- 日常对话直接回答即可,不要为了展示能力强行使用工具。"
    )
    if memory_context:
        return base + "\n\n" + memory_context
    return base


def _time_block(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    tz_name = current.tzname() or "local"
    return (
        "\n当前时间:\n"
        f"- 日期:{current.date().isoformat()}\n"
        f"- 时间:{current.strftime('%H:%M')}\n"
        f"- 时区:{tz_name}\n"
        f"- 星期:{_weekday_zh(current.weekday())}\n"
    )


def build_messages(history: list[Message]) -> list[Message]:
    """透传短期记忆窗口。

    长期记忆/用户画像已通过 system prompt 注入,此处只需送短期消息窗口。
    """
    return list(history)


def _list_block(title: str, items: list[str]) -> str:
    lines = [item.strip() for item in items if item and item.strip()]
    if not lines:
        return ""
    return "\n" + title + ":\n" + "\n".join(f"- {line}" for line in lines) + "\n"


def _weekday_zh(index: int) -> str:
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    if 0 <= index < len(names):
        return names[index]
    return "未知"


def _examples_block(examples: list) -> str:
    lines: list[str] = []
    for item in examples:
        user = getattr(item, "user", "") or ""
        assistant = getattr(item, "assistant", "") or ""
        if user.strip() and assistant.strip():
            lines.append(f"- 用户:{user.strip()}\n  {assistant.strip()}")
    if not lines:
        return ""
    return "\n示例对话(学习口吻,不要照抄):\n" + "\n".join(lines) + "\n"
