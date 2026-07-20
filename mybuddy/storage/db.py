"""SQLite + SQLAlchemy 引擎与会话工厂。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def make_engine(db_file: str) -> Engine:
    """创建 SQLite engine。确保父目录存在。

    启用 WAL + busy_timeout:后台事实抽取 / dream job 现在会和对话轮次并发写同一库,
    WAL 允许读写并行、busy_timeout 让短暂写锁等待而非立即 "database is locked"。
    """
    p = Path(db_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{p.as_posix()}"
    engine = create_engine(url, future=True, connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()

    return engine


def init_db(db_file: str) -> Engine:
    """创建所有表并新增缺失列,返回 engine。幂等。"""
    engine = make_engine(db_file)
    Base.metadata.create_all(engine)
    _migrate_columns(engine)
    return engine


def _migrate_columns(engine: Engine) -> None:
    """为已有表新增缺失列(仅 SQLite)。"""
    with engine.connect() as conn:
        user_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        if "password_hash" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(256)"))
        if "is_guest" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_guest BOOLEAN DEFAULT 0"))

        msg_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(messages)")).fetchall()}
        if "user_id" not in msg_cols:
            conn.execute(text("ALTER TABLE messages ADD COLUMN user_id INTEGER"))

        conn.commit()


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
