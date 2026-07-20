"""会话管理(轻量版):chat/cbt/diary 会话的创建、查询与关闭。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine

from mybuddy._time import utcnow
from mybuddy.storage.db import session_scope
from mybuddy.storage.models import ChatSession

SESSION_TYPES = {"chat", "cbt", "diary"}


class SessionManager:
    """会话 CRUD。用户发第一条消息时自动创建 chat 会话。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_or_create_active(
        self,
        user_id: int | None,
        session_type: str = "chat",
    ) -> int:
        """返回该用户(或访客)当前活跃会话的 id,不存在则创建。"""
        if session_type not in SESSION_TYPES:
            session_type = "chat"
        with session_scope(self._engine) as s:
            q = (
                s.query(ChatSession)
                .filter(ChatSession.session_type == session_type)
                .filter(ChatSession.status == "active")
            )
            if user_id is None:
                q = q.filter(ChatSession.user_id.is_(None))
            else:
                q = q.filter(ChatSession.user_id == user_id)
            row = q.order_by(ChatSession.created_at.desc()).first()
            if row is not None:
                return row.id
            row = ChatSession(
                user_id=user_id,
                is_guest=user_id is None,
                session_type=session_type,
                status="active",
            )
            s.add(row)
            s.flush()
            return row.id

    def close(
        self,
        session_db_id: int,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        with session_scope(self._engine) as s:
            row = s.query(ChatSession).filter(ChatSession.id == session_db_id).one_or_none()
            if row is None:
                return False
            row.status = "closed"
            row.closed_at = utcnow()
            if summary:
                row.summary = summary
            if metadata:
                row.meta_json = json.dumps(metadata, ensure_ascii=False)
            return True

    def list_sessions(self, user_id: int | None, limit: int = 20) -> list[dict[str, Any]]:
        with session_scope(self._engine) as s:
            q = s.query(ChatSession)
            if user_id is None:
                q = q.filter(ChatSession.user_id.is_(None))
            else:
                q = q.filter(ChatSession.user_id == user_id)
            rows = q.order_by(ChatSession.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": r.id,
                    "session_type": r.session_type,
                    "status": r.status,
                    "summary": r.summary,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "closed_at": r.closed_at.isoformat() if r.closed_at else None,
                }
                for r in rows
            ]
