"""SQLite + SQLAlchemy 引擎与会话工厂。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from mybuddy._time import utcnow

from .models import Base


def make_engine(db_file: str) -> Engine:
    """创建 SQLite engine。确保父目录存在。"""
    p = Path(db_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{p.as_posix()}"
    return create_engine(url, future=True)


def init_db(db_file: str) -> Engine:
    """创建所有表,返回 engine。幂等(create_all)。"""
    engine = make_engine(db_file)
    Base.metadata.create_all(engine)
    _ensure_profile_claim_columns(engine)
    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """提供一个自动 commit/rollback 的 Session 上下文。"""
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ensure_profile_claim_columns(engine: Engine) -> None:
    """为旧 SQLite 库补齐动态命题生命周期字段。"""
    inspector = inspect(engine)
    if "profile_claims" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("profile_claims")}
    statements = {
        "status": "ALTER TABLE profile_claims ADD COLUMN status VARCHAR(16) DEFAULT 'active'",
        "category": "ALTER TABLE profile_claims ADD COLUMN category VARCHAR(32) DEFAULT 'general'",
        "evidence_count": "ALTER TABLE profile_claims ADD COLUMN evidence_count INTEGER DEFAULT 0",
        "evidence_days_json": "ALTER TABLE profile_claims ADD COLUMN evidence_days_json TEXT",
        "conflict_ids_json": "ALTER TABLE profile_claims ADD COLUMN conflict_ids_json TEXT",
        "first_seen_at": "ALTER TABLE profile_claims ADD COLUMN first_seen_at DATETIME",
        "last_seen_at": "ALTER TABLE profile_claims ADD COLUMN last_seen_at DATETIME",
        "promoted_memory_id": "ALTER TABLE profile_claims ADD COLUMN promoted_memory_id VARCHAR(128)",
        "promotion_checked_at": "ALTER TABLE profile_claims ADD COLUMN promotion_checked_at DATETIME",
    }
    with engine.begin() as conn:
        for column, statement in statements.items():
            if column not in existing:
                conn.execute(text(statement))
        now_expr = "COALESCE(updated_at, CURRENT_TIMESTAMP)"
        conn.execute(
            text(
                "UPDATE profile_claims SET "
                "status = COALESCE(status, 'active'), "
                "category = COALESCE(category, 'general'), "
                "evidence_count = COALESCE(evidence_count, 0), "
                f"first_seen_at = COALESCE(first_seen_at, {now_expr}), "
                f"last_seen_at = COALESCE(last_seen_at, {now_expr})"
            )
        )
        rows = conn.execute(
            text(
                "SELECT id, evidence_ids_json, evidence_count, evidence_days_json, updated_at "
                "FROM profile_claims"
            )
        ).mappings()
        for row in rows:
            evidence_ids = _json_list(row.get("evidence_ids_json"))
            if not evidence_ids:
                continue
            updates: dict[str, object] = {}
            if not row.get("evidence_count"):
                updates["evidence_count"] = len(evidence_ids)
            if not _json_list(row.get("evidence_days_json")):
                updates["evidence_days_json"] = json.dumps(
                    [_date_string(row.get("updated_at"))],
                    ensure_ascii=False,
                )
            if not updates:
                continue
            updates["id"] = row["id"]
            assignments = ", ".join(f"{key} = :{key}" for key in updates if key != "id")
            conn.execute(text(f"UPDATE profile_claims SET {assignments} WHERE id = :id"), updates)


def _json_list(value: object) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _date_string(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value or "").strip()
    if not text:
        return utcnow().date().isoformat()
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return text[:10]
