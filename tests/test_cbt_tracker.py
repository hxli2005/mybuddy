"""CBT 追踪器与引导引擎单元测试:DB 级冷却、5 轮节奏、情绪相关技巧排序。"""

from __future__ import annotations

from datetime import timedelta

from mybuddy._time import utcnow
from mybuddy.storage.db import init_db
from mybuddy.therapy.cbt import tracker as cbt_tracker_mod
from mybuddy.therapy.cbt.engine import CbtGuide
from mybuddy.therapy.cbt.tracker import COOLDOWN_HOURS, CbtTracker


def _engine(tmp_path):
    return init_db(str(tmp_path / "cbt.db"))


# ---- CbtTracker:冷却(DB 级) ----

def test_cooldown_survives_across_instances(tmp_path) -> None:
    engine = _engine(tmp_path)
    CbtTracker(engine, user_id=1).record("grounding")

    fresh = CbtTracker(engine, user_id=1)  # 全新实例,只靠 DB
    assert fresh.is_on_cooldown("grounding")


def test_cooldown_expires_after_24_hours(tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path)
    CbtTracker(engine, user_id=1).record("grounding")

    future = utcnow() + timedelta(hours=COOLDOWN_HOURS, minutes=1)
    monkeypatch.setattr(cbt_tracker_mod, "utcnow", lambda: future)
    assert not CbtTracker(engine, user_id=1).is_on_cooldown("grounding")


def test_cooldown_scoped_by_technique_and_user(tmp_path) -> None:
    engine = _engine(tmp_path)
    CbtTracker(engine, user_id=1).record("grounding")

    assert not CbtTracker(engine, user_id=1).is_on_cooldown("gratitude")
    assert not CbtTracker(engine, user_id=2).is_on_cooldown("grounding")


def test_memory_mode_cooldown_without_engine() -> None:
    t = CbtTracker()  # 访客:仅内存
    assert not t.is_on_cooldown("grounding")
    t.record("grounding")
    assert t.is_on_cooldown("grounding")
    assert not t.is_on_cooldown("worry_time")
    # 内存模式不落库
    assert t.get_events() == []


def test_get_events_returns_recorded_details(tmp_path) -> None:
    engine = _engine(tmp_path)
    t = CbtTracker(engine, user_id=1)
    t.record("grounding", context={"trigger": "好慌"}, completed=True)
    t.record("worry_time")

    events = t.get_events()
    assert len(events) == 2
    types = {e["technique_type"] for e in events}
    assert types == {"grounding", "worry_time"}
    by_type = {e["technique_type"]: e for e in events}
    assert by_type["grounding"]["completed"] is True
    assert by_type["worry_time"]["completed"] is False
    assert all(e["created_at"] for e in events)


# ---- CbtGuide:节奏与排序 ----

def test_guide_first_trigger_then_requires_5_round_gap() -> None:
    g = CbtGuide()
    first = g.detect_opportunity("我好慌", "negative")
    assert first is not None
    assert first["technique"] == "grounding"
    assert first["name"] and first["hint"]

    # 触发后 4 轮内即使命中也不再触发(节奏 ≥5 轮)
    for _ in range(4):
        assert g.detect_opportunity("我不行", "negative") is None
    second = g.detect_opportunity("我不行", "negative")
    assert second is not None
    assert second["technique"] == "cognitive_restructuring"


def test_guide_no_match_returns_none() -> None:
    g = CbtGuide()
    assert g.detect_opportunity("今天中午吃了面", "neutral") is None


def test_negative_emotion_prefers_grounding_over_cognitive() -> None:
    g = CbtGuide()
    # 同时命中 grounding(好慌)与认知重构(我不行)→ negative 时 grounding 优先
    out = g.detect_opportunity("我好慌,感觉我不行", "negative")
    assert out is not None
    assert out["technique"] == "grounding"


def test_negative_emotion_never_offers_behavioral_activation() -> None:
    g = CbtGuide()
    assert g.detect_opportunity("今天什么也不想做,不想动", "negative") is None


def test_neutral_emotion_allows_behavioral_activation() -> None:
    g = CbtGuide()
    out = g.detect_opportunity("今天不想动", "neutral")
    assert out is not None
    assert out["technique"] == "behavioral_activation"


def test_positive_emotion_offers_gratitude() -> None:
    g = CbtGuide()
    out = g.detect_opportunity("今天太好了,超幸运", "positive")
    assert out is not None
    assert out["technique"] == "gratitude"


def test_external_cooldown_check_skips_to_next_candidate() -> None:
    g = CbtGuide()
    out = g.detect_opportunity(
        "我好慌,感觉我不行",
        "negative",
        cooldown_check=lambda tech: tech == "grounding",  # 模拟 DB 冷却中
    )
    assert out is not None
    assert out["technique"] == "cognitive_restructuring"


def test_same_technique_blocked_by_24h_memory_cooldown() -> None:
    g = CbtGuide()
    assert g.detect_opportunity("我好慌", "negative")["technique"] == "grounding"
    out = None
    for _ in range(5):  # 熬过 5 轮节奏后,同技巧仍被 24h 冷却挡住
        out = g.detect_opportunity("我好慌", "negative")
    assert out is None


def test_mark_used_puts_technique_on_cooldown_and_resets_rounds() -> None:
    g = CbtGuide()
    g.mark_used("grounding")
    for _ in range(5):
        assert g.detect_opportunity("我好慌", "negative") is None
    # 换一个未用过的技巧,节奏已满足 → 可触发
    out = g.detect_opportunity("我不行", "negative")
    assert out is not None
    assert out["technique"] == "cognitive_restructuring"
