from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from mybuddy._time import utcnow
from mybuddy.agent.context import build_system_prompt
from mybuddy.agent.living_state import synthesize_living_state
from mybuddy.api import AppState, VPetEventRequest
from mybuddy.config import Config, PersonaConfig
from mybuddy.emotion import EmotionResult, EmotionTracker
from mybuddy.integrations.vpet import normalize_body_state
from mybuddy.storage import (
    Message,
    VPetEvent,
    append_message,
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


def test_body_state_injection_low_food_replaces_fictional_body_status(tmp_path) -> None:
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
        body_state={"food": 20, "drink": 80, "feeling": 55},
        body_state_injection=True,
    )
    prompt = build_system_prompt(PersonaConfig(), life=life)

    assert "肚子有点空" in life.today_status
    assert "语气会更黏一点" in life.current_mood
    assert "论文" in life.recent_self_event
    assert "肚子有点空" in prompt
    assert "杯水" not in prompt
    assert "刚吃" not in prompt


def test_body_state_injection_off_keeps_living_state_equivalent(tmp_path) -> None:
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
    with_body_disabled = synthesize_living_state(
        PersonaConfig(),
        **kwargs,
        body_state={"food": 5, "strength": 5, "feeling": 5},
        body_state_injection=False,
    )

    assert with_body_disabled.model_dump() == without_body.model_dump()


@pytest.mark.asyncio
async def test_vpet_chat_body_state_used_meta_tracks_actual_injection(tmp_path) -> None:
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
    assert used["vpet"]["body_state_used"] is True
    assert used_call["body_state"] == {"food": 20}
    assert disabled["vpet"]["body_state_present"] is True
    assert disabled["vpet"]["body_state_used"] is False
    assert disabled_call["body_state"] is None
    assert missing["vpet"]["body_state_present"] is False
    assert missing["vpet"]["body_state_used"] is False
    assert missing_call["body_state"] is None


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

    assert first == {"ok": True, "replied": False, "gate_reason": None, "event_log_id": 1}
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
            VPetEventRequest(event="touch_head", want_reply=True, client_event_id="busy")
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
        VPetEventRequest(event="touch_head", want_reply=True, client_event_id="budget")
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
        "discarded_count": 1,
    }
    assert repeat["events"] == []
    with session_scope(engine) as s:
        telemetry = [row.event for row in s.query(VPetEvent).order_by(VPetEvent.id.asc()).all()]
        assert telemetry == ["pending_overdue", "pending_discarded", "pending_digested"]
        assert {row.day_index for row in s.query(VPetEvent).all()} == {1}


@pytest.mark.asyncio
async def test_vpet_event_uses_body_state_and_shortens_reply(tmp_path) -> None:
    engine = init_db(str(tmp_path / "event_body.db"))
    cfg = Config()
    cfg.vpet.touch_escalation = True
    cfg.vpet.body_state_injection = True
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
    assert aggregate_flush == {
        "ok": True,
        "replied": False,
        "gate_reason": None,
        "event_log_id": 1,
    }
    assert state.agent.calls[0]["body_state"] == {"food": 20}
    assert state.agent.calls[0]["meta"]["vpet"]["body_state_present"] is True
    assert state.agent.calls[0]["meta"]["vpet"]["body_state_used"] is True
    with session_scope(engine) as s:
        rows = s.query(VPetEvent).order_by(VPetEvent.id.asc()).all()
        assert rows[0].event == "touch_head"
        assert rows[0].count == 9
        assert rows[0].day_index == 1
        assert rows[1].event == "body_state_conflict"
        conflict_context = json.loads(rows[1].context_json or "{}")
        assert conflict_context["source_event"] == "touch_head"
        assert "food_low_but_reply_satiated" in conflict_context["reasons"]
        assistant = s.query(Message).filter(Message.role == "assistant").one()
        assert assistant.content == payload["speech"]["text"]


@pytest.mark.asyncio
async def test_vpet_chat_records_telemetry_and_body_conflicts(tmp_path) -> None:
    engine = init_db(str(tmp_path / "chat_telemetry.db"))
    cfg = Config()
    cfg.vpet.body_state_injection = True
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
        assert [row.event for row in rows] == ["user_chat", "body_state_conflict"]
        assert rows[0].replied == 1
        assert rows[0].turn_id == "turn-chat"
        assert rows[0].message_id is not None
        assert rows[0].day_index == 1
        assert json.loads(rows[0].body_state_json or "{}") == {"food": 10}
        assert json.loads(rows[0].client_flags_json or "{}") == {
            "body_state_injection": True
        }
        conflict_context = json.loads(rows[1].context_json or "{}")
        assert conflict_context["source_event"] == "user_chat"
        assert "food_low_but_reply_satiated" in conflict_context["reasons"]


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


def test_web_token_auth_guards_api_and_v1_but_not_root() -> None:
    cfg = Config()
    cfg.vpet.bridge_token = "secret"
    state = AppState(config_path="config.yaml")
    state.cfg = cfg

    assert _authorize(state, "/api/status", token="") == (False, 401)
    assert _authorize(state, "/v1/chat/completions", token="") == (False, 401)
    assert _authorize(state, "/", token="") == (True, None)
    assert _authorize(state, "/api/status", token="secret") == (True, None)


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
        body_state: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "user_input": user_input,
                "source": source,
                "enable_tools": enable_tools,
                "meta": meta,
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
        body_state: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "user_input": user_input,
                "source": source,
                "enable_tools": enable_tools,
                "meta": meta,
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
    cfg.vpet.body_state_injection = enabled
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = cfg
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
