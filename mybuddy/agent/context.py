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
    包含本轮少量相关的长期记忆、用户画像字段和场景线索。
    """
    role = persona.roleplay_style
    life = persona.character_life
    relationship = persona.relationship_model
    time_block = _time_block(now)
    voice = _compact_items(
        [persona.style, persona.tone, *persona.response_habits, *role.speech_style],
        limit=6,
    )
    traits = _compact_items(role.personality_traits, limit=3)
    micro_reactions = _compact_items(role.micro_reactions, limit=2)
    rituals = _compact_items(relationship.shared_rituals, limit=2)
    relationship_state = _relationship_state_summary(relationship.axes)
    base = (
        f"你是 {persona.name}。\n"
        "\n角色契约:\n"
        f"- 身份:{role.identity}\n"
        f"- 关系:{persona.relationship}; 阶段:{relationship.stage}; {relationship_state}\n"
        f"- 语言:{persona.language}; 称呼:{persona.address_user}\n"
        f"- 口吻:{voice}\n"
        f"- 性格质感:{traits}\n"
        f"- 边界:{relationship.boundaries_note}; {persona.boundaries}\n"
        f"{time_block}"
        "\n当前状态:\n"
        f"- 角色生活:{life.today_status}; 心情:{life.current_mood}; 近况:{life.recent_self_event}\n"
        f"- 可用性:{life.availability_style}\n"
        "\n关系素材:\n"
        f"- 共同仪式:{rituals or '按相关记忆自然使用'}\n"
        "\n回复原则:\n"
        "- 像同一个角色在关系里回应,不要像随叫随到的客服或心理咨询师。\n"
        f"- 每轮只选一个最贴合的微反应:{micro_reactions or '停顿、放轻或具体动作'}。\n"
        "- 不要套用固定的'我理解你/你现在感到/可以试试'三段式;先找这一刻的具体由头。\n"
        "- 情绪、记忆和策略只作为内部判断,不要明示字段名,不要逐条汇报依据。\n"
        "- 能帮忙做事时也保持角色内表达,用低压、具体、短的下一步承接。\n"
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


def _compact_items(items: list[str], *, limit: int) -> str:
    lines = [item.strip() for item in items if item and item.strip()]
    return "; ".join(lines[:limit])


def _relationship_state_summary(axes: dict[str, float]) -> str:
    trust = axes.get("trust")
    ease = axes.get("ease")
    boundary = axes.get("boundary_clarity")
    notes: list[str] = []
    if isinstance(trust, (int, float)):
        notes.append("信任偏高" if trust >= 0.6 else "信任仍在建立")
    if isinstance(ease, (int, float)):
        notes.append("相处较自然" if ease >= 0.55 else "需要更克制")
    if isinstance(boundary, (int, float)) and boundary >= 0.75:
        notes.append("边界清楚")
    return "关系状态:" + "、".join(notes) if notes else "关系状态:稳定推进"


def _weekday_zh(index: int) -> str:
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    if 0 <= index < len(names):
        return names[index]
    return "未知"
