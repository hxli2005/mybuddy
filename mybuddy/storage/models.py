"""SQLAlchemy ORM 模型。

M1 只建基础表,字段保持最小。各表将在对应里程碑开始写入:
- messages (M2 对话历史)
- reminders (M2 提醒工具)
- pending_messages (M4 主动关怀队列)
- profile_fields / profile_claims (M3 用户画像)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from mybuddy._time import utcnow as _now


class Base(DeclarativeBase):
    pass


class User(Base):
    """MyBuddy 内部用户。

    外部渠道(QQ/Web/App)都映射到这里。当前小规模测试阶段用一套内部用户
    统一承载配额、状态和每用户运行目录。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    daily_message_limit: Mapped[int] = mapped_column(Integer, default=30)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ExternalAccount(Base):
    """外部渠道账号与内部用户的绑定。"""

    __tablename__ = "external_accounts"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_external_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class InboundEvent(Base):
    """外部渠道入站事件去重记录。"""

    __tablename__ = "inbound_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_inbound_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    event_id: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(16), default="processing", index=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserUsage(Base):
    """用户按天、按渠道的消息额度计数。"""

    __tablename__ = "user_usage"
    __table_args__ = (UniqueConstraint("user_id", "day", "source", name="uq_user_usage_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    source: Mapped[str] = mapped_column(String(32), default="chat", index=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class UserPersona(Base):
    """用户级 AI 人格配置覆盖。

    全局 config.yaml 里的 persona 仍作为默认值。这里保存单个内部用户的完整
    PersonaConfig JSON,让 QQ/Web/App 都能按同一个 user_id 解析人格。
    """

    __tablename__ = "user_personas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    persona_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, index=True)


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
    # candidate | active | stable | promoted | stale | refuted | archived
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # general | fact | preference | relationship | emotion_pattern | task | boundary
    category: Mapped[str] = mapped_column(String(32), default="general", index=True)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_days_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    promoted_memory_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    promotion_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
