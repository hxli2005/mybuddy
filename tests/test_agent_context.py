from __future__ import annotations

from datetime import datetime

from mybuddy.agent.context import build_system_prompt
from mybuddy.config import PersonaConfig


def test_build_system_prompt_uses_persona_fields() -> None:
    persona = PersonaConfig(
        name="阿澈",
        language="中文",
        tone="直接,但先接住压力",
        boundaries="不替代专业咨询,必要时提醒求助专业资源",
        address_user="你",
    )

    prompt = build_system_prompt(persona)

    assert "你是 阿澈" in prompt
    assert "直接,但先接住压力" in prompt
    assert "不替代专业咨询" in prompt
    assert "回复原则" in prompt
    assert "安全规则" in prompt
    assert "工具使用" in prompt


def test_build_system_prompt_includes_current_time_context() -> None:
    persona = PersonaConfig(name="小布")

    prompt = build_system_prompt(persona, now=datetime(2026, 6, 4, 14, 30))

    assert "当前时间:" in prompt
    assert "日期:2026-06-04" in prompt
    assert "时间:14:30" in prompt
    assert "时区:" in prompt
    assert "星期:四" in prompt


def test_default_persona_is_mental_health_companion() -> None:
    prompt = build_system_prompt(PersonaConfig())

    assert "你是 小布" in prompt
    assert "心理健康陪伴者" in prompt
    assert "不是治疗师" in prompt
    assert "自伤或自杀" in prompt
    assert "能力边界" in prompt
    # 陪伴者不应自带亲密关系身份标签。
    assert "男友" not in prompt
    assert "男朋友" not in prompt
    assert "伴侣" not in prompt
