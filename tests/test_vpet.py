from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import mybuddy.api as api_module
from mybuddy._time import utcnow
from mybuddy.agent.context import build_system_prompt
from mybuddy.agent.living_state import synthesize_living_state
from mybuddy.api import AppState, VPetEventRequest
from mybuddy.body import PhysioEngine
from mybuddy.config import Config, PersonaConfig
from mybuddy.emotion import EmotionResult, EmotionTracker
from mybuddy.integrations.vpet import chat_to_vpet_payload, normalize_body_state
from mybuddy.memory import LongTermMemory
from mybuddy.storage import (
    Message,
    PhysioDaily,
    PhysioState,
    VPetEvent,
    append_message,
    count_vpet_escalations_today,
    enqueue,
    init_db,
    mark_vpet_event_result,
    record_vpet_event,
    session_scope,
)
from mybuddy.web import DemoHandler


def test_normalize_body_state_whitelist_and_clamp() -> None:
    assert normalize_body_state("bad") == {}
    assert normalize_body_state(
        {
            "food": -3,
            "drink": 120,
            "feeling": "33.5",
            "likability": -10,
            "money": 200000,
            "mode": "Nomal",
            "bad": 1,
        }
    ) == {
        "food": 0,
        "drink": 100,
        "feeling": 33.5,
        "likability": 0,
        "money": 100000,
        "mode": "Nomal",
    }
    assert normalize_body_state({"mode": "Normal"}) == {}


def test_concurrent_client_event_id_creates_one_audit_row(tmp_path) -> None:
    engine = init_db(str(tmp_path / "event-race.db"))

    def record(_index: int):
        return record_vpet_event(
            engine,
            event="feed",
            client_event_id="same-event",
            server_flags={},
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(record, range(10)))

    assert sum(created for _row, created in results) == 1
    assert {row["id"] for row, _created in results} == {1}
    with session_scope(engine) as session:
        assert session.query(VPetEvent).count() == 1


def test_vpet_chat_bubble_is_two_sentences_but_keeps_full_text() -> None:
    full = "第一句。第二句！第三句应该只在聊天面板里。"
    payload = chat_to_vpet_payload({"text": full})

    assert payload["text"] == full
    assert payload["speech"]["text"] == "第一句。第二句！"
    assert payload["speech"]["truncated"] is True


def test_vpet_state_shape_and_idle_priority(tmp_path) -> None:
    engine = init_db(str(tmp_path / "state.db"))
    cfg = Config()
    cfg.persona.character_life.recent_self_event = "刚读完一篇文献"
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg

    base = state.vpet_state_payload()
    assert base["ok"] is True
    assert base["bridge"] == "vpet-bridge/2"
    assert datetime.fromisoformat(base["server_time"]).tzinfo is not None
    assert base["idle_hint"] == "read"
    assert set(base) == {
        "ok",
        "bridge",
        "server_time",
        "time_offset_minutes",
        "physio",
        "idle_hint",
        "warmth",
        "server_flags",
        "day_index",
    }

    record_vpet_event(
        engine,
        event="work_start",
        context={"session_id": "priority"},
        server_flags={},
    )
    assert api_module._vpet_idle_hint(engine, None, cfg) == "work"
    assert api_module._vpet_idle_hint(engine, {"sleeping": True}, cfg) == "sleep"


def test_physio_injection_low_hunger_replaces_fictional_body_status(tmp_path) -> None:
    engine = init_db(str(tmp_path / "body_life.db"))
    append_message(
        engine,
        session_id="s1",
        role="user",
        content="论文 baseline 复现对不上",
        meta={"turn_id": "old"},
    )
    life = synthesize_living_state(
        PersonaConfig(),
        engine=engine,
        session_id="s1",
        physio={
            "hunger": 20,
            "energy": 80,
            "mood": 55,
            "sleeping": False,
            "woken": False,
            "levels": {"hungry": True, "tired": False, "low": False, "bright": False},
        },
        physio_injection=True,
    )
    prompt = build_system_prompt(PersonaConfig(), life=life)

    assert "肚子有点空" in life.today_status
    assert "语气会更黏一点" in life.current_mood
    assert "论文" in life.recent_self_event
    assert "肚子有点空" in prompt
    assert "杯水" not in prompt
    assert "刚吃" not in prompt


