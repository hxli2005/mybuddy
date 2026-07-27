"""管理员账户自动初始化：无管理员时创建 admin/123456。"""

from __future__ import annotations

import bcrypt
from sqlalchemy import Engine

from mybuddy.storage.db import session_scope
from mybuddy.storage.models import User


def seed_admin(engine: Engine) -> None:
    """若数据库中无管理员，自动创建 admin / 123456。"""
    with session_scope(engine) as s:
        existing = s.query(User).filter(User.role == "admin").first()
        if existing is not None:
            return
        user = User(
            display_name="admin",
            password_hash=bcrypt.hashpw(b"123456", bcrypt.gensalt()).decode(),
            role="admin",
            status="active",
            daily_message_limit=0,
        )
        s.add(user)
