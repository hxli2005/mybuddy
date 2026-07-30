"""对话式评估追踪器单元测试:维度初始化、状态迁移、归档、过期重置与投放节流。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from mybuddy._time import utcnow as real_utcnow
from mybuddy.assessment import tracker as tracker_mod
from mybuddy.assessment.tracker import (
    ConversationalAssessmentTracker,
    InMemoryAssessmentTracker,
)
from mybuddy.storage.db import init_db, session_scope
from mybuddy.storage.models import AssessmentCycle, AssessmentDimension


@pytest.fixture(autouse=True)
def _fresh_ask_counter(monkeypatch):
    """投放节流计数是模块级进程内状态,逐测试隔离。"""
    monkeypatch.setattr(tracker_mod, "_ROUNDS_SINCE_LAST_ASK", {})


@pytest.fixture()
def tracker(tmp_path):
    engine = init_db(str(tmp_path / "assess.db"))
    t = ConversationalAssessmentTracker(engine, user_id=1)
    t.ensure_dimensions()
    return t


def _dim_row(tracker_obj, atype, idx):
    with session_scope(tracker_obj._engine) as s:
        return (
            s.query(AssessmentDimension)
            .filter(AssessmentDimension.user_id == 1)
            .filter(AssessmentDimension.assessment_type == atype)
            .filter(AssessmentDimension.dimension_index == idx)
            .one()
        )


# ---- ensure_dimensions ----

def test_ensure_dimensions_creates_16_unasked(tracker) -> None:
    pending = tracker.get_pending_dimensions()
    assert len(pending) == 16
    assert {d["assessment_type"] for d in pending} == {"phq9", "gad7"}
    assert sum(1 for d in pending if d["assessment_type"] == "phq9") == 9
    assert sum(1 for d in pending if d["assessment_type"] == "gad7") == 7
    assert all(d["status"] == "unasked" for d in pending)


def test_ensure_dimensions_idempotent(tracker) -> None:
    tracker.ensure_dimensions()
    tracker.ensure_dimensions()
    with session_scope(tracker._engine) as s:
        count = s.query(AssessmentDimension).filter(AssessmentDimension.user_id == 1).count()
    assert count == 16


# ---- 状态迁移 ----

def test_mark_asked_transitions_and_stamps_time(tracker) -> None:
    tracker.mark_asked("phq9", 2)
    asked = tracker.get_asked_dimensions()
    assert [(d["assessment_type"], d["dimension_index"]) for d in asked] == [("phq9", 2)]
    assert asked[0]["asked_at"] is not None
    assert len(tracker.get_pending_dimensions()) == 15


def test_record_score_transitions_and_stores_source(tracker) -> None:
    tracker.mark_asked("gad7", 3)
    tracker.record_score("gad7", 3, 2, source_conv='{"answer": "很难放松"}')

    scored = tracker.get_scored_dimensions()
    assert [(d["assessment_type"], d["dimension_index"], d["score"]) for d in scored] == [
        ("gad7", 3, 2)
    ]
    assert scored[0]["scored_at"] is not None
    assert tracker.get_asked_dimensions() == []
    row = _dim_row(tracker, "gad7", 3)
    assert row.source_conversation == '{"answer": "很难放松"}'


def test_record_score_unknown_dimension_is_noop(tracker) -> None:
    tracker.record_score("phq9", 99, 3)
    assert tracker.get_scored_dimensions() == []


# ---- 归档 ----

def test_phq9_completion_archives_cycle_with_total_and_severity(tracker) -> None:
    tracker.mark_asked("phq9", 0)
    for i in range(9):
        tracker.record_score("phq9", i, 1)

    history = tracker.get_history()
    assert len(history) == 1
    cycle = history[0]
    assert cycle["assessment_type"] == "phq9"
    assert cycle["total_score"] == 9
    assert cycle["severity"] == "轻度"
    assert cycle["started_at"] is not None
    assert cycle["completed_at"] is not None


def test_gad7_completion_archives_severity(tracker) -> None:
    for i in range(7):
        tracker.record_score("gad7", i, 3)

    history = tracker.get_history()
    assert len(history) == 1
    assert history[0]["assessment_type"] == "gad7"
    assert history[0]["total_score"] == 21
    assert history[0]["severity"] == "重度"


def test_partial_scale_does_not_archive(tracker) -> None:
    for i in range(8):  # phq9 还差第 9 维
        tracker.record_score("phq9", i, 1)
    assert tracker.get_history() == []


def test_rescore_after_completion_does_not_duplicate_cycle(tracker) -> None:
    for i in range(9):
        tracker.record_score("phq9", i, 1)
    tracker.record_score("phq9", 0, 3)  # 重复评分已 scored 的维度
    assert len(tracker.get_history()) == 1


# ---- 过期重置 ----

def test_reset_expired_asked_dimension_after_14_days(tracker, monkeypatch) -> None:
    old = real_utcnow() - timedelta(days=tracker_mod.DIMENSION_EXPIRY_DAYS + 1)
    monkeypatch.setattr(tracker_mod, "utcnow", lambda: old)
    tracker.mark_asked("phq9", 0)
    monkeypatch.setattr(tracker_mod, "utcnow", real_utcnow)
    tracker.mark_asked("phq9", 1)  # 未过期,应保持 asked

    pending = {(d["assessment_type"], d["dimension_index"]) for d in tracker.get_pending_dimensions()}
    assert ("phq9", 0) in pending  # 过期 → 重置回 unasked
    assert ("phq9", 1) not in pending
    row = _dim_row(tracker, "phq9", 0)
    assert row.status == "unasked"
    assert row.asked_at is None


def test_scored_dimensions_are_not_reset_by_expiry(tracker, monkeypatch) -> None:
    old = real_utcnow() - timedelta(days=tracker_mod.DIMENSION_EXPIRY_DAYS + 1)
    monkeypatch.setattr(tracker_mod, "utcnow", lambda: old)
    tracker.mark_asked("phq9", 0)
    tracker.record_score("phq9", 0, 2)
    monkeypatch.setattr(tracker_mod, "utcnow", real_utcnow)

    tracker.get_pending_dimensions()  # 触发 _reset_expired
    assert len(tracker.get_scored_dimensions()) == 1


# ---- 周期自动重置 ----

def test_cycle_auto_resets_after_14_days(tracker, monkeypatch) -> None:
    old = real_utcnow() - timedelta(days=tracker_mod.CYCLE_RESET_DAYS + 1)
    monkeypatch.setattr(tracker_mod, "utcnow", lambda: old)
    tracker.mark_asked("phq9", 0)
    tracker.record_score("phq9", 0, 3)
    monkeypatch.setattr(tracker_mod, "utcnow", real_utcnow)

    tracker.ensure_dimensions()  # 周期超时 → 自动 reset_cycle
    assert tracker.get_scored_dimensions() == []
    assert len(tracker.get_pending_dimensions()) == 16
    row = _dim_row(tracker, "phq9", 0)
    assert row.score is None and row.scored_at is None


def test_cycle_not_reset_when_recent(tracker) -> None:
    tracker.mark_asked("phq9", 0)
    tracker.record_score("phq9", 0, 3)
    tracker.ensure_dimensions()
    assert len(tracker.get_scored_dimensions()) == 1


# ---- 投放节流 ----

def test_pick_next_dimension_throttles_by_rounds(tracker, monkeypatch) -> None:
    monkeypatch.setattr(tracker_mod.random, "choice", lambda seq: seq[0])

    first = tracker.pick_next_dimension()
    assert first is not None
    assert {"assessment_type", "dimension_index", "dimension_name", "hint"} <= set(first)

    # 投放后必须再间隔 MIN_ROUNDS_BETWEEN_ASKS 轮
    for _ in range(tracker_mod.MIN_ROUNDS_BETWEEN_ASKS):
        assert tracker.pick_next_dimension() is None
    assert tracker.pick_next_dimension() is not None


def test_pick_never_selects_phq9_self_harm_dimension(tracker) -> None:
    # 除 PHQ-9 第 9 维(index 8)外全部评分完
    for i in range(8):
        tracker.record_score("phq9", i, 0)
    for i in range(7):
        tracker.record_score("gad7", i, 0)

    pending = tracker.get_pending_dimensions()
    assert [(d["assessment_type"], d["dimension_index"]) for d in pending] == [("phq9", 8)]
    assert tracker.pick_next_dimension() is None  # 自伤维度永不主动投放


# ---- InMemoryAssessmentTracker(访客) ----

def test_inmemory_tracker_same_interface_state_machine() -> None:
    t = InMemoryAssessmentTracker()
    assert len(t.get_pending_dimensions()) == 16

    t.mark_asked("phq9", 1)
    asked = t.get_asked_dimensions()
    assert [(d["assessment_type"], d["dimension_index"]) for d in asked] == [("phq9", 1)]
    assert asked[0]["asked_at"] is not None

    t.record_score("phq9", 1, 2)
    assert t.get_asked_dimensions() == []
    assert len(t.get_pending_dimensions()) == 15

    t.reset_cycle()
    assert len(t.get_pending_dimensions()) == 16


def test_inmemory_pick_throttle_and_self_harm_filter(monkeypatch) -> None:
    monkeypatch.setattr(tracker_mod.random, "choice", lambda seq: seq[0])

    t = InMemoryAssessmentTracker()
    assert t.pick_next_dimension() is not None
    for _ in range(tracker_mod.MIN_ROUNDS_BETWEEN_ASKS):
        assert t.pick_next_dimension() is None
    assert t.pick_next_dimension() is not None

    # 只剩 phq9 自伤维度时不投放
    t2 = InMemoryAssessmentTracker()
    for i in range(8):
        t2.record_score("phq9", i, 0)
    for i in range(7):
        t2.record_score("gad7", i, 0)
    assert t2.pick_next_dimension() is None