def test_physio_injection_off_keeps_living_state_equivalent(tmp_path) -> None:
    engine = init_db(str(tmp_path / "body_life_off.db"))
    append_message(
        engine,
        session_id="s1",
        role="user",
        content="今天组会又被问住了",
        meta={"turn_id": "old"},
    )
    kwargs = {
        "engine": engine,
        "session_id": "s1",
    }

    without_body = synthesize_living_state(PersonaConfig(), **kwargs)
    with_physio_disabled = synthesize_living_state(
        PersonaConfig(),
        **kwargs,
        physio={"hunger": 5, "energy": 5, "mood": 5, "levels": {"hungry": True}},
        physio_injection=False,
    )

    assert with_physio_disabled.model_dump() == without_body.model_dump()


@pytest.mark.asyncio
async def test_vpet_chat_body_state_is_always_ignored_and_physio_is_used(tmp_path) -> None:
    used, used_call = await _run_vpet_chat_meta(
        tmp_path / "used.db",
        enabled=True,
        body_state={"food": 20},
    )
    disabled, disabled_call = await _run_vpet_chat_meta(
        tmp_path / "disabled.db",
        enabled=False,
        body_state={"food": 20},
    )
    missing, missing_call = await _run_vpet_chat_meta(
        tmp_path / "missing.db",
        enabled=True,
        body_state=None,
    )

    assert used["vpet"]["body_state_present"] is True
    assert used["vpet"]["body_state_used"] is False
    assert used_call["body_state"] is None
    assert used_call["physio"] is not None
    assert disabled["vpet"]["body_state_present"] is True
    assert disabled["vpet"]["body_state_used"] is False
    assert disabled_call["body_state"] is None
    assert disabled_call["physio"] is None
    assert missing["vpet"]["body_state_present"] is False
    assert missing["vpet"]["body_state_used"] is False
    assert missing_call["body_state"] is None
    assert missing_call["physio"] is not None


@pytest.mark.asyncio
async def test_legacy_body_state_runtime_warning_is_emitted_once(tmp_path, caplog) -> None:
    engine = init_db(str(tmp_path / "body-warning.db"))
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = Config()
    state.agent = _FakeChatAgent(engine)

    with caplog.at_level("WARNING", logger="mybuddy.api"):
        await state.vpet_chat_payload("第一轮", body_state={"food": 10})
        await state.vpet_chat_payload("第二轮", body_state={"food": 20})

    warnings = [record for record in caplog.records if "body_state 已弃用" in record.message]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_vpet_event_false_records_only_and_deduplicates(tmp_path) -> None:
    engine = init_db(str(tmp_path / "event_false.db"))
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = Config()

    req = VPetEventRequest(
        event="touch_head",
        count=999,
        body_state={"food": -1, "mode": "Ill", "ignored": True},
        want_reply=False,
        client_event_id="evt-1",
    )
    first = await state.vpet_event_payload(req, client_flags={"touch": True})
    replay = await state.vpet_event_payload(
        VPetEventRequest(event="touch_head", want_reply=True, client_event_id="evt-1")
    )

    assert first == {
        "ok": True,
        "bridge": "vpet-bridge/2",
        "replied": False,
        "gate_reason": None,
        "event_log_id": 1,
    }
    assert replay == first
    with session_scope(engine) as s:
        rows = s.query(VPetEvent).all()
        assert len(rows) == 1
        assert rows[0].count == 50
        assert json.loads(rows[0].body_state_json or "{}") == {"food": 0, "mode": "Ill"}
        assert json.loads(rows[0].client_flags_json or "{}") == {"touch": True}


@pytest.mark.asyncio
async def test_vpet_event_gate_reasons(tmp_path) -> None:
    engine = init_db(str(tmp_path / "event_gates.db"))
    cfg = Config()
    cfg.vpet.touch_escalation_daily_limit = 1
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg

    disabled = await state.vpet_event_payload(
        VPetEventRequest(event="touch_head", want_reply=True, client_event_id="disabled")
    )
    assert disabled["gate_reason"] == "escalation_disabled"

    cfg.vpet.touch_escalation = True
    await state.agent_lock.acquire()
    try:
        busy = await state.vpet_event_payload(
            VPetEventRequest(
                event="touch_head",
                context={"window_count": 5},
                want_reply=True,
                client_event_id="busy",
            )
        )
    finally:
        state.agent_lock.release()
    assert busy["gate_reason"] == "agent_busy"

    row, _ = record_vpet_event(
        engine,
        event="touch_head",
        count=1,
        want_reply=True,
        server_flags={"touch_escalation": True},
    )
    mark_vpet_event_result(engine, row["id"], escalated=True, replied=True)
    budget = await state.vpet_event_payload(
        VPetEventRequest(
            event="touch_head",
            context={"window_count": 5},
            want_reply=True,
            client_event_id="budget",
        )
    )
    assert budget["gate_reason"] == "budget_exceeded"


