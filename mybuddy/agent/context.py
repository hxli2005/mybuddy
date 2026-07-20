"""Agent 上下文构建:system prompt 和消息窗口。"""

from __future__ import annotations

from datetime import datetime

from mybuddy.config import PersonaConfig
from mybuddy.llm import Message
from mybuddy.safety import CapabilityGuard


def build_system_prompt(
    persona: PersonaConfig,
    memory_context: str = "",
    *,
    now: datetime | None = None,
    assessment_hint: str = "",
    cbt_hint: str = "",
) -> str:
    """构建 system prompt。memory_context 包含记忆检索结果和场景线索。"""
    time_block = _time_block(now)
    base = (
        f"你是 {persona.name}。\n"
        "\n角色:\n"
        "- 一个懂心理学、会接得住情绪的温暖陪伴者,用日常语言帮用户理解和调节情绪。\n"
        f"- 语言:{persona.language}; 称呼:{persona.address_user}(默认用\"你\",除非用户明确要求用特定称呼)\n"
        f"- 口吻:{persona.tone}\n"
        f"- 边界:{persona.boundaries}\n"
        f"{time_block}"
        f"\n{CapabilityGuard.system_prompt_section()}\n"
        "\n回复原则:\n"
        "- 像一个懂心理学、会接得住情绪的温暖朋友,不是随叫随到的客服。\n"
        "- 不要暴露内部字段名或向用户逐条报告你检测到了什么(情绪、风险等)。\n"
        "- 不要套用固定的'我理解你/你现在感到/可以试试'三段式;先找这一刻的具体由头。\n"
        "- 用低压、具体、短的下一步承接,像朋友聊天而不是做心理评估。\n"
        "\n安全规则:\n"
        "- 用户表达自伤或自杀意图时:不害怕、不讲大道理、不试图独自解决。先表达关心,然后温和地建议联系信任的人或专业热线。\n"
        "- 用户询问诊断、药物或治疗方案时:明确表示这不是你能做的,建议咨询专业医生或心理咨询师。\n"
        "\n工具使用:\n"
        "- 用户请求设置提醒、查询天气等具体事项时,调用对应工具。\n"
        "- 涉及新闻、最新事实或其他时效信息时,优先依据外部资料检索段;没有资料就不要装作确认。\n"
        "- 日常对话直接回答即可,不要为了展示能力强行使用工具。"
    )
    if assessment_hint:
        base += f"\n\n{assessment_hint}"
    if cbt_hint:
        base += f"\n\n{cbt_hint}"
    if memory_context:
        return base + "\n\n" + memory_context
    return base


def build_messages(history: list[Message]) -> list[Message]:
    """构建消息列表(透传短期记忆窗口)。"""
    return list(history)


def _time_block(now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now()
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    tz_name = now.astimezone().tzname() or ""
    return (
        "\n当前时间:\n"
        f"- 日期:{now.strftime('%Y-%m-%d')}\n"
        f"- 时间:{now.strftime('%H:%M')}\n"
        f"- 时区:{tz_name}\n"
        f"- 星期:{weekdays[now.weekday()]}\n"
    )
