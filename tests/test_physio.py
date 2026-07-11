from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from mybuddy._time import configure_time_offset
from mybuddy.body import PhysioBusyError, PhysioEngine, enqueue_crossed_murmurs
from mybuddy.config import Config, PhysioConfig
from mybuddy.storage import (
    PhysioDaily,
    PhysioState,
    VPetEvent,
    init_db,
    list_undelivered,
    session_scope,
)


def _engine(tmp_path, **updates) -> PhysioEngine:
    values = {
        "enabled": True,
        "sleep_start": "00:00",
        "sleep_end": "00:00",  # 相同表示测试里的全天清醒
        **updates,
    }
    config = PhysioConfig(**values)
    return PhysioEngine(init_db(str(tmp_path / "physio.db")), config)


def test_snapshot_evolves_awake_curves_and_mood_baseline(tmp_path) -> None:
    physio = _engine(tmp_path)
    start = datetime(2026, 7, 11, 1, 0)

    initial = physio.snapshot(start)
    later = physio.snapshot(start + timedelta(hours=2))

    assert initial.hunger == 70
    assert initial.energy == 70
    assert later.hunger == 58
    assert later.energy == 60
    assert later.mood == 60


def test_sleeping_halves_hunger_decay_and_restores_energy(tmp_path) -> None:
    physio = _engine(tmp_path, sleep_start="00:00", sleep_end="23:59")
    start = datetime(2026, 7, 11, 1, 0)

    physio.snapshot(start)
    later = physio.snapshot(start + timedelta(hours=1))

    assert later.sleeping is True
    assert later.hunger == 67
    assert later.energy == 90


def test_evolution_splits_across_both_sleep_boundaries(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "mybuddy.body.physio.localnow",
        lambda: datetime(2026, 7, 11, 0, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    physio = _engine(tmp_path, sleep_start="00:30", sleep_end="08:30")
    start_utc = datetime(2026, 7, 10, 16, 0)  # 本地 00:00

    physio.snapshot(start_utc)
    later = physio.snapshot(start_utc + timedelta(hours=10))  # 本地 10:00

    assert later.sleeping is False
    assert later.hunger == 34  # 清醒 2h×6 + 睡眠 8h×3
    assert later.energy == 92  # 睡眠阶段封顶 100，醒后 1.5h 回落 7.5


def test_feed_catalog_and_unknown_item_falls_back_to_water(tmp_path) -> None:
    physio = _engine(tmp_path)
    now = datetime(2026, 7, 11, 9, 0)
    physio.snapshot(now)

    curry = physio.apply_feed("curry", now)
    unknown = physio.apply_feed("not-food", now)

    assert curry.hunger == 100
    assert curry.mood == 64
    assert unknown.hunger == 100
    assert unknown.mood == 65


def test_touch_and_chat_daily_caps_persist(tmp_path) -> None:
    physio = _engine(tmp_path)
    now = datetime(2026, 7, 11, 9, 0)
    physio.snapshot(now)

    touched = physio.apply_touch(count=20, now=now)
    for _ in range(8):
        chatted = physio.apply_chat(now=now)

    assert touched.mood == 70
    assert chatted.mood == 75
    with session_scope(physio._engine) as session:
        daily = session.get(PhysioDaily, physio._local_datetime(now).date().isoformat())
        assert daily is not None
        assert daily.touch_count == 20
        assert daily.touch_mood_gain == 10
        assert daily.chat_mood_gain == 5


def test_woken_penalty_applies_once_and_is_visible_for_one_minute(tmp_path) -> None:
    physio = _engine(tmp_path, sleep_start="00:00", sleep_end="23:59")
    now = datetime(2026, 7, 11, 1, 0)
    physio.snapshot(now)

    first = physio.apply_chat(now=now)
    second = physio.apply_chat(now=now + timedelta(seconds=20))

    assert first.woken is True
    assert first.sleeping is True
    assert first.mood == 56  # -5 被叫醒 +1 对话
    assert second.woken is True
    assert second.mood == 57  # 只加对话,不重复 -5


def test_concurrent_touches_do_not_lose_updates(tmp_path) -> None:
    physio = _engine(tmp_path)
    now = datetime(2026, 7, 11, 9, 0)
    physio.snapshot(now)

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda _index: physio.apply_touch(now=now), range(5)))

    with session_scope(physio._engine) as session:
        state = session.get(PhysioState, 1)
        daily = session.get(PhysioDaily, physio._local_datetime(now).date().isoformat())
        assert state is not None
        assert daily is not None
        assert state.mood == pytest.approx(70.0)
        assert daily.touch_count == 5
        assert daily.touch_mood_gain == pytest.approx(10.0)