@pytest.mark.asyncio
async def test_vpet_event_escalates_with_agent_event_mode(tmp_path) -> None:
    engine = init_db(str(tmp_path / "event_pass.db"))
    cfg = Config()
    cfg.vpet.touch_escalation = True
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    state.agent = _FakeEventAgent(engine)

    payload = await state.vpet_event_payload(
        VPetEventRequest(event="touch_head", count=7, want_reply=True, client_event_id="pass")
    )

    assert payload["ok"] is True
    assert payload["replied"] is True
    assert payload["speech"]["text"] == "轻点嘛,我在呢。"
    assert state.agent.calls[0]["source"] == "vpet_event"
    assert state.agent.calls[0]["enable_tools"] is False
    assert "摸了摸你的头" in state.agent.calls[0]["user_input"]
    with session_scope(engine) as s:
        event = s.query(VPetEvent).one()
        assert event.escalated == 1
        assert event.replied == 1
        assert event.message_id is not None
        user = s.query(Message).filter(Message.role == "user").one()
        meta = json.loads(user.meta_json or "{}")
        assert meta["source"] == "vpet_event"
        assert meta["vpet"]["event"] == "touch_head"
        assert meta["vpet"]["count"] == 7
        assert meta["vpet"]["client_event_id"] == "pass"


def test_vpet_pending_drain_digest_three_way(tmp_path) -> None:
    engine = init_db(str(tmp_path / "drain_digest.db"))
    cfg = Config()
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    old = utcnow().replace(microsecond=0) - timedelta(minutes=180)

    enqueue(engine, source="reminder", content="提醒:喝水", scheduled_at=old)
    enqueue(engine, source="greeting", content="早上好呀", scheduled_at=old)
    enqueue(engine, source="nudge", content="起来走走?", scheduled_at=old)
    enqueue(engine, source="body_murmur", content="有点困", scheduled_at=old)

    payload = state.vpet_pending_payload(drain=True, digest=True)
    repeat = state.vpet_pending_payload(drain=True, digest=False)

    assert payload["drained"] is True
    assert len(payload["events"]) == 1
    assert payload["events"][0]["source"] == "reminder"
    assert payload["events"][0]["speech"]["interrupt"] is False
    assert payload["events"][0]["speech"]["persistent"] is True
    assert payload["digest"] == {
        "text": "你不在的时候我攒了两件事:一个提醒、还有一次想叫你歇会儿。",
        "sources": ["reminder", "nudge"],
        "discarded_count": 2,
    }
    assert payload["server_flags"] == {
        "physio_injection": False,
        "touch_escalation": False,
        "physical_proactive": False,
    }
    assert repeat["events"] == []
    with session_scope(engine) as s:
        telemetry = [row.event for row in s.query(VPetEvent).order_by(VPetEvent.id.asc()).all()]
        assert telemetry == [
            "pending_overdue",
            "pending_discarded",
            "pending_digested",
            "pending_discarded",
        ]
        assert {row.day_index for row in s.query(VPetEvent).all()} == {1}


