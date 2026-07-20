"""CBT使用追踪器:记录技巧使用、冷却管理、持久化。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine

from mybuddy._time import utcnow
from mybuddy.storage.db import session_scope
from mybuddy.storage.models import CbtEvent

COOLDOWN_HOURS = 24


class CbtTracker:
    """CBT技巧追踪(登录用户持久化,访客仅内存)。"""

    def __init__(self, engine: Engine | None = None, user_id: int | None = None):
        self._engine = engine
        self._user_id = user_id
        self._memory: dict[str, datetime] = {}  # 访客内存追踪

    def is_on_cooldown(self, technique: str) -> bool:
        """检查技巧是否在冷却中。"""
        if self._engine and self._user_id:
            with session_scope(self._engine) as s:
                row = (
                    s.query(CbtEvent)
                    .filter(CbtEvent.user_id == self._user_id)
                    .filter(CbtEvent.technique_type == technique)
                    .order_by(CbtEvent.created_at.desc())
                    .first()
                )
                if row and row.created_at:
                    return (utcnow() - row.created_at) < timedelta(hours=COOLDOWN_HOURS)
                return False
        last = self._memory.get(technique)
        if last is None:
            return False
        return (datetime.now() - last) < timedelta(hours=COOLDOWN_HOURS)

    def record(self, technique: str, context: dict | None = None, completed: bool = False) -> None:
        """记录一次技巧使用。"""
        import json
        ctx_json = json.dumps(context, ensure_ascii=False) if context else None

        if self._engine and self._user_id:
            with session_scope(self._engine) as s:
                s.add(CbtEvent(
                    user_id=self._user_id,
                    technique_type=technique,
                    trigger_context=ctx_json,
                    completed=completed,
                    completed_at=utcnow() if completed else None,
                ))
        else:
            self._memory[technique] = datetime.now()

    def get_events(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近的技巧使用记录。"""
        if self._engine and self._user_id:
            with session_scope(self._engine) as s:
                rows = (
                    s.query(CbtEvent)
                    .filter(CbtEvent.user_id == self._user_id)
                    .order_by(CbtEvent.created_at.desc())
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        "id": r.id,
                        "technique_type": r.technique_type,
                        "completed": r.completed,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in rows
                ]
        return []
