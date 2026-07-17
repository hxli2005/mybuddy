"""工具注册表与内置工具。

导入本包即会触发内置工具通过 @tool 注册到全局 `ToolRegistry.default()`。

纯陪伴裁决后只剩她自己的能力:
  - recall_memory:主动检索长期记忆
  - list_skills:查看自己会什么
"""

from .context import (
    get_config,
    get_engine,
    get_long_term,
    get_provider,
    get_scheduler,
    get_skill_registry,
    set_context,
    use_context,
)
from .memory_tool import recall_memory, setup_memory_tool  # noqa: F401
from .registry import ToolEntry, ToolRegistry, tool
from .skill_tool import list_skills, setup_skill_tool  # noqa: F401

__all__ = [
    "ToolEntry",
    "ToolRegistry",
    "get_config",
    "get_engine",
    "get_long_term",
    "get_provider",
    "get_scheduler",
    "get_skill_registry",
    "set_context",
    "setup_memory_tool",
    "setup_skill_tool",
    "tool",
    "use_context",
]