def test_concurrent_feeds_do_not_lose_updates(tmp_path) -> None:
    physio = _engine(tmp_path)
    now = datetime(2026, 7, 11, 9, 0)
    physio.snapshot(now)
    with session_scope(physio._engine) as session:
        state = session.get(PhysioState, 1)
        assert state is not None
        state.hunger = 0
        state.mood = 50

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda _index: physio.apply_feed("water", now), range(5)))

    snapshot = physio.snapshot(now)
    assert snapshot.hunger == 15
    assert snapshot.mood == 55


def test_threshold_crossing_enqueues_one_persisted_murmur(tmp_path) -> None:
    physio = _engine(tmp_path)
    now = datetime(2026, 7, 11, 9, 0)
    physio.snapshot(now)
    with session_scope(physio._engine) as session:
        state = session.get(PhysioState, 1)
        assert state is not None
        state.hunger = 31.0
        state.updated_at = now

    crossed = physio.snapshot(now + timedelta(minutes=10))
    approved = enqueue_crossed_murmurs(
        physio._engine,
        physio,
        crossed,
        server_flags={"physio_injection": True},
        day_index=1,
    )
    repeated = enqueue_crossed_murmurs(
        physio._engine,
        physio,
        physio.snapshot(now + timedelta(minutes=11)),
        server_flags={"physio_injection": True},
        day_index=1,
    )

    assert approved == ["hunger"]
    assert repeated == []
    assert [item["source"] for item in list_undelivered(physio._engine)] == ["body_murmur"]
    with session_scope(physio._engine) as session:
        assert session.query(VPetEvent).filter(VPetEvent.event == "body_murmur").count() == 1


def test_murmur_daily_limit_and_cooldown_survive_restart(tmp_path) -> None:
    physio = _engine(tmp_path, murmur_daily_limit=2)
    now = datetime(2026, 7, 11, 9, 0)

    assert physio.claim_murmurs(("hunger", "energy", "mood"), now=now) == [
        "hunger",
        "energy",
    ]
    restarted = PhysioEngine(physio._engine, physio._config)
    assert restarted.claim_murmurs(("hunger", "energy", "mood"), now=now) == []
    assert restarted.claim_murmurs(
        ("hunger", "energy", "mood"), now=now + timedelta(days=1)
    ) == ["hunger", "energy"]


def test_time_offset_requires_acceptance_mode(monkeypatch) -> None:
    monkeypatch.setenv("MYBUDDY_TIME_OFFSET_MINUTES", "60")
    with pytest.raises(RuntimeError, match="生产模式禁止"):
        configure_time_offset(acceptance_mode=False)
    assert configure_time_offset(acceptance_mode=True) == 60
    monkeypatch.setenv("MYBUDDY_TIME_OFFSET_MINUTES", "0")
    assert configure_time_offset(acceptance_mode=False) == 0


def test_legacy_body_state_config_maps_to_physio_with_warning() -> None:
    with pytest.warns(DeprecationWarning):
        config = Config.model_validate({"vpet": {"body_state_injection": True}})
    assert config.vpet.physio_injection is True
    assert config.vpet.body_state_injection is True


def test_work_stop_speech_has_persisted_daily_limit(tmp_path) -> None:
    physio = _engine(tmp_path)
    now = datetime(2026, 7, 11, 9, 0)
    assert [physio.claim_work_stop_speech(now) for _ in range(5)] == [
        True,
        True,
        True,
        True,
        False,
    ]


def test_busy_retry_exhaustion_raises_service_error(monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    class LockedConnection:
        def exec_driver_sql(self, _sql):
            raise OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked"))

        def rollback(self):
            return None

        def close(self):
            return None

    class LockedEngine:
        def __init__(self) -> None:
            self.calls = 0

        def connect(self):
            self.calls += 1
            return LockedConnection()

    engine = LockedEngine()
    physio = PhysioEngine(engine, PhysioConfig(enabled=True))  # type: ignore[arg-type]
    monkeypatch.setattr("mybuddy.body.physio.time.sleep", lambda _seconds: None)

    with pytest.raises(PhysioBusyError, match="database is busy"):
        physio.snapshot(datetime(2026, 7, 11, 9, 0))
    assert engine.calls == 4
