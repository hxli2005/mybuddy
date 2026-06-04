from __future__ import annotations

from datetime import datetime

from mybuddy.agent.context import build_system_prompt
from mybuddy.config import PersonaConfig


def test_build_system_prompt_uses_detailed_persona_fields() -> None:
    persona = PersonaConfig(
        name="阿澈",
        style="冷静、具体",
        language="中文",
        relationship="像一个长期合作的项目伙伴",
        tone="直接,但先接住压力",
        boundaries="不替代专业咨询",
        response_habits=["先复述用户目标", "给出一个低负担下一步"],
        address_user="你",
    )

    prompt = build_system_prompt(persona)

    assert "你是 阿澈" in prompt
    assert "像一个长期合作的项目伙伴" in prompt
    assert "直接,但先接住压力" in prompt
    assert "先复述用户目标" in prompt
    assert "不替代专业咨询" in prompt


def test_build_system_prompt_includes_current_time_context() -> None:
    persona = PersonaConfig(name="小布")

    prompt = build_system_prompt(persona, now=datetime(2026, 6, 4, 14, 30))

    assert "当前时间:" in prompt
    assert "日期:2026-06-04" in prompt
    assert "时间:14:30" in prompt
    assert "时区:" in prompt
    assert "星期:周四" in prompt
