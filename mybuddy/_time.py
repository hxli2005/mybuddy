"""时间工具。

统一提供 UTC"当下"时间。返回 naive datetime(tzinfo=None),
与 SQLAlchemy `DateTime` 列以及历史数据兼容;语义上仍是 UTC。

替代已弃用的 `datetime.utcnow()`。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta


def _read_offset() -> int:
    raw = os.environ.get("MYBUDDY_TIME_OFFSET_MINUTES", "0").strip() or "0"
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("MYBUDDY_TIME_OFFSET_MINUTES 必须是整数") from exc


_offset_minutes = _read_offset()


def configure_time_offset(*, acceptance_mode: bool) -> int:
    """在进程启动阶段读取并冻结验收时钟偏移。"""
    global _offset_minutes
    value = _read_offset()
    if value and not acceptance_mode:
        raise RuntimeError(
            "生产模式禁止 MYBUDDY_TIME_OFFSET_MINUTES 非零;"
            "仅可在 vpet.acceptance_mode=true 时使用"
        )
    _offset_minutes = value
    return value


def time_offset_minutes() -> int:
    return _offset_minutes


def utcnow() -> datetime:
    return (datetime.now(UTC) + timedelta(minutes=_offset_minutes)).replace(tzinfo=None)


def localnow() -> datetime:
    """返回带本机时区的模拟服务端时间。"""
    return datetime.now().astimezone() + timedelta(minutes=_offset_minutes)
