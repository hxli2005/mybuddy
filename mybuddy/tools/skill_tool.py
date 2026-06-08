"""list_skills 工具:让 Agent 能自查手上积累了哪些 skill。

和 recall_memory 的注入方式一致:CLI 启动时 setup_skill_tool(registry) 注入一次,
工具内部通过模块级变量访问(skill registry 是重对象,不适合塞进 ToolContext)。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .context import get_skill_registry, set_context
from .registry import tool

if TYPE_CHECKING:
    from mybuddy.learning import SkillRegistry


_registry: SkillRegistry | None = None


def setup_skill_tool(registry: SkillRegistry) -> None:
    """CLI 启动时注入 SkillRegistry。"""
    global _registry
    _registry = registry
    set_context(skill_registry=registry)


@tool(
    name="list_skills",
    description=(
        "列出你(AI)已经积累的可复用做法(skill)。"
        "当你不确定某个情境下应该怎么回应、或想参考过去成功的套路时使用。"
    ),
)
def list_skills() -> str:
    """返回全部未归档 skill 的摘要。"""
    try:
        registry = get_skill_registry()
    except RuntimeError:
        registry = _registry
    if registry is None:
        return "skill registry 未初始化。"

    skills = registry.all()
    if not skills:
        return "目前还没有积累任何 skill。"

    out = []
    for s in sorted(skills, key=lambda x: x.confidence, reverse=True):
        preview = "; ".join(s.steps[:3])
        if len(s.steps) > 3:
            preview += "…"
        out.append(
            {
                "name": s.name,
                "triggers": s.triggers,
                "confidence": round(s.confidence, 2),
                "steps_preview": preview,
            }
        )
    return json.dumps(out, ensure_ascii=False)
