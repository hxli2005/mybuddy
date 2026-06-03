"""FeedbackBus + 隐式反馈关键词测试。"""

from __future__ import annotations

import pytest

from mybuddy.learning import (
    FeedbackBus,
    FeedbackEvent,
    detect_implicit_negative,
    make_profile_claim_subscriber,
    make_trajectory_subscriber,
)
from mybuddy.learning.trajectory import TrajectoryLogger
from mybuddy.memory import LongTermMemory, UserProfile
from mybuddy.storage import init_db

from .test_memory import mock_embed

# ---- bus ----

def test_bus_publishes_to_all_subscribers() -> None:
    bus = FeedbackBus()
    got_a: list[FeedbackEvent] = []
    got_b: list[FeedbackEvent] = []
    bus.subscribe(lambda e: got_a.append(e))
    bus.subscribe(lambda e: got_b.append(e))

    ev = FeedbackEvent(turn_id="t1", label="good")
    bus.publish(ev)

    assert len(got_a) == 1 and got_a[0].turn_id == "t1"
    assert len(got_b) == 1


def test_bus_one_subscriber_failure_doesnt_block_others() -> None:
    bus = FeedbackBus()
    got: list[FeedbackEvent] = []

    def broken(_e):
        raise RuntimeError("x")

    bus.subscribe(broken)
    bus.subscribe(lambda e: got.append(e))
    bus.publish(FeedbackEvent(turn_id="t", label="bad"))

    assert len(got) == 1


# ---- trajectory subscriber ----

def test_trajectory_subscriber_writes_label(tmp_path) -> None:
    logger = TrajectoryLogger(tmp_path)
    bus = FeedbackBus()
    bus.subscribe(make_trajectory_subscriber(logger))

    bus.publish(FeedbackEvent(turn_id="turn-abc", label="good"))

    # 按天文件里应有一条
    files = list(tmp_path.glob("*.labels.jsonl"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8").strip()
    assert "turn-abc" in content
    assert '"label": "good"' in content


# ---- profile claim subscriber ----

@pytest.fixture
def profile_env(tmp_path):
    engine = init_db(str(tmp_path / "p.db"))
    (tmp_path / "chroma").mkdir()
    ltm = LongTermMemory(
        persist_dir=str(tmp_path / "chroma"),
        collection_name=f"fb_{tmp_path.name}",
        embedding_fn=mock_embed,
    )
    return engine, UserProfile(engine, ltm)


def test_claim_subscriber_boosts_on_good(profile_env) -> None:
    _, profile = profile_env
    cid = profile.add_claim("用户爱晨跑", confidence=0.5)

    bus = FeedbackBus()
    bus.subscribe(make_profile_claim_subscriber(profile))
    bus.publish(FeedbackEvent(turn_id="t", label="good", related_claim_ids=[cid]))

    claims = profile.get_all_claims()
    match = next(c for c in claims if c["sql_id"] == cid)
    assert match["confidence"] > 0.5  # 被 +delta_good(默认 0.05)


def test_claim_subscriber_penalizes_on_bad(profile_env) -> None:
    _, profile = profile_env
    cid = profile.add_claim("用户讨厌咖啡", confidence=0.5)

    bus = FeedbackBus()
    bus.subscribe(make_profile_claim_subscriber(profile))
    bus.publish(FeedbackEvent(turn_id="t", label="bad", related_claim_ids=[cid]))

    claims = profile.get_all_claims()
    match = next(c for c in claims if c["sql_id"] == cid)
    assert match["confidence"] < 0.5  # 被 +delta_bad(默认 -0.1)


def test_claim_subscriber_skips_without_related(profile_env) -> None:
    _, profile = profile_env
    cid = profile.add_claim("x", confidence=0.5)

    bus = FeedbackBus()
    bus.subscribe(make_profile_claim_subscriber(profile))
    # 无 related_claim_ids
    bus.publish(FeedbackEvent(turn_id="t", label="bad"))

    claims = profile.get_all_claims()
    match = next(c for c in claims if c["sql_id"] == cid)
    assert match["confidence"] == 0.5


def test_claim_subscriber_treats_implicit_negative_as_negative(profile_env) -> None:
    _, profile = profile_env
    cid = profile.add_claim("x", confidence=0.5)

    bus = FeedbackBus()
    bus.subscribe(make_profile_claim_subscriber(profile))
    bus.publish(
        FeedbackEvent(turn_id="t", label="implicit:negative", related_claim_ids=[cid])
    )

    claims = profile.get_all_claims()
    match = next(c for c in claims if c["sql_id"] == cid)
    assert match["confidence"] < 0.5


# ---- 隐式反馈关键词 ----

def test_implicit_negative_detects_common_phrases() -> None:
    for text in [
        "不对,我说的是另一件事",
        "你理解错了",
        "不是这样的",
        "我的意思是周末",
        "再试一次吧",
        "搞错了",
    ]:
        assert detect_implicit_negative(text), f"应命中: {text}"


def test_implicit_negative_ignores_neutral_text() -> None:
    for text in [
        "好的谢谢",
        "今天天气不错",
        "再见",
        "",
    ]:
        assert not detect_implicit_negative(text), f"不应命中: {text}"
