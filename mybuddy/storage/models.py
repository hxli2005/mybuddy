"""SQLAlchemy ORM 模型。

单用户单进程:对话历史、主动消息队列、桌宠事件账本、生理状态、画像字段。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
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


class PendingMessage(Base):
    """主动消息队列(早安问候 / nudge / 身体低语)。"""

    __tablename__ = "pending_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # greeting | nudge | dynamic | cowork_break | body_murmur
    source: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(Text)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class VPetEvent(Base):
    """VPet 桌宠桥接事件日志。

    这里同时记客户端触摸/回场事件和后端 drain 侧的丢弃、合并事件,供实验期
    只靠 SQL 还原当时开关与派送结果。
    """

    __tablename__ = "vpet_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_event_id: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    body_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    want_reply: Mapped[int] = mapped_column(Integer, default=0)
    escalated: Mapped[int] = mapped_column(Integer, default=0, index=True)
    replied: Mapped[int] = mapped_column(Integer, default=0)
    gate_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_flags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    server_flags_json: Mapped[str] = mapped_column(Text)
    last_emotion_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    day_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class PhysioState(Base):
    """生理曲线当前值;全库固定 id=1。"""

    __tablename__ = "physio_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    hunger: Mapped[float] = mapped_column(Float, default=70.0)
    energy: Mapped[float] = mapped_column(Float, default=70.0)
    mood: Mapped[float] = mapped_column(Float, default=60.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    woken_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_levels_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class PhysioDaily(Base):
    """按服务端本地日期持久化的生理限额与聚合账本。"""

    __tablename__ = "physio_daily"

    local_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    touch_mood_gain: Mapped[float] = mapped_column(Float, default=0.0)
    chat_mood_gain: Mapped[float] = mapped_column(Float, default=0.0)
    touch_count: Mapped[int] = mapped_column(Integer, default=0)
    murmur_count: Mapped[int] = mapped_column(Integer, default=0)
    feed_items_json: Mapped[str] = mapped_column(Text, default="[]")
    touch_memory_written: Mapped[bool] = mapped_column(Boolean, default=False)
    work_stop_speech_count: Mapped[int] = mapped_column(Integer, default=0)


class PhysioCooldown(Base):
    """身体哼唧三条曲线的持久冷却。"""

    __tablename__ = "physio_cooldowns"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_emitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProfileField(Base):
    """用户画像核心字段(hard facts):姓名/生日/偏好/禁忌等。"""

    __tablename__ = "profile_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


