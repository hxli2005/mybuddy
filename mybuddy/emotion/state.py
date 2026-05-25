"""情绪窗口:跟踪最近 N 轮情绪,判断是否连续负面。

进程内轻量状态,不持久化(重启后重新积累)。连续负面判断用于触发共情语气调整
和离线 nudge 生成。
"""

from __future__ import annotations

from collections import deque

from .detector import EmotionResult


class EmotionTracker:
    """滚动窗口 + 连续负面判定。"""

    def __init__(self, window: int = 5) -> None:
        self._results: deque[EmotionResult] = deque(maxlen=window)

    def add(self, result: EmotionResult) -> None:
        self._results.append(result)

    def latest(self) -> EmotionResult | None:
        return self._results[-1] if self._results else None

    def is_consecutive_negative(self, n: int = 2) -> bool:
        """最近 n 条是否全部为 negative(strength >= 0.3)。"""
        if len(self._results) < n:
            return False
        recent = list(self._results)[-n:]
        return all(r.is_negative for r in recent)

    def reset(self) -> None:
        self._results.clear()

    def __len__(self) -> int:
        return len(self._results)
