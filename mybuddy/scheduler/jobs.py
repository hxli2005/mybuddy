"""调度器顶层 job 函数。

SQLAlchemyJobStore 用 pickle 序列化 job,因此这些函数必须是模块顶层(不能是 lambda
或类方法),且参数只能是基本类型(str / int / dict)。job 在触发时自己重建 engine /
config / provider,避免持有跨进程不可序列化的对象。

三类 job:
  - fire_reminder(reminder_id, db_file):到期提醒 → 写 pending_messages + 更新 Reminder.status
  - fire_daily_greeting(db_file, persona_name):每日早安 → 写 pending_messages
  - run_dream_job(db_file, config_path):夜间 Dream Job(五件事)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from mybuddy._time import utcnow
from mybuddy.storage import Message, PendingMessage, Reminder, enqueue, init_db, session_scope

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def fire_reminder(reminder_id: int, db_file: str) -> None:
    """到期提醒触发:写主动消息队列,更新 reminders.status=fired。"""
    engine = init_db(db_file)
    with session_scope(engine) as s:
        r = s.get(Reminder, reminder_id)
        if r is None:
            logger.warning("reminder %s 不存在,跳过", reminder_id)
            return
        if r.status != "pending":
            logger.info("reminder %s 状态为 %s,跳过", reminder_id, r.status)
            return
        content = r.content
        r.status = "fired"
        r.fired_at = utcnow()

    enqueue(
        engine,
        source="reminder",
        content=f"⏰ 提醒:{content}",
        meta={"reminder_id": reminder_id},
    )
    logger.info("reminder %s 已入队播放", reminder_id)


def fire_daily_greeting(db_file: str, persona_name: str = "小布") -> None:
    """每日早安:写一条 pending_messages。MVP 里是固定话术,
    后续可改成 LLM 动态生成(生成逻辑放在 dream.py 更合适)。
    """
    engine = init_db(db_file)
    content = f"早上好呀 ☀️ {persona_name}在这里等你,今天打算做点什么?"
    enqueue(engine, source="greeting", content=content)
    logger.info("daily greeting enqueued")


def fire_silence_followup(
    db_file: str,
    session_id: str,
    user_message_id: int,
    user_text: str,
    persona_name: str,
    min_gap_hours: int,
    cooldown_hours: int,
    max_per_day: int,
    quiet_start: str,
    quiet_end: str,
) -> None:
    """会话沉默检查:有明确由头且节流通过时入队一条 follow-up nudge。"""
    reason = _silence_contact_reason(user_text)
    if reason is None:
        logger.info("silence followup skipped: no concrete reason")
        return
    if _in_quiet_hours(datetime.now(), quiet_start, quiet_end):
        logger.info("silence followup skipped: quiet hours")
        return

    engine = init_db(db_file)
    now = utcnow()
    with session_scope(engine) as s:
        original = s.get(Message, user_message_id)
        if original is None or original.session_id != session_id or original.role != "user":
            logger.info("silence followup skipped: original user message missing")
            return

        later_user = (
            s.query(Message)
            .filter(Message.session_id == session_id)
            .filter(Message.role == "user")
            .filter(Message.id > user_message_id)
            .order_by(Message.id.desc())
            .first()
        )
        if later_user is not None:
            logger.info("silence followup skipped: user has replied")
            return

        if _count_recent_nudges(s, now - timedelta(hours=24)) >= max(0, max_per_day):
            logger.info("silence followup skipped: daily nudge limit")
            return
        if _has_recent_nudge(s, now - timedelta(hours=max(1, min_gap_hours))):
            logger.info("silence followup skipped: recent nudge gap")
            return
        if _has_unanswered_silence_nudge(
            s,
            session_id=session_id,
            since=now - timedelta(hours=max(1, cooldown_hours)),
        ):
            logger.info("silence followup skipped: previous silence nudge unanswered")
            return

    brief = _brief_text(user_text)
    content = _silence_followup_content(persona_name, brief, reason["kind"])
    enqueue(
        engine,
        source="nudge",
        content=content,
        meta={
            "origin": "silence_followup",
            "session_id": session_id,
            "user_message_id": user_message_id,
            "contact_reason": reason["contact_reason"],
            "reason_type": reason["kind"],
            "reason_score": 3,
            "attempt_index": 1,
        },
    )
    logger.info("silence followup enqueued for session %s", session_id)


def run_dream_job(db_file: str, config_path: str) -> None:
    """夜间 Dream Job 入口。委托给 learning.dream.DreamJob 执行五件事。

    由于 APScheduler 在自己的线程池跑 job,这里用 asyncio.run 起新 event loop。
    """
    # 延迟 import 避免循环依赖
    from mybuddy.config import load_config
    from mybuddy.learning.dream import DreamJob
    from mybuddy.llm import make_provider
    from mybuddy.memory import LongTermMemory, UserProfile

    cfg = load_config(config_path)
    engine = init_db(db_file)
    provider = make_provider(cfg.llm)
    ltm = LongTermMemory(
        persist_dir=cfg.paths.chroma_dir,
        embedding_model=cfg.memory.embedding_model,
    )
    profile = UserProfile(engine, ltm)
    job = DreamJob(
        engine=engine,
        config=cfg,
        provider=provider,
        ltm=ltm,
        profile=profile,
    )
    asyncio.run(job.run())
    logger.info("dream job finished")


_EXPLICIT_LATER_RE = re.compile(
    r"(等会|等一下|晚点|回头|稍后|一会儿|过会儿|明天|改天|晚上|下午|再说|继续)",
    re.I,
)
_STUCK_TASK_RE = re.compile(
    r"(卡住|写不动|做不动|动不了|拖延|开头|报告|论文|作业|汇报|复盘|任务|DDL|ddl|deadline)",
    re.I,
)


def _silence_contact_reason(text: str) -> dict[str, str] | None:
    clean = " ".join((text or "").strip().split())
    if not clean:
        return None
    if _EXPLICIT_LATER_RE.search(clean):
        return {
            "kind": "explicit_later",
            "contact_reason": f"用户提到稍后继续:{_brief_text(clean)}",
        }
    if _STUCK_TASK_RE.search(clean):
        return {
            "kind": "stuck_task",
            "contact_reason": f"用户提到任务卡住:{_brief_text(clean)}",
        }
    return None


def _silence_followup_content(persona_name: str, brief: str, kind: str) -> str:
    name = persona_name or "小布"
    if kind == "explicit_later":
        return (
            f"{name}刚想起你说过「{brief}」。不催你,只是问一句:"
            "要不要把刚才那件事接着放到桌上?"
        )
    return (
        f"{name}还记着你刚才说「{brief}」。没要催你,"
        "要不要只把最卡的那一小块丢给我?"
    )


def _count_recent_nudges(session, since: datetime) -> int:
    return (
        session.query(PendingMessage)
        .filter(PendingMessage.source == "nudge")
        .filter(PendingMessage.scheduled_at >= since)
        .count()
    )


def _has_recent_nudge(session, since: datetime) -> bool:
    return _count_recent_nudges(session, since) > 0


def _has_unanswered_silence_nudge(session, *, session_id: str, since: datetime) -> bool:
    rows = (
        session.query(PendingMessage)
        .filter(PendingMessage.source == "nudge")
        .filter(PendingMessage.scheduled_at >= since)
        .order_by(PendingMessage.scheduled_at.desc())
        .all()
    )
    row = None
    for candidate in rows:
        meta = _safe_json(candidate.meta_json)
        if meta.get("origin") == "silence_followup" and meta.get("session_id") == session_id:
            row = candidate
            break
    if row is None:
        return False
    meta = _safe_json(row.meta_json)
    marker = row.delivered_at or row.scheduled_at
    later_user = (
        session.query(Message)
        .filter(Message.session_id == session_id)
        .filter(Message.role == "user")
        .filter(Message.created_at > marker)
        .first()
    )
    return later_user is None


def _in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    start_min = _parse_hh_mm_minutes(start)
    end_min = _parse_hh_mm_minutes(end)
    if start_min is None or end_min is None:
        return False
    current = now.hour * 60 + now.minute
    if start_min == end_min:
        return False
    if start_min < end_min:
        return start_min <= current < end_min
    return current >= start_min or current < end_min


def _parse_hh_mm_minutes(value: str) -> int | None:
    try:
        hour, minute = value.split(":", 1)
        h, m = int(hour), int(minute)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def _safe_json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _brief_text(text: str, limit: int = 28) -> str:
    clean = " ".join((text or "").strip().split())
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "..."
