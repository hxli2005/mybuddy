"""工具注册表与内置工具。

导入本包即会触发内置工具通过 @tool 注册到全局 `ToolRegistry.default()`。

各里程碑加入的工具:
  - M2: weather / set_reminder
  - M3: recall_memory
  - M7: translate / web_search / write_note / search_notes / list_skills
"""

# 触发内置工具注册(import 副作用)
from . import notes as _notes  # noqa: F401
from . import reminder as _reminder  # noqa: F401
from . import translate as _translate  # noqa: F401
from . import weather as _weather  # noqa: F401
from . import web_search as _web_search  # noqa: F401
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