@pytest.mark.asyncio
async def test_vpet_event_ignores_body_state_and_shortens_reply(tmp_path) -> None:
    engine = init_db(str(tmp_path / "event_body.db"))
    cfg = Config()
    cfg.vpet.touch_escalation = True
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    state.agent = _FakeEventAgent(
        engine,
        response_text="我刚吃饱了,现在一点都不饿。顺便说,这句话应该被截掉。",
    )

    payload = await state.vpet_event_payload(
        VPetEventRequest(
            event="touch_head",
            count=7,
            body_state={"food": 20},
            want_reply=True,
            client_event_id="short-body",
        )
    )
    replay = await state.vpet_event_payload(
        VPetEventRequest(event="touch_head", want_reply=True, client_event_id="short-body")
    )
    aggregate_flush = await state.vpet_event_payload(
        VPetEventRequest(
            event="touch_head",
            count=9,
            want_reply=False,
            client_event_id="short-body",
        )
    )

    assert payload["speech"]["text"] == "我刚吃饱了,现在一点都不饿。"
    assert len(payload["speech"]["text"]) <= 28
    assert replay["speech"]["text"] == payload["speech"]["text"]
    assert aggregate_flush["replied"] is True
    assert aggregate_flush["speech"]["text"] == payload["speech"]["text"]
    assert aggregate_flush["event_log_id"] == 1
    assert state.agent.calls[0]["body_state"] is None
    assert state.agent.calls[0]["physio"] is None
    assert state.agent.calls[0]["meta"]["vpet"]["body_state_present"] is True
    assert state.agent.calls[0]["meta"]["vpet"]["body_state_used"] is False
    with session_scope(engine) as s:
        rows = s.query(VPetEvent).order_by(VPetEvent.id.asc()).all()
        assert rows[0].event == "touch_head"
        assert rows[0].count == 7
        assert rows[0].day_index == 1
        assert len(rows) == 1
        assistant = s.query(Message).filter(Message.role == "assistant").one()
        assert assistant.content == payload["speech"]["text"]


@pytest.mark.asyncio
async def test_user_back_generates_memory_aware_greeting_without_touch_budget(tmp_path) -> None:
    engine = init_db(str(tmp_path / "user-back.db"))
    cfg = Config()
    cfg.vpet.touch_escalation = False
    cfg.vpet.physical_proactive = True
    cfg.vpet.touch_escalation_daily_limit = 0
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    state.agent = _FakeEventAgent(engine, response_text="来了？周报接着弄吧。")

    payload = await state.vpet_event_payload(
        VPetEventRequest(
            event="user_back",
            want_reply=True,
            client_event_id="user-back-1",
        )
    )

    assert payload["replied"] is True
    assert "周报" in payload["speech"]["text"]
    assert "living_state.recent_self_event" in state.agent.calls[0]["user_input"]
    with session_scope(engine) as session:
        row = session.query(VPetEvent).filter(VPetEvent.event == "user_back").one()
        assert row.replied == 1
        assert row.escalated == 1
    assert count_vpet_escalations_today(engine) == 0


@pytest.mark.asyncio
async def test_touch_restart_cannot_repeat_first_touch_escalation(tmp_path) -> None:
    engine = init_db(str(tmp_path / "touch-restart.db"))
    cfg = Config()
    cfg.vpet.touch_escalation = True
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    state.agent = _FakeEventAgent(engine)

    first = await state.vpet_event_payload(
        VPetEventRequest(
            event="touch_head",
            context={"window_count": 1},
            want_reply=True,
            client_event_id="touch-first",
        )
    )
    restarted_first = await state.vpet_event_payload(
        VPetEventRequest(
            event="touch_head",
            context={"window_count": 1},
            want_reply=True,
            client_event_id="touch-restarted",
        )
    )
    threshold = await state.vpet_event_payload(
        VPetEventRequest(
            event="touch_head",
            context={"window_count": 5},
            want_reply=True,
            client_event_id="touch-threshold",
        )
    )

    assert first["replied"] is True
    assert restarted_first["replied"] is False
    assert restarted_first["gate_reason"] == "touch_not_eligible"
    assert threshold["replied"] is True
    assert len(state.agent.calls) == 2
    assert "共 5 次" in state.agent.calls[1]["user_input"]


@pytest.mark.asyncio
async def test_vpet_chat_records_telemetry_and_ignores_legacy_body_state(tmp_path) -> None:
    engine = init_db(str(tmp_path / "chat_telemetry.db"))
    cfg = Config()
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    state.agent = _FakeChatAgent(engine, response_text="我刚吃饱了,不饿。")

    payload = await state.vpet_chat_payload(
        "饿了吗",
        event="user_chat",
        body_state={"food": 10},
        client_flags={"body_state_injection": True},
    )

    assert payload["speech"]["text"] == "我刚吃饱了,不饿。"
    with session_scope(engine) as s:
        rows = s.query(VPetEvent).order_by(VPetEvent.id.asc()).all()
        assert [row.event for row in rows] == ["user_chat"]
        assert rows[0].replied == 1
        assert rows[0].turn_id == "turn-chat"
        assert rows[0].message_id is not None
        assert rows[0].day_index == 1
        assert json.loads(rows[0].body_state_json or "{}") == {"food": 10}
        assert json.loads(rows[0].client_flags_json or "{}") == {
            "body_state_injection": True
        }


