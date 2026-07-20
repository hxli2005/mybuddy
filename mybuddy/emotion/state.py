"""情绪窗口:跟踪最近 N 轮情绪,判断是否连续负面/连续同类。

进程内轻量状态,不持久化(重启后重新积累)。连续负面判断用于触发共情语气调整
和离线 nudge 生成。可通过 on_record 回调把每次记录交给外部持久化。
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable

from .detector import EmotionResult

logger = logging.getLogger(__name__)


class EmotionTracker:
    """滚动窗口 + 连续负面/连续同类判定。"""

    def __init__(
        self,
        window: int = 5,
        on_record: Callable[[EmotionResult], None] | None = None,
    ) -> None:
        self._results: deque[EmotionResult] = deque(maxlen=window)
        self._on_record = on_record

    def add(self, result: EmotionResult) -> None:
        self._results.append(result)
        if self._on_record is not None:
            try:
                self._on_record(result)
            except Exception:
                logger.exception("emotion on_record callback failed")

    def latest(self) -> EmotionResult | None:
        return self._results[-1] if self._results else None

    def is_consecutive_negative(self, n: int = 2) -> bool:
        """最近 n 条是否全部为 negative(strength >= 0.3)。"""
        if len(self._results) < n:
            return False
        recent = list(self._results)[-n:]
        return all(r.is_negative for r in recent)

    def consecutive_category(self, category: str, n: int = 3) -> bool:
        """最近 n 条是否全部属于同一 category(如连续 3 轮焦虑)。"""
        if len(self._results) < n:
            return False
        recent = list(self._results)[-n:]
        return all(getattr(r, "category", None) == category for r in recent)

    def reset(self) -> None:
        self._results.clear()

    def __len__(self) -> int:
        return len(self._results)
