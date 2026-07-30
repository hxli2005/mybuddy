"""情绪追踪(mood tracker)纯逻辑单元测试:推导分、落库、签到、趋势与统计。"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

from mybuddy._time import utcnow
from mybuddy.mood import tracker as mood_mod
from mybuddy.mood.tracker import MoodTracker, derive_mood_score
from mybuddy.storage.db import init_db, session_scope
from mybuddy.storage.models import MoodRecord


def _emo(label=None, strength=0.0, category=None):
    return SimpleNamespace(label=label, strength=strength, category=category, intensity=None)


def _engine(tmp_path):
    return init_db(str(tmp_path / "mood.db"))


def _add_record(engine, user_id, score, days_ago, *, now, source="chat", category=None):
    with session_scope(engine) as s:
        s.add(
            MoodRecord(
                user_id=user_id,
                mood_score=score,
                source=source,
                category=category,
                created_at=now - timedelta(days=days_ago),
            )
        )


# ---- derive_mood_score ----

def test_derive_positive_base() -> None:
    # 6 + strength*4
    assert derive_mood_score(_emo("positive", 0.5)) == 8
    assert derive_mood_score(_emo("positive", 0.0)) == 6


def test_derive_positive_category_clamped_at_ten() -> None:
    # 6 + 4 + 1 = 11 → 钳制到 10
    assert derive_mood_score(_emo("positive", 1.0, "joy")) == 10


def test_derive_negative_base() -> None:
    # 4 - strength*3
    assert derive_mood_score(_emo("negative", 1.0)) == 1
    assert derive_mood_score(_emo("negative", 0.0, "stress")) == 3


def test_derive_negative_category_floor_at_zero() -> None:
    # 4 - 3 - 1 = 0,下界钳制
    assert derive_mood_score(_emo("negative", 1.0, "anxiety")) == 0


def test_derive_neutral_default_and_category_bonus() -> None:
    assert derive_mood_score(_emo("neutral")) == 5
    assert derive_mood_score(_emo("neutral", category="gratitude")) == 6


def test_derive_handles_missing_or_none_attributes() -> None:
    assert derive_mood_score(object()) == 5  # 无任何属性 → neutral
    assert derive_mood_score(_emo(None, None)) == 5  # None 回退


# ---- record_from_emotion ----

def test_record_from_emotion_persists_row(tmp_path) -> None:
    engine = _engine(tmp_path)
    t = MoodTracker(engine)
    t.record_from_emotion(1, _emo("negative", 0.7, "anxiety"))

    rows = t.records(1)
    assert len(rows) == 1
    assert rows[0]["source"] == "chat"
    assert rows[0]["category"] == "anxiety"
    assert rows[0]["score"] == 1  # round(4 - 2.1 - 1)

    with session_scope(engine) as s:
        row = s.query(MoodRecord).one()
        data = json.loads(row.emotion_data)
        assert data["label"] == "negative"
        assert data["category"] == "anxiety"


def test_record_from_emotion_noop_without_engine() -> None:
    t = MoodTracker(None)
    t.record_from_emotion(1, _emo("negative", 0.5))  # 不应抛异常
    assert t.records(1) == []


def test_record_from_emotion_swallows_errors(tmp_path) -> None:
    engine = _engine(tmp_path)
    t = MoodTracker(engine)
    # strength 不可转 float → derive_mood_score 内部抛异常 → 被吞掉
    t.record_from_emotion(1, _emo("negative", "不是数字"))
    assert t.records(1) == []


# ---- checkin / records ----

def test_checkin_returns_id_and_persists(tmp_path) -> None:
    engine = _engine(tmp_path)
    t = MoodTracker(engine)
    rid = t.checkin(1, 7, notes="还行")
    assert isinstance(rid, int)

    rows = t.records(1)
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["score"] == 7
    assert rows[0]["notes"] == "还行"
    assert rows[0]["source"] == "checkin"


def test_records_limit_keeps_latest_in_chronological_order(tmp_path) -> None:
    engine = _engine(tmp_path)
    now = utcnow()
    for days_ago, score in [(2, 1), (1, 2), (0, 3)]:
        _add_record(engine, 1, score, days_ago, now=now)

    rows = MoodTracker(engine).records(1, limit=2)
    assert [r["score"] for r in rows] == [2, 3]  # 最新两条,时间正序


# ---- trends ----

def test_trends_daily_average_and_cutoff(tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path)
    now = utcnow()
    monkeypatch.setattr(mood_mod, "utcnow", lambda: now)

    _add_record(engine, 1, 4, 0, now=now)
    _add_record(engine, 1, 7, 0, now=now)
    _add_record(engine, 1, 8, 1, now=now)
    _add_record(engine, 1, 2, 10, now=now)  # 超出窗口,应被排除

    out = MoodTracker(engine).trends(1, days=7)
    assert len(out) == 2
    yesterday = (now - timedelta(days=1)).strftime("%m-%d")
    today = now.strftime("%m-%d")
    assert out[0] == {"date": yesterday, "avg_score": 8.0}
    assert out[1] == {"date": today, "avg_score": 5.5}


def test_trends_empty_without_engine() -> None:
    assert MoodTracker(None).trends(1) == []


# ---- stats ----

def test_stats_aggregates(tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path)
    now = utcnow()
    monkeypatch.setattr(mood_mod, "utcnow", lambda: now)

    _add_record(engine, 1, 8, 0, now=now, source="checkin")
    _add_record(engine, 1, 2, 1, now=now, source="checkin")
    _add_record(engine, 1, 6, 0, now=now, source="chat", category="anxiety")

    stats = MoodTracker(engine).stats(1)
    assert stats["total_records"] == 3
    assert stats["categories"] == {"anxiety": 1}
    assert stats["streak"] == 2  # 今天 + 昨天连续签到
    assert stats["avg_score"] == 5.3  # (8+2+6)/3
    assert stats["best_day"] == now.strftime("%Y-%m-%d")
    assert stats["worst_day"] == (now - timedelta(days=1)).strftime("%Y-%m-%d")


def test_stats_streak_allows_starting_from_yesterday(tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path)
    now = utcnow()
    monkeypatch.setattr(mood_mod, "utcnow", lambda: now)

    # 今天没签到:从昨天起算,不清零
    _add_record(engine, 1, 5, 1, now=now, source="checkin")
    _add_record(engine, 1, 5, 2, now=now, source="checkin")
    _add_record(engine, 1, 5, 4, now=now, source="checkin")  # 断档,不计入

    assert MoodTracker(engine).stats(1)["streak"] == 2


def test_stats_chat_records_do_not_count_toward_streak(tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path)
    now = utcnow()
    monkeypatch.setattr(mood_mod, "utcnow", lambda: now)

    _add_record(engine, 1, 5, 0, now=now, source="chat")
    stats = MoodTracker(engine).stats(1)
    assert stats["total_records"] == 1
    assert stats["streak"] == 0


def test_stats_defaults_without_engine() -> None:
    stats = MoodTracker(None).stats(1)
    assert stats == {
        "total_records": 0,
        "streak": 0,
        "categories": {},
        "avg_score": None,
        "best_day": None,
        "worst_day": None,
    }