@pytest.mark.asyncio
async def test_vpet_chat_conflict_guard_reads_engine_physio(tmp_path) -> None:
    engine = init_db(str(tmp_path / "chat_physio_conflict.db"))
    cfg = Config()
    cfg.physio.enabled = True
    cfg.vpet.physio_injection = True
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    state.physio = PhysioEngine(engine, cfg.physio)
    state.agent = _FakeChatAgent(engine, response_text="我刚吃饱了,一点都不饿。")
    state.physio.snapshot()
    with session_scope(engine) as session:
        physio_row = session.get(PhysioState, 1)
        assert physio_row is not None
        physio_row.hunger = 10

    await state.vpet_chat_payload("饿了吗", event="user_chat")

    with session_scope(engine) as session:
        conflict = (
            session.query(VPetEvent).filter(VPetEvent.event == "body_state_conflict").one()
        )
        context = json.loads(conflict.context_json or "{}")
        assert "hunger_low_but_reply_satiated" in context["reasons"]
        assert context["physio"]["hunger"] <= 30


@pytest.mark.asyncio
async def test_vpet_chat_does_not_drain_pending_while_physio_is_sleeping(
    tmp_path, monkeypatch
) -> None:
    engine = init_db(str(tmp_path / "sleep_pending.db"))
    cfg = Config()
    cfg.physio.enabled = True
    cfg.vpet.physio_injection = True
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    state.physio = PhysioEngine(engine, cfg.physio)
    state.agent = _FakeChatAgent(engine)
    enqueue(engine, content="夜里不该主动冒出来", source="nudge")
    sleeping_utc = datetime(2026, 7, 11, 17, 0)
    monkeypatch.setattr("mybuddy.body.physio.utcnow", lambda: sleeping_utc)
    monkeypatch.setattr(
        "mybuddy.body.physio.localnow",
        lambda: datetime(2026, 7, 12, 1, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    payload = await state.vpet_chat_payload("晚安", event="user_chat")

    assert payload["pending"] == []
    assert state.agent.calls[0]["physio"]["sleeping"] is True
    with session_scope(engine) as session:
        pending = session.query(api_module.PendingMessage).one()
        assert pending.delivered_at is None


@pytest.mark.asyncio
async def test_vpet_event_records_latest_emotion_label(tmp_path) -> None:
    engine = init_db(str(tmp_path / "event_emotion.db"))
    cfg = Config()
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    tracker = EmotionTracker(window=5)
    tracker.add(EmotionResult(label="negative", strength=0.8, reason="压力"))
    state.agent = SimpleNamespace(_emotion_tracker=tracker)

    await state.vpet_event_payload(
        VPetEventRequest(event="touch_head", want_reply=False, client_event_id="emotion")
    )

    with session_scope(engine) as s:
        row = s.query(VPetEvent).one()
        assert row.last_emotion_label == "negative"


@pytest.mark.asyncio
async def test_feed_updates_physio_once_and_aggregates_shared_moment(tmp_path, monkeypatch) -> None:
    # This test asserts feed deltas, not the sleep-window wake penalty. Freeze it at
    # 12:00 Asia/Shanghai so it stays deterministic when the suite runs overnight.
    monkeypatch.setattr(
        "mybuddy.body.physio.utcnow",
        lambda: datetime(2026, 7, 12, 4, 0),
    )
    engine = init_db(str(tmp_path / "feed.db"))
    cfg = Config()
    cfg.physio.enabled = True
    cfg.vpet.physio_injection = True
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    state.physio = PhysioEngine(engine, cfg.physio)
    state.ltm = LongTermMemory(persist_dir=tmp_path / "memory")

    first = await state.vpet_event_payload(
        VPetEventRequest(
            event="feed",
            context={"item": "curry"},
            client_event_id="feed-1",
        )
    )
    duplicate = await state.vpet_event_payload(
        VPetEventRequest(
            event="feed",
            context={"item": "curry"},
            client_event_id="feed-1",
        )
    )
    second = await state.vpet_event_payload(
        VPetEventRequest(
            event="feed",
            context={"item": "milk_tea"},
            client_event_id="feed-2",
        )
    )

    assert first["physio"]["mood"] == 64
    assert duplicate["event_log_id"] == first["event_log_id"]
    assert duplicate["physio"] == first["physio"]
    assert second["physio"]["mood"] == 70
    moments = state.ltm.list_all(mem_type="shared_moment")
    assert len(moments) == 1
    assert "咖喱饭" in moments[0]["content"]
    assert "奶茶" in moments[0]["content"]
    with session_scope(engine) as session:
        assert session.query(VPetEvent).filter(VPetEvent.event == "feed").count() == 2


@pytest.mark.asyncio
async def test_failed_feed_memory_is_repaired_from_committed_event(tmp_path, monkeypatch) -> None:
    engine = init_db(str(tmp_path / "feed-repair.db"))
    cfg = Config()
    cfg.physio.enabled = True
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    state.physio = PhysioEngine(engine, cfg.physio)
    state.ltm = LongTermMemory(persist_dir=tmp_path / "repair-memory")
    original_add = state.ltm.add
    monkeypatch.setattr(state.ltm, "add", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")))

    await state.vpet_event_payload(
        VPetEventRequest(
            event="feed",
            context={"item": "curry"},
            client_event_id="repair-feed",
        )
    )
    with session_scope(engine) as session:
        row = session.query(VPetEvent).filter(VPetEvent.event == "feed").one()
        assert json.loads(row.context_json or "{}")["memory_pending"] is True

    monkeypatch.setattr(state.ltm, "add", original_add)
    state._repair_pending_shared_moments()

    moments = state.ltm.list_all(mem_type="shared_moment")
    assert len(moments) == 1
    assert "咖喱饭" in moments[0]["content"]
    with session_scope(engine) as session:
        row = session.query(VPetEvent).filter(VPetEvent.event == "feed").one()
        assert json.loads(row.context_json or "{}")["memory_pending"] is False


@pytest.mark.asyncio
async def test_audit_events_are_idempotent_and_server_enriched(tmp_path) -> None:
    engine = init_db(str(tmp_path / "audit.db"))
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = Config()

    heartbeat = VPetEventRequest(
        event="presence_heartbeat",
        count=999,
        context={"idle_seconds": 3, "fullscreen": False},
        client_event_id="heartbeat-1",
    )
    shown = VPetEventRequest(
        event="notice_shown",
        context={"pending_id": 4, "source": "nudge", "shown_at": "client-value"},
        client_event_id="shown-1",
    )
    await state.vpet_event_payload(heartbeat)
    await state.vpet_event_payload(heartbeat)
    await state.vpet_event_payload(shown)

    with session_scope(engine) as session:
        rows = session.query(VPetEvent).order_by(VPetEvent.id).all()
        assert [row.event for row in rows] == ["presence_heartbeat", "notice_shown"]
        assert rows[0].count == 20
        context = json.loads(rows[0].context_json or "{}")
        assert context["local_date"]
        assert context["server_time"]
        assert context["client_event_id"] == "heartbeat-1"
        shown_context = json.loads(rows[1].context_json or "{}")
        assert shown_context["client_shown_at"] == "client-value"
        assert shown_context["shown_at"] != "client-value"


@pytest.mark.asyncio
async def test_next_day_interaction_flushes_touch_shared_moment(tmp_path) -> None:
    engine = init_db(str(tmp_path / "touch-memory.db"))
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = Config()
    state.ltm = LongTermMemory(persist_dir=tmp_path / "touch-memory")
    with session_scope(engine) as session:
        session.add(PhysioDaily(local_date="2026-07-10", touch_count=7))

    await state.vpet_event_payload(
        VPetEventRequest(
            event="presence_heartbeat",
            count=1,
            client_event_id="next-day",
        )
    )

    moments = state.ltm.list_all(mem_type="shared_moment")
    assert len(moments) == 1
    assert "7 次" in moments[0]["content"]
    with session_scope(engine) as session:
        daily = session.get(PhysioDaily, "2026-07-10")
        assert daily is not None
        assert daily.touch_memory_written is True


@pytest.mark.asyncio
async def test_cowork_start_schedules_and_stop_cancels(tmp_path) -> None:
    engine = init_db(str(tmp_path / "cowork.db"))
    scheduler = _FakeCoworkScheduler()
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = Config()
    state.scheduler = scheduler

    started = await state.vpet_event_payload(
        VPetEventRequest(
            event="work_start",
            context={"session_id": "work-1"},
            client_event_id="work-start-1",
        )
    )
    assert started["replied"] is False
    assert scheduler.scheduled[0][0] == "work-1"
    assert state.vpet_state_payload()["idle_hint"] == "work"

    stopped = await state.vpet_event_payload(
        VPetEventRequest(
            event="work_stop",
            context={"session_id": "work-1"},
            client_event_id="work-stop-1",
        )
    )
    assert stopped["replied"] is True
    assert stopped["speech"]["text"]
    replay = await state.vpet_event_payload(
        VPetEventRequest(
            event="work_stop",
            context={"session_id": "work-1"},
            client_event_id="work-stop-1",
        )
    )
    assert replay["speech"]["text"] == stopped["speech"]["text"]
    assert replay["duration_minutes"] == stopped["duration_minutes"]
    repeated_stop = await state.vpet_event_payload(
        VPetEventRequest(
            event="work_stop",
            context={"session_id": "work-1"},
            client_event_id="work-stop-2",
        )
    )
    assert repeated_stop["replied"] is False
    assert repeated_stop["gate_reason"] == "unknown_work_session"
    assert scheduler.cancelled == ["work-1"]
    assert state.vpet_state_payload()["idle_hint"] != "work"


@pytest.mark.asyncio
async def test_unknown_stop_does_not_close_another_open_cowork_session(tmp_path) -> None:
    engine = init_db(str(tmp_path / "cowork-unknown.db"))
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = Config()

    await state.vpet_event_payload(
        VPetEventRequest(
            event="work_start",
            context={"session_id": "open-session"},
            client_event_id="open-start",
        )
    )
    unknown = await state.vpet_event_payload(
        VPetEventRequest(
            event="work_stop",
            context={"session_id": "other-session"},
            client_event_id="other-stop",
        )
    )

    assert unknown["gate_reason"] == "unknown_work_session"
    assert state.vpet_state_payload()["idle_hint"] == "work"


def test_web_token_auth_guards_api_and_v1_but_not_root() -> None:
    cfg = Config()
    cfg.vpet.bridge_token = "secret"
    state = AppState(config_path="config.yaml")
    state.cfg = cfg

    assert _authorize(state, "/api/status", token="") == (False, 401)
    assert _authorize(state, "/v1/chat/completions", token="") == (False, 401)
    assert _authorize(state, "/", token="") == (True, None)
    assert _authorize(state, "/api/status", token="secret") == (True, None)


def test_experiment_server_flags_follow_frozen_schedule(monkeypatch) -> None:
    cfg = Config()

    monkeypatch.setattr(api_module, "_localnow", lambda: datetime(2026, 8, 4, tzinfo=UTC))
    assert api_module._server_flags(cfg) == {
        "physio_injection": True,
        "touch_escalation": True,
        "physical_proactive": True,
    }

    monkeypatch.setattr(api_module, "_localnow", lambda: datetime(2026, 8, 5, tzinfo=UTC))
    assert api_module._server_flags(cfg)["touch_escalation"] is False

    monkeypatch.setattr(api_module, "_localnow", lambda: datetime(2026, 8, 11, tzinfo=UTC))
    assert api_module._server_flags(cfg)["physical_proactive"] is False


@pytest.mark.asyncio
async def test_first_event_of_experiment_day_records_flags_change(tmp_path, monkeypatch) -> None:
    engine = init_db(str(tmp_path / "flags-change.db"))
    cfg = Config()
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    monkeypatch.setattr(api_module, "_localnow", lambda: datetime(2026, 8, 4, tzinfo=UTC))

    await state.vpet_event_payload(
        VPetEventRequest(event="presence_heartbeat", client_event_id="flags-heartbeat-1")
    )
    await state.vpet_event_payload(
        VPetEventRequest(event="presence_heartbeat", client_event_id="flags-heartbeat-2")
    )

    with session_scope(engine) as session:
        rows = session.query(VPetEvent).order_by(VPetEvent.id).all()
        assert [row.event for row in rows] == [
            "flags_changed",
            "presence_heartbeat",
            "presence_heartbeat",
        ]


class _FakeEventAgent:
    session_id = "fake-session"

    def __init__(self, engine: Any, response_text: str = "轻点嘛,我在呢。") -> None:
        self.engine = engine
        self.response_text = response_text
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        user_input: str,
        *,
        source: str = "chat",
        enable_tools: bool = True,
        meta: dict[str, Any] | None = None,
        physio: dict[str, Any] | None = None,
        body_state: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "user_input": user_input,
                "source": source,
                "enable_tools": enable_tools,
                "meta": meta,
                "physio": physio,
                "body_state": body_state,
            }
        )
        turn_id = "turn-event"
        append_message(
            self.engine,
            session_id=self.session_id,
            role="user",
            content=user_input,
            meta={"turn_id": turn_id, "source": source, **(meta or {})},
        )
        append_message(
            self.engine,
            session_id=self.session_id,
            role="assistant",
            content=self.response_text,
            meta={"turn_id": turn_id, "source": source, **(meta or {})},
        )
        return SimpleNamespace(
            text=self.response_text,
            steps=1,
            finish_reason="stop",
            trajectory=SimpleNamespace(turn_id=turn_id),
            tool_calls=[],
            emotion=None,
            emotional_support={"mode": "neutral"},
            triggered_skills=[],
            search_sources=[],
        )


class _FakeChatAgent:
    session_id = "fake-chat"

    def __init__(self, engine: Any, response_text: str = "我在。") -> None:
        self.engine = engine
        self.response_text = response_text
        self.calls: list[dict[str, Any]] = []
        self._memory = SimpleNamespace(add_message=lambda _message: None)

    async def run(
        self,
        user_input: str,
        *,
        source: str = "chat",
        enable_tools: bool = True,
        meta: dict[str, Any] | None = None,
        physio: dict[str, Any] | None = None,
        body_state: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "user_input": user_input,
                "source": source,
                "enable_tools": enable_tools,
                "meta": meta,
                "physio": physio,
                "body_state": body_state,
            }
        )
        turn_id = "turn-chat"
        append_message(
            self.engine,
            session_id=self.session_id,
            role="user",
            content=user_input,
            meta={"turn_id": turn_id, "source": source, **(meta or {})},
        )
        append_message(
            self.engine,
            session_id=self.session_id,
            role="assistant",
            content=self.response_text,
            meta={"turn_id": turn_id, "source": source},
        )
        return SimpleNamespace(
            text=self.response_text,
            steps=1,
            finish_reason="stop",
            trajectory=SimpleNamespace(turn_id=turn_id),
            tool_calls=[],
            emotion=None,
            emotional_support={"mode": "neutral"},
            triggered_skills=[],
            search_sources=[],
        )


