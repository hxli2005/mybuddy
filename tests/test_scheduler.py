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
from mybuddy.scheduler.jobs import fire_daily_greeting, fire_reminder
from mybuddy.storage import (
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
