"""SQLAlchemy ORM 模型。

M1 只建基础表,字段保持最小。各表将在对应里程碑开始写入:
- messages (M2 对话历史)
- reminders (M2 提醒工具)
- pending_messages (M4 主动关怀队列)
- profile_fields / profile_claims (M3 用户画像)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from mybuddy._time import utcnow as _now


class Base(DeclarativeBase):
    pass


class Message(Base):
    """对话消息历史(短期/长期原始记录)。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user/assistant/tool/system
    content: Mapped[str] = mapped_column(Text)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class Reminder(Base):
    """用户显式创建的提醒。"""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text)
    trigger_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # pending | fired | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)


class PendingMessage(Base):
    """主动消息队列(早安问候 / nudge / 到期提醒)。"""

    __tablename__ = "pending_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # greeting | nudge | reminder
    source: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(Text)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProfileField(Base):
    """用户画像核心字段(hard facts):姓名/生日/偏好/禁忌等。"""

    __tablename__ = "profile_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ProfileClaim(Base):
    """用户画像动态命题(soft claims):带置信度与证据链。"""

    __tablename__ = "profile_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Note(Base):
    """用户笔记/日记。M7 加入。

    每条笔记 SQLite 为主存,同时写入 LongTermMemory 档案层(mem_type="note");
    档案 uid 约定为 `note_{sql_id}`。
    """

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    content: Mapped[str] = mapped_column(Text)
    # 标签 JSON 数组,如 ["工作", "灵感"]
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