async def _run_vpet_chat_meta(
    db_path: Any,
    *,
    enabled: bool,
    body_state: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    engine = init_db(str(db_path))
    cfg = Config()
    cfg.physio.enabled = enabled
    cfg.vpet.physio_injection = enabled
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
    state.physio = PhysioEngine(engine, cfg.physio) if enabled else None
    state.agent = _FakeChatAgent(engine)

    await state.vpet_chat_payload("饿了", body_state=body_state)

    with session_scope(engine) as s:
        row = s.query(Message).filter(Message.role == "user").one()
        meta = json.loads(row.meta_json or "{}")
    return meta, state.agent.calls[0]


def _authorize(state: AppState, path: str, *, token: str) -> tuple[bool, int | None]:
    handler = DemoHandler.__new__(DemoHandler)
    handler.server = SimpleNamespace(state=state)
    handler.path = path
    handler.headers = {"X-MyBuddy-Token": token}
    seen: dict[str, int] = {}

    def _send_error(status: Any, _detail: str) -> None:
        seen["status"] = int(status)

    handler._send_error = _send_error  # type: ignore[method-assign]
    return DemoHandler._authorize_request(handler), seen.get("status")


class _FakeCoworkScheduler:
    running = True

    def __init__(self) -> None:
        self.scheduled: list[tuple[str, Any]] = []
        self.cancelled: list[str] = []

    def schedule_cowork_break(self, *, session_id: str, run_at: Any) -> None:
        self.scheduled.append((session_id, run_at))

    def cancel_cowork_break(self, session_id: str) -> bool:
        self.cancelled.append(session_id)
        return True
