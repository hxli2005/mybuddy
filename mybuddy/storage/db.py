"""SQLite + SQLAlchemy 引擎与会话工厂。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

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
