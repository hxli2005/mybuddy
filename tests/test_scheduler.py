"""调度器集成测试。

直接调用 jobs 模块的顶层函数来验证持久化路径(写 pending_messages),
不依赖 APScheduler 线程实际触发(那对单测太脆弱)。list_jobs 单独验证。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from mybuddy._time import utcnow
from mybuddy.config import Config
from mybuddy.scheduler import MyBuddyScheduler
from mybuddy.scheduler.core import _scheduler_cron_time, _scheduler_run_date
from mybuddy.scheduler.jobs import (
    fire_cowork_break,
    fire_daily_greeting,
    fire_silence_followup,
)
from mybuddy.storage import (
    Message,
    drain_pending,
    init_db,
    list_undelivered,
    record_vpet_event,
    session_scope,
)


def _make_cfg(db_file: str) -> Config:
    cfg = Config()
    cfg.paths.db_file = db_file
    return cfg


def test_fire_daily_greeting(tmp_path) -> None:
    db_file = str(tmp_path / "s.db")
    engine = init_db(db_file)
    fire_daily_greeting(db_file, persona_name="小布")

    drained = drain_pending(engine)
    assert len(drained) == 1
    assert drained[0]["source"] == "greeting"
    assert "小布" in drained[0]["content"]


def test_fire_cowork_break_only_for_open_session(tmp_path) -> None:
    db_file = str(tmp_path / "cowork.db")
    engine = init_db(db_file)
    record_vpet_event(
        engine,
        event="work_start",
        context={"session_id": "work-1"},
        server_flags={},
    )

    fire_cowork_break(db_file, "work-1")
    assert [item["source"] for item in list_undelivered(engine)] == ["cowork_break"]

    record_vpet_event(
        engine,
        event="work_stop",
        context={"session_id": "work-1"},
        server_flags={},
    )
    fire_cowork_break(db_file, "work-1")
    assert len(list_undelivered(engine)) == 1


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
async def test_scheduler_cowork_job_is_persistent_and_cancelable(tmp_path) -> None:
    db_file = str(tmp_path / "cowork-job.db")
    init_db(db_file)
    scheduler = MyBuddyScheduler(_make_cfg(db_file))
    scheduler.start()
    try:
        scheduler.schedule_cowork_break(
            session_id="work-1",
            run_at=utcnow() + timedelta(hours=1),
        )
        assert any(job["id"] == "vpet_cowork:work-1" for job in scheduler.list_jobs())
        assert scheduler.cancel_cowork_break("work-1") is True
        assert not any(job["id"] == "vpet_cowork:work-1" for job in scheduler.list_jobs())
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


def test_scheduler_converts_simulated_clock_to_real_trigger(monkeypatch) -> None:
    from datetime import UTC, datetime

    monkeypatch.setattr("mybuddy.scheduler.core.time_offset_minutes", lambda: 120)

    assert _scheduler_run_date(datetime(2026, 7, 11, 10, 0)) == datetime(
        2026, 7, 11, 8, 0, tzinfo=UTC
    )
    assert _scheduler_cron_time(1, 0) == (23, 0)
