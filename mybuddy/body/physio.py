"""生理曲线引擎。

曲线采用懒求值:没有后台 tick,每次读取或互动时在 SQLite 原子事务中把状态
演化到指定时刻。壳永远不持有或回写这些数值。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from mybuddy._time import localnow, utcnow
from mybuddy.config import PhysioConfig
from mybuddy.storage.models import PhysioCooldown, PhysioDaily, PhysioState

if TYPE_CHECKING:
    from sqlalchemy import Engine


FOOD_CATALOG: dict[str, dict[str, int | str]] = {
    "congee": {"name": "一碗粥", "hunger": 20, "mood": 2},
    "curry": {"name": "咖喱饭", "hunger": 35, "mood": 4},
    "milk_tea": {"name": "奶茶", "hunger": 10, "mood": 6},
    "coffee": {"name": "咖啡", "hunger": 5, "mood": 8},
    "water": {"name": "水", "hunger": 3, "mood": 1},
}


class PhysioBusyError(RuntimeError):
    """SQLite 生理事务持续繁忙；上层应返回 503 而不是使用旧状态。"""


@dataclass(frozen=True)
class PhysioSnapshot:
    hunger: int
    energy: int
    mood: int
    sleeping: bool
    woken: bool
    levels: dict[str, bool]
    updated_at: str
    delta: dict[str, float]
    crossed: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload.pop("crossed", None)
        return payload


T = TypeVar("T")


class PhysioEngine:
    """线程安全的 SQLite 生理状态机。"""

    def __init__(self, engine: Engine, config: PhysioConfig) -> None:
        self._engine = engine
        self._config = config

    def snapshot(self, now: datetime | None = None) -> PhysioSnapshot:
        current = now or utcnow()
        return self._atomic(lambda session: self._snapshot_in_session(session, current))

    def apply_feed(self, item_id: str, now: datetime | None = None) -> PhysioSnapshot:
        current = now or utcnow()
        clean_id = item_id if item_id in FOOD_CATALOG else "water"
        item = FOOD_CATALOG[clean_id]

        def mutate(session: Session) -> PhysioSnapshot:
            state, daily, before = self._prepare_interaction(session, current)
            state.hunger = _clamp(state.hunger + float(item["hunger"]))
            state.mood = _clamp(state.mood + float(item["mood"]))
            feed_items = _json_list(daily.feed_items_json)
            if clean_id not in feed_items:
                feed_items.append(clean_id)
            daily.feed_items_json = json.dumps(feed_items, ensure_ascii=False)
            return self._build_snapshot(state, current, before)

        return self._atomic(mutate)

    def apply_touch(self, count: int = 1, now: datetime | None = None) -> PhysioSnapshot:
        current = now or utcnow()
        clean_count = max(1, min(50, int(count)))

        def mutate(session: Session) -> PhysioSnapshot:
            state, daily, before = self._prepare_interaction(session, current)
            actual_gain = min(
                2.0 * clean_count,
                max(0.0, 10.0 - daily.touch_mood_gain),
            )
            state.mood = _clamp(state.mood + actual_gain)
            daily.touch_mood_gain += actual_gain
            daily.touch_count += clean_count
            return self._build_snapshot(state, current, before)

        return self._atomic(mutate)

    def apply_chat(self, now: datetime | None = None) -> PhysioSnapshot:
        current = now or utcnow()

        def mutate(session: Session) -> PhysioSnapshot:
            state, daily, before = self._prepare_interaction(session, current)
            actual_gain = min(1.0, max(0.0, 5.0 - daily.chat_mood_gain))
            state.mood = _clamp(state.mood + actual_gain)
            daily.chat_mood_gain += actual_gain
            return self._build_snapshot(state, current, before)

        return self._atomic(mutate)

    def claim_murmurs(
        self,
        kinds: tuple[str, ...],
        *,
        now: datetime | None = None,
    ) -> list[str]:
        """按日限额与 4h 冷却原子认领身体低语。"""
        current = now or utcnow()

        def mutate(session: Session) -> list[str]:
            daily = self._get_daily(session, current)
            approved: list[str] = []
            for kind in kinds:
                if kind not in {"hunger", "energy", "mood"}:
                    continue
                if daily.murmur_count >= max(0, self._config.murmur_daily_limit):
                    break
                cooldown = session.get(PhysioCooldown, kind)
                if cooldown is not None and cooldown.last_emitted_at is not None:
                    if current - cooldown.last_emitted_at < timedelta(hours=4):
                        continue
                if cooldown is None:
                    cooldown = PhysioCooldown(kind=kind)
                    session.add(cooldown)
                cooldown.last_emitted_at = current
                daily.murmur_count += 1
                approved.append(kind)
            return approved

        return self._atomic(mutate)

    def claim_work_stop_speech(self, now: datetime | None = None) -> bool:
        """原子领取共处收尾语日预算(最多 4 次)。"""
        current = now or utcnow()

        def mutate(session: Session) -> bool:
            daily = self._get_daily(session, current)
            if daily.work_stop_speech_count >= 4:
                return False
            daily.work_stop_speech_count += 1
            return True

        return self._atomic(mutate)

    def _snapshot_in_session(self, session: Session, now: datetime) -> PhysioSnapshot:
        state = self._get_state(session, now)
        before = _values(state)
        self._evolve(state, now)
        return self._build_snapshot(state, now, before)

    def _prepare_interaction(
        self,
        session: Session,
        now: datetime,
    ) -> tuple[PhysioState, PhysioDaily, dict[str, float]]:
        state = self._get_state(session, now)
        before = _values(state)
        self._evolve(state, now)
        sleeping = self._is_sleeping(now, state.last_interaction_at)
        if sleeping and (
            state.last_interaction_at is None
            or now - state.last_interaction_at >= timedelta(minutes=10)
        ):
            state.mood = _clamp(state.mood - 5.0)
            state.woken_until = now + timedelta(seconds=60)
        state.last_interaction_at = now
        daily = self._get_daily(session, now)
        return state, daily, before

    def _get_state(self, session: Session, now: datetime) -> PhysioState:
        state = session.get(PhysioState, 1)
        if state is None:
            state = PhysioState(
                id=1,
                hunger=70.0,
                energy=70.0,
                mood=self._config.mood_baseline,
                updated_at=now,
            )
            session.add(state)
            session.flush()
        return state

    def _get_daily(self, session: Session, now: datetime) -> PhysioDaily:
        key = self._local_datetime(now).date().isoformat()
        daily = session.get(PhysioDaily, key)
        if daily is None:
            daily = PhysioDaily(local_date=key)
            session.add(daily)
            session.flush()
        return daily

    def _evolve(self, state: PhysioState, now: datetime) -> None:
        if now <= state.updated_at:
            return
        cursor = state.updated_at
        while cursor < now:
            sleeping = self._in_sleep_window(self._local_datetime(cursor))
            transition = self._next_transition(cursor)
            segment_end = min(now, transition)
            hours = max((segment_end - cursor).total_seconds() / 3600.0, 0.0)
            hunger_rate = self._config.hunger_decay_per_hour * (0.5 if sleeping else 1.0)
            energy_rate = 20.0 if sleeping else -5.0
            state.hunger = _clamp(state.hunger - hunger_rate * hours)
            state.energy = _clamp(state.energy + energy_rate * hours)
            cursor = segment_end

        elapsed_hours = (now - state.updated_at).total_seconds() / 3600.0
        half_life = max(self._config.mood_half_life_hours, 0.001)
        baseline = self._config.mood_baseline
        state.mood = _clamp(
            baseline + (state.mood - baseline) * (0.5 ** (elapsed_hours / half_life))
        )
        state.updated_at = now

    def _next_transition(self, utc_value: datetime) -> datetime:
        local = self._local_datetime(utc_value)
        start_h, start_m = _parse_hh_mm(self._config.sleep_start)
        end_h, end_m = _parse_hh_mm(self._config.sleep_end)
        candidates: list[datetime] = []
        for day_offset in range(0, 3):
            day = local.date() + timedelta(days=day_offset)
            for hour, minute in ((start_h, start_m), (end_h, end_m)):
                candidate = local.replace(
                    year=day.year,
                    month=day.month,
                    day=day.day,
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0,
                )
                if candidate > local:
                    candidates.append(candidate)
        next_local = min(candidates)
        return next_local.astimezone(UTC).replace(tzinfo=None)

    def _is_sleeping(self, now: datetime, _last_interaction: datetime | None) -> bool:
        # sleeping 表示作息窗,不是“最近十分钟没有互动”。用户把她叫醒时
        # sleeping 与 woken 会同时为真,壳据此保留睡姿并让回复带困意。
        return self._in_sleep_window(self._local_datetime(now))

    def _in_sleep_window(self, local_value: datetime) -> bool:
        start_h, start_m = _parse_hh_mm(self._config.sleep_start)
        end_h, end_m = _parse_hh_mm(self._config.sleep_end)
        current = local_value.hour * 60 + local_value.minute
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m
        if start == end:
            return False
        if start < end:
            return start <= current < end
        return current >= start or current < end

    @staticmethod
    def _local_datetime(utc_value: datetime) -> datetime:
        if utc_value.tzinfo is None:
            utc_value = utc_value.replace(tzinfo=UTC)
        return utc_value.astimezone(localnow().tzinfo)

    def _build_snapshot(
        self,
        state: PhysioState,
        now: datetime,
        before: dict[str, float],
    ) -> PhysioSnapshot:
        values = _values(state)
        levels = {
            "hungry": state.hunger <= 30,
            "tired": state.energy <= 30,
            "low": state.mood <= 30,
            "bright": state.mood >= 70,
        }
        previous_levels = _json_dict(state.last_levels_json)
        crossed = tuple(
            kind
            for kind, level_name in (
                ("hunger", "hungry"),
                ("energy", "tired"),
                ("mood", "low"),
            )
            if previous_levels and not previous_levels.get(level_name) and levels[level_name]
        )
        state.last_levels_json = json.dumps(levels, ensure_ascii=False)
        sleeping = self._is_sleeping(now, state.last_interaction_at)
        return PhysioSnapshot(
            hunger=round(state.hunger),
            energy=round(state.energy),
            mood=round(state.mood),
            sleeping=sleeping,
            woken=bool(state.woken_until and state.woken_until >= now),
            levels=levels,
            updated_at=state.updated_at.isoformat(timespec="seconds"),
            delta={key: round(values[key] - before[key], 4) for key in values},
            crossed=crossed,
        )

    def _atomic(self, operation: Callable[[Session], T]) -> T:
        delays = (0.05, 0.1, 0.2)
        for attempt in range(len(delays) + 1):
            connection = self._engine.connect()
            session: Session | None = None
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                session = Session(bind=connection, expire_on_commit=False, future=True)
                result = operation(session)
                session.flush()
                connection.commit()
                return result
            except OperationalError as exc:
                connection.rollback()
                locked = "locked" in str(exc).lower()
                if locked and attempt >= len(delays):
                    raise PhysioBusyError("physio database is busy") from exc
                if not locked:
                    raise
                time.sleep(delays[attempt])
            except Exception:
                connection.rollback()
                raise
            finally:
                if session is not None:
                    session.close()
                connection.close()
        raise RuntimeError("unreachable")


def _values(state: PhysioState) -> dict[str, float]:
    return {
        "hunger": float(state.hunger),
        "energy": float(state.energy),
        "mood": float(state.mood),
    }


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _parse_hh_mm(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"无效时间:{value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"无效时间:{value!r}")
    return hour, minute


def _json_list(value: str | None) -> list[str]:
    try:
        loaded = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in loaded] if isinstance(loaded, list) else []


def _json_dict(value: str | None) -> dict[str, bool]:
    try:
        loaded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(key): bool(item) for key, item in loaded.items()}
