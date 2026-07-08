"""VPet 事件遥测写入与结果回填。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from mybuddy._time import utcnow
from mybuddy.storage.db import session_scope
from mybuddy.storage.models import Message, VPetEvent

if TYPE_CHECKING:
    from sqlalchemy import Engine


def record_vpet_event(
    engine: Engine,
    *,
    event: str,
    count: int = 1,
    body_state: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    want_reply: bool = False,
    client_event_id: str | None = None,
    client_flags: dict[str, Any] | None = None,
    server_flags: dict[str, Any],
    last_emotion_label: str | None = None,
    day_index: int | None = None,
) -> tuple[dict[str, Any], bool]:
    """记录一条 VPet 事件;client_event_id 命中时返回旧行和 created=False。"""
    clean_client_event_id = (client_event_id or "").strip() or None
    with session_scope(engine) as s:
        if clean_client_event_id is not None:
            existing = (
                s.query(VPetEvent)
                .filter(VPetEvent.client_event_id == clean_client_event_id)
                .one_or_none()
            )
            if existing is not None:
                if count > existing.count:
                    existing.count = count
                if body_state:
                    existing.body_state_json = _dump_json_or_none(body_state)
                if context:
                    existing.context_json = _dump_json_or_none(context)
                if client_flags:
                    existing.client_flags_json = _dump_json_or_none(client_flags)
                s.flush()
                return _event_payload(existing), False

        row = VPetEvent(
            client_event_id=clean_client_event_id,
            event=event,
            count=count,
            body_state_json=_dump_json_or_none(body_state),
            context_json=_dump_json_or_none(context),
            want_reply=1 if want_reply else 0,
            escalated=0,
            replied=0,
            client_flags_json=_dump_json_or_none(client_flags),
            server_flags_json=json.dumps(server_flags, ensure_ascii=False, default=str),
            last_emotion_label=last_emotion_label,
            day_index=day_index,
        )
        s.add(row)
        s.flush()
        return _event_payload(row), True


def mark_vpet_event_result(
    engine: Engine,
    event_id: int,
    *,
    escalated: bool = False,
    replied: bool = False,
    gate_reason: str | None = None,
    turn_id: str | None = None,
    message_id: int | None = None,
) -> dict[str, Any] | None:
    """回填升格/拒绝结果。"""
    with session_scope(engine) as s:
        row = s.query(VPetEvent).filter(VPetEvent.id == event_id).one_or_none()
        if row is None:
            return None
        row.escalated = 1 if escalated else 0
        row.replied = 1 if replied else 0
        row.gate_reason = gate_reason
        row.turn_id = turn_id
        row.message_id = message_id
        s.flush()
        return _event_payload(row)


def count_vpet_escalations_today(engine: Engine) -> int:
    """统计 UTC 当日已批准升格次数。"""
    today = utcnow().date()
    start = datetime(today.year, today.month, today.day)
    with session_scope(engine) as s:
        return (
            s.query(VPetEvent)
            .filter(VPetEvent.created_at >= start)
            .filter(VPetEvent.escalated == 1)
            .count()
        )


def get_message_content(engine: Engine, message_id: int | None) -> str:
    if message_id is None:
        return ""
    with session_scope(engine) as s:
        row = s.query(Message).filter(Message.id == message_id).one_or_none()
        return row.content if row is not None else ""


def latest_assistant_message_id(engine: Engine, *, turn_id: str) -> int | None:
    with session_scope(engine) as s:
        rows = (
            s.query(Message)
            .filter(Message.role == "assistant")
            .order_by(Message.id.desc())
            .limit(20)
            .all()
        )
        for row in rows:
            try:
                meta = json.loads(row.meta_json or "{}")
            except json.JSONDecodeError:
                meta = {}
            if isinstance(meta, dict) and meta.get("turn_id") == turn_id:
                return row.id
        return None


def _dump_json_or_none(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _event_payload(row: VPetEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "client_event_id": row.client_event_id,
        "event": row.event,
        "count": row.count,
        "body_state": _load_json(row.body_state_json),
        "context": _load_json(row.context_json),
        "want_reply": bool(row.want_reply),
        "escalated": bool(row.escalated),
        "replied": bool(row.replied),
        "gate_reason": row.gate_reason,
        "turn_id": row.turn_id,
        "message_id": row.message_id,
        "client_flags": _load_json(row.client_flags_json),
        "server_flags": _load_json(row.server_flags_json),
        "last_emotion_label": row.last_emotion_label,
        "day_index": row.day_index,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
    }
