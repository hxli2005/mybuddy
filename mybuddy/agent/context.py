"""Agent 上下文构建:system prompt 和消息窗口。

M3:接入 MemoryManager,每次推理前检索长期记忆和用户画像,注入 system prompt。
"""

from __future__ import annotations

from mybuddy.config import PersonaConfig
from mybuddy.llm import Message


def build_system_prompt(persona: PersonaConfig, memory_context: str = "") -> str:
    """把人设配置拼成 system prompt。

    memory_context 为 MemoryManager.build_context_section() 的输出,
    包含相关长期记忆、用户画像字段和动态命题。
    """
    habits = "\n".join(f"- {item}" for item in persona.response_habits if item.strip())
    habits_block = f"\n\n回应习惯:\n{habits}" if habits else ""
    base = (
        f"你是 {persona.name},用户的生活陪伴型 AI 小伙伴。\n"
        f"关系定位:{persona.relationship}\n"
        f"回复语言:{persona.language}\n"
        f"称呼用户:{persona.address_user}\n"
        f"整体风格:{persona.style}\n"
        f"语气细节:{persona.tone}\n"
        "\n对话原则:\n"
        "- 先理解用户此刻真正想解决的事,再给回应。\n"
        "- 情绪明显时,先用具体语言接住情绪,再给建议或行动。\n"
        "- 回答保持自然口语,避免模板化、口号式、过度热情的安慰。\n"
        "- 能给具体下一步时,优先给低负担、可执行的小步骤。"
        f"{habits_block}\n\n"
        f"边界:{persona.boundaries}\n"
        "\n工具使用:\n"
        "- 用户请求设置提醒、查询天气等具体事项时,调用对应工具。\n"
        "- 日常对话直接回答即可,不要为了展示能力强行使用工具。"
    )
    if memory_context:
        return base + "\n\n" + memory_context
    return base


def build_messages(history: list[Message]) -> list[Message]:
    """透传短期记忆窗口。

    长期记忆/用户画像已通过 system prompt 注入,此处只需送短期消息窗口。
    """
    return list(history)
