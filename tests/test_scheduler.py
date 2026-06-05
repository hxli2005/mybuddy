"""调度器集成测试。

直接调用 jobs 模块的顶层函数来验证持久化路径(写 pending_messages、更新 reminders
状态),不依赖 APScheduler 线程实际触发(那对单测太脆弱)。schedule_reminder /
list_jobs 单独验证。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from mybuddy._time import utcnow
from mybuddy.config import Config
from mybuddy.scheduler import MyBuddyScheduler
from mybuddy.scheduler.jobs import fire_daily_greeting, fire_reminder, fire_silence_followup
from mybuddy.storage import (
    Message,
    Reminder,
    drain_pending,
    init_db,
    list_undelivered,
    session_scope,
)


def _make_cfg(db_file: str) -> Config:
    cfg = Config()
    cfg.paths.db_file = db_file
    return cfg


def test_fire_reminder_updates_status_and_enqueues(tmp_path) -> None:
    db_file = str(tmp_path / "s.db")
    engine = init_db(db_file)
    with session_scope(engine) as s:
        r = Reminder(content="该喝水了", trigger_at=utcnow(), status="pending")
        s.add(r)
        s.flush()
        rid = r.id

    fire_reminder(rid, db_file)

    with session_scope(engine) as s:
        updated = s.get(Reminder, rid)
        assert updated.status == "fired"
        assert updated.fired_at is not None

    pending = list_undelivered(engine)
    assert len(pending) == 1
    assert "该喝水了" in pending[0]["content"]
    assert pending[0]["source"] == "reminder"


def test_fire_reminder_idempotent(tmp_path) -> None:
    """已 fired 的 reminder 再次触发不应重复入队。"""
    db_file = str(tmp_path / "s.db")
    engine = init_db(db_file)
    with session_scope(engine) as s:
        r = Reminder(
            content="重复触发",
            trigger_at=utcnow(),
            status="fired",
            fired_at=utcnow(),
        )
        s.add(r)
        s.flush()
        rid = r.id

    fire_reminder(rid, db_file)
    assert list_undelivered(engine) == []


def test_fire_daily_greeting(tmp_path) -> None:
    db_file = str(tmp_path / "s.db")
    engine = init_db(db_file)
    fire_daily_greeting(db_file, persona_name="小布")

    drained = drain_pending(engine)
    assert len(drained) == 1
    assert drained[0]["source"] == "greeting"
    assert "小布" in drained[0]["content"]


def test_fire_silence_followup_enqueues_when_user_stays_silent(tmp_path) -> None:
    db_file = str(tmp_path / "silence.db")
    engine = init_db(db_file)
    with session_scope(engine) as s:
        msg = Message(session_id="s1", role="user", content="我晚点继续写报告开头")
        s.add(msg)
        s.flush()
        msg_id = msg.id

    fire_silence_followup(
        db_file,
        "s1",
        msg_id,
        "我晚点继续写报告开头",
        "小布",
        6,
        48,
        1,
        "00:00",
        "00:00",
    )

    pending = list_undelivered(engine)
    assert len(pending) == 1
    assert pending[0]["source"] == "nudge"
    assert "晚点继续写报告开头" in pending[0]["content"]


def test_fire_silence_followup_skips_if_user_replied(tmp_path) -> None:
    db_file = str(tmp_path / "silence_skip.db")
    engine = init_db(db_file)
    with session_scope(engine) as s:
        msg = Message(session_id="s1", role="user", content="我晚点继续")
        s.add(msg)
        s.flush()
        msg_id = msg.id
        s.add(Message(session_id="s1", role="assistant", content="好。"))
        s.add(Message(session_id="s1", role="user", content="我回来了"))

    fire_silence_followup(
        db_file,
        "s1",
        msg_id,
        "我晚点继续",
        "小布",
        6,
        48,
        1,
        "00:00",
        "00:00",
    )

    assert list_undelivered(engine) == []


def test_fire_silence_followup_skips_without_concrete_reason(tmp_path) -> None:
    db_file = str(tmp_path / "silence_no_reason.db")
    engine = init_db(db_file)
    with session_scope(engine) as s:
        msg = Message(session_id="s1", role="user", content="今天有点累")
        s.add(msg)
        s.flush()
        msg_id = msg.id

    fire_silence_followup(
        db_file,
        "s1",
        msg_id,
        "今天有点累",
        "小布",
        6,
        48,
        1,
        "00:00",
        "00:00",
    )

    assert list_undelivered(engine) == []


@pytest.mark.asyncio
async def test_scheduler_schedule_reminder_registers_job(tmp_path) -> None:
    """schedule_reminder 真的把 job 写进 jobstore。"""
    db_file = str(tmp_path / "s.db")
    init_db(db_file)
    cfg = _make_cfg(db_file)

    scheduler = MyBuddyScheduler(cfg)
    scheduler.start()
    try:
        trigger = utcnow() + timedelta(hours=1)
        scheduler.schedule_reminder(42, trigger)

        jobs = scheduler.list_jobs()
        assert any(j["id"] == "reminder_42" for j in jobs)

        # 幂等:再次注册替换
        scheduler.schedule_reminder(42, trigger + timedelta(minutes=5))
        jobs2 = scheduler.list_jobs()
        assert len([j for j in jobs2 if j["id"] == "reminder_42"]) == 1

        # 取消
        assert scheduler.cancel_reminder(42)
        jobs3 = scheduler.list_jobs()
        assert not any(j["id"] == "reminder_42" for j in jobs3)
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_scheduler_schedule_silence_followup_replaces_session_job(tmp_path) -> None:
    db_file = str(tmp_path / "s.db")
    init_db(db_file)
    cfg = _make_cfg(db_file)

    scheduler = MyBuddyScheduler(cfg)
    scheduler.start()
    try:
        trigger = utcnow() + timedelta(hours=1)
        scheduler.schedule_silence_followup(
            session_id="abc",
            user_message_id=1,
            user_text="晚点继续",
            run_at=trigger,
        )
        scheduler.schedule_silence_followup(
            session_id="abc",
            user_message_id=2,
            user_text="晚点继续",
            run_at=trigger + timedelta(minutes=5),
        )

        jobs = scheduler.list_jobs()
        matching = [j for j in jobs if j["id"] == "silence_followup_abc"]
        assert len(matching) == 1
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_scheduler_cron_jobs(tmp_path) -> None:
    """每日早安和 dream job 注册后都能在 list_jobs 里看到。"""
    db_file = str(tmp_path / "s.db")
    init_db(db_file)
    cfg = _make_cfg(db_file)

    scheduler = MyBuddyScheduler(cfg)
    scheduler.start()
    try:
        scheduler.schedule_daily_greeting("09:17")
        scheduler.schedule_dream_job("02:23", config_path="config.yaml")

        jobs = {j["id"]: j for j in scheduler.list_jobs()}
        assert "daily_greeting" in jobs
        assert "dream_job" in jobs
        assert jobs["daily_greeting"]["next_run"] is not None
    finally:
        scheduler.shutdown()
