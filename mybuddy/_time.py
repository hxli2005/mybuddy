"""时间工具。

统一提供 UTC"当下"时间。返回 naive datetime(tzinfo=None),
与 SQLAlchemy `DateTime` 列以及历史数据兼容;语义上仍是 UTC。

替代已弃用的 `datetime.utcnow()`。
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
