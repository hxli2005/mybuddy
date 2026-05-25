"""短期记忆:固定容量的滚动消息窗口。

Agent 每轮对话把 user/assistant/tool 消息写入,超出容量自动丢弃最旧的。
M3 从 agent/core.py 提取为独立模块,便于测试和替换。
"""

from __future__ import annotations

from collections import deque

from mybuddy.llm import Message


class ShortTermMemory:
    """进程内滚动消息窗口。线程不安全,单 CLI 进程足够。"""

    def __init__(self, capacity: int = 20) -> None:
        self._messages: deque[Message] = deque(maxlen=capacity)

    def add(self, msg: Message) -> None:
        self._messages.append(msg)

    def get_all(self) -> list[Message]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return f"ShortTermMemory(len={len(self)}, cap={self._messages.maxlen})"
