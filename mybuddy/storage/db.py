"""SQLite + SQLAlchemy 引擎与会话工厂。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
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
    """创建所有表,返回 engine。幂等(create_all)。"""
    engine = make_engine(db_file)
    Base.metadata.create_all(engine)
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
