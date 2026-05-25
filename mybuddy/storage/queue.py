"""pending_messages 主动消息队列。

所有"调度器触发、等 CLI 播出"的消息统一入这张表:
  - 到期提醒(source=reminder)
  - 每日早安(source=greeting)
  - Dream Job nudge(source=nudge)

CLI 每次对话开始前调用 drain_pending() 取出未派送的条目,打印后 mark_delivered。

meta 以 JSON 形式存 meta_json 字段(例如 reminder 条目 meta={"reminder_id": 3})。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy import Engine

from mybuddy._time import utcnow
from mybuddy.storage import PendingMessage, session_scope


def enqueue(
    engine: Engine,
    *,
    source: str,
    content: str,
    meta: dict[str, Any] | None = None,
    scheduled_at=None,
) -> int:
    """写一条主动消息,返回 id。source ∈ {reminder, greeting, nudge}。"""
    meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
    with session_scope(engine) as s:
        pm = PendingMessage(
            source=source,
            content=content,
            scheduled_at=scheduled_at or utcnow(),
            meta_json=meta_json,
        )
        s.add(pm)
        s.flush()
        return pm.id


def drain_pending(engine: Engine, *, limit: int = 20) -> list[dict[str, Any]]:
    """取出未派送的消息(按 scheduled_at 升序),**并立即标记为已派送**。

    返回纯 dict 列表(解绑 ORM),CLI 直接打印。并发场景下单 CLI 进程足够,
    不做行级锁;多进程时需要加 `SELECT ... FOR UPDATE` 或乐观锁。
    """
    now = utcnow()
    out: list[dict[str, Any]] = []
    with session_scope(engine) as s:
        q = (
            s.query(PendingMessage)
            .filter(PendingMessage.delivered_at.is_(None))
            .filter(PendingMessage.scheduled_at <= now)
            .order_by(PendingMessage.scheduled_at.asc())
            .limit(limit)
        )
        rows = q.all()
        for pm in rows:
            out.append(
                {
                    "id": pm.id,
                    "source": pm.source,
                    "content": pm.content,
                    "scheduled_at": pm.scheduled_at.isoformat(timespec="seconds"),
                    "meta": json.loads(pm.meta_json) if pm.meta_json else {},
                }
            )
            pm.delivered_at = now
    return out


def list_undelivered(engine: Engine) -> list[dict[str, Any]]:
    """查看所有未派送消息(不标记),调试用。"""
    with session_scope(engine) as s:
        rows = (
            s.query(PendingMessage)
            .filter(PendingMessage.delivered_at.is_(None))
            .order_by(PendingMessage.scheduled_at.asc())
            .all()
        )
        return [
            {
                "id": pm.id,
                "source": pm.source,
                "content": pm.content,
                "scheduled_at": pm.scheduled_at.isoformat(timespec="seconds"),
            }
            for pm in rows
        ]
