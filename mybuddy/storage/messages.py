"""原始聊天消息日志。

SQLite messages 是聊天主日志,保存用户、助手和工具消息的原始流水。
长期记忆的 conversations/raw 只保存整理后的记忆素材和可追溯事件。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine

from .db import session_scope
from .models import Message


def append_message(
    engine: Engine,
    *,
    session_id: str,
    role: str,
    content: str,
    meta: dict[str, Any] | None = None,
) -> int:
    """写入一条原始聊天消息,返回 SQL id。"""
    with session_scope(engine) as s:
        row = Message(
            session_id=session_id,
            role=role,
            content=content,
            meta_json=json.dumps(meta or {}, ensure_ascii=False, default=str),
        )
        s.add(row)
        s.flush()
        return row.id


def list_messages(
    engine: Engine,
    *,
    limit: int = 100,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """读取最近原始聊天消息,按时间正序返回。"""
    clean_limit = max(1, min(int(limit), 500))
    with session_scope(engine) as s:
        q = s.query(Message)
        if session_id:
            q = q.filter(Message.session_id == session_id)
        rows = q.order_by(Message.created_at.desc(), Message.id.desc()).limit(clean_limit).all()
        rows.reverse()
        return [_message_payload(row) for row in rows]


def _message_payload(row: Message) -> dict[str, Any]:
    try:
        meta = json.loads(row.meta_json or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": row.id,
        "session_id": row.session_id,
        "role": row.role,
        "content": row.content,
        "meta": meta,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
    }
