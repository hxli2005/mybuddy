"""情绪感知子系统。"""

from .detector import EmotionDetector, EmotionResult
from .state import EmotionTracker
from .support import EmotionalSupport, build_emotional_support, support_system_hint

__all__ = [
    "EmotionDetector",
    "EmotionResult",
    "EmotionTracker",
    "EmotionalSupport",
    "build_emotional_support",
    "support_system_hint",
]
