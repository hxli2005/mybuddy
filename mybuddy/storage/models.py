"""SQLAlchemy ORM 模型。

M1 只建基础表,字段保持最小。各表将在对应里程碑开始写入:
- messages (M2 对话历史)
- reminders (M2 提醒工具)
- pending_messages (M4 主动关怀队列)
- profile_fields (M3 用户画像核心字段)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
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


class Message(Base):
    """对话消息历史(短期/长期原始记录)。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
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


class MoodRecord(Base):
    """情绪记录(每次对话自动记录 + 手动签到)。"""

    __tablename__ = "mood_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    mood_label: Mapped[str] = mapped_column(String(16), default="neutral")
    mood_score: Mapped[int] = mapped_column(Integer, default=5)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="chat")  # chat | checkin
    emotion_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class SafetyEvent(Base):
    """安全事件日志(危机检测、内容审核等)。"""

    __tablename__ = "safety_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)  # crisis_detected | content_flagged
    severity: Mapped[str] = mapped_column(String(16), default="low")  # low | medium | high | critical
    details: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    action_taken: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class AssessmentDimension(Base):
    """无感化心理评估维度追踪(每个维度独立一行)。"""

    __tablename__ = "assessment_dimensions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assessment_type: Mapped[str] = mapped_column(String(8), index=True)  # phq9 | gad7
    dimension_index: Mapped[int] = mapped_column(Integer)  # 0-8 (phq9) or 0-6 (gad7)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-3 Likert
    source_conversation: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: 用户原始回答+AI评分推理
    status: Mapped[str] = mapped_column(String(16), default="unasked", index=True)  # unasked | asked | answered | scored
    asked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class CbtEvent(Base):
    """CBT 技巧使用追踪(无感记录)。"""

    __tablename__ = "cbt_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    technique_type: Mapped[str] = mapped_column(String(32))  # cognitive_restructuring | behavioral_activation | worry_time | gratitude | grounding
    trigger_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    completed: Mapped[bool] = mapped_column(default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class AssessmentCycle(Base):
    """完成的评估周期归档(支撑历史趋势查询)。"""

    __tablename__ = "assessment_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assessment_type: Mapped[str] = mapped_column(String(8), index=True)  # phq9 | gad7
    total_score: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(16), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class ChatSession(Base):
    """会话管理(支持访客)。"""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    session_type: Mapped[str] = mapped_column(String(16), default="chat")  # chat | cbt | diary
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | closed
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
