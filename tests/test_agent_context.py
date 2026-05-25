from __future__ import annotations

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
