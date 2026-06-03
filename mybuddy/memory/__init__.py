"""自研分层记忆系统。

三层结构:
  - ShortTermMemory: 进程内滚动消息窗口
  - LongTermMemory: raw/conversations/archive 三层结构化文本存储
  - UserProfile: 核心字段(hard facts) + 动态命题集(soft claims)

MemoryManager 统一协调三层,并触发 LLM 事实抽取。
"""

from .extractor import FactExtractor, FactExtractResult
from .governance import MemoryGovernance
from .long_term import LongTermMemory
from .manager import MemoryManager
from .profile import UserProfile
from .short_term import ShortTermMemory

__all__ = [
    "FactExtractResult",
    "FactExtractor",
    "LongTermMemory",
    "MemoryGovernance",
    "MemoryManager",
    "ShortTermMemory",
    "UserProfile",
]
