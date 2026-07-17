from __future__ import annotations

import json

import pytest

from mybuddy.api import (
    AppState,
    _integrate_pending_messages,
    create_app,
)
from mybuddy.config import Config
from mybuddy.storage import (
    Message,
    enqueue,
    init_db,
    session_scope,
)


def test_vpet_business_errors_use_protocol_v2_shape(tmp_path) -> None:
    pytest.importorskip("fastapi")  # FastAPI 路径专属;未装 api extra 时跳过
    from fastapi.testclient import TestClient

    app = create_app(str(tmp_path / "unused.yaml"))
    state = app.state.mybuddy
    state.cfg = Config()
    state.engine = init_db(str(tmp_path / "errors.db"))
    client = TestClient(app)

    invalid = client.post("/api/vpet/event", json={"event": "not-an-event"})
    chat = client.post("/api/vpet/chat", json={"message": "在吗"})

    assert invalid.status_code == 400
    assert invalid.json() == {
        "ok": False,
        "error": {"code": "invalid_request", "message": "unsupported vpet event"},
    }
    assert chat.status_code == 400
    assert chat.json()["error"]["code"] == "llm_not_configured"

    state.cfg.vpet.bridge_token = "secret"
    assert client.get("/api/vpet/state").status_code == 401
    authorized = client.get(
        "/api/vpet/state",
        headers={"X-MyBuddy-Token": "secret"},
    )
    assert authorized.status_code == 200
    assert authorized.json()["bridge"] == "vpet-bridge/2"


def test_dead_admin_endpoints_are_gone(tmp_path) -> None:
    """第二刀回归:面向已删前端的管理端点与 OpenAI 兼容层不再暴露。"""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app = create_app(str(tmp_path / "unused.yaml"))
    state = app.state.mybuddy
    state.cfg = Config()
    state.engine = init_db(str(tmp_path / "gone.db"))
    client = TestClient(app)

    for method, path in [
        ("get", "/api/persona"),
        ("get", "/api/messages"),
        ("get", "/api/users"),
        ("get", "/api/profile"),
        ("get", "/api/memory"),
        ("get", "/api/reminders"),
        ("get", "/api/skills"),
        ("get", "/api/notes"),
        ("post", "/v1/chat/completions"),
    ]:
        resp = getattr(client, method)(path)
        assert resp.status_code == 404, f"{method} {path} 应已删除"


@pytest.mark.asyncio
async def test_vpet_chat_payload_maps_chat_result(monkeypatch) -> None:
    state = AppState(config_path="config.yaml")

    async def fake_chat_payload(message: str) -> dict:
        assert message == "今天有点撑不住"
        return {
            "text": "先坐会儿,别硬顶。",
            "turn_id": "turn-vpet",
            "finish_reason": "stop",
            "emotion": {"label": "negative", "strength": 0.8, "reason": "疲惫"},
            "emotional_support": {"mode": "strong_support"},
            "tool_calls": [],
            "triggered_skills": [],
            "pending_messages": [
                {
                    "id": 7,
                    "source": "greeting",
                    "content": "早上好呀",
                    "scheduled_at": "2026-06-05T09:17:00",
                }
            ],
        }

    monkeypatch.setattr(state, "chat_payload", fake_chat_payload)

    payload = await state.vpet_chat_payload("今天有点撑不住", event="user_chat")

    assert payload["bridge"] == "vpet-bridge/2"
    assert payload["speech"]["text"] == "先坐会儿,别硬顶。"
    assert payload["action"]["name"] == "concern"
    assert payload["expression"]["name"] == "worried"
    assert payload["pending"][0]["action"]["name"] == "greet"


def test_vpet_pending_payload_can_peek_and_drain(tmp_path) -> None:
    engine = init_db(str(tmp_path / "vpet_pending.db"))
    state = AppState(config_path="config.yaml")
    state.engine = engine
    enqueue(engine, source="nudge", content="那股压力现在还压着吗?", meta={"origin": "nudge"})

    peek = state.vpet_pending_payload(drain=False)
    drained = state.vpet_pending_payload(drain=True)
    after = state.vpet_pending_payload(drain=False)

    assert peek["drained"] is False
    assert peek["events"][0]["action"]["name"] == "concern"
    assert peek["events"][0]["speech"]["interrupt"] is False
    assert drained["drained"] is True
    assert drained["events"][0]["expression"]["name"] == "worried"
    assert after["events"] == []


def test_integrate_pending_nudge_as_assistant_message(tmp_path) -> None:
    engine = init_db(str(tmp_path / "pending_nudge.db"))
    pending_id = enqueue(
        engine,
        source="nudge",
        content="刚才那件事,要不要接着放到桌上?",
        meta={"origin": "silence_followup"},
    )
    seen = []

    integrated = _integrate_pending_messages(
        engine,
        session_id="s1",
        items=[
            {
                "id": pending_id,
                "source": "nudge",
                "content": "刚才那件事,要不要接着放到桌上?",
                "scheduled_at": "2026-06-05T10:00:00",
                "meta": {"origin": "silence_followup"},
            }
        ],
        add_to_short_term=seen.append,
    )

    assert integrated[0]["role"] == "assistant"
    assert isinstance(integrated[0]["message_id"], int)
    assert seen[0].role.value == "assistant"
    assert seen[0].content == "刚才那件事,要不要接着放到桌上?"
    with session_scope(engine) as s:
        row = s.query(Message).one()
        assert row.session_id == "s1"
        assert row.role == "assistant"
        assert row.content == "刚才那件事,要不要接着放到桌上?"
        meta = json.loads(row.meta_json)
        assert meta["source"] == "pending_message"
        assert meta["pending_source"] == "nudge"


def test_integrate_pending_system_source_stays_side_effect(tmp_path) -> None:
    """非对话型主动消息(如久坐提醒)保持 system 语义,不进短期记忆。"""
    engine = init_db(str(tmp_path / "pending_system.db"))
    seen = []

    integrated = _integrate_pending_messages(
        engine,
        session_id="s1",
        items=[
            {
                "id": 1,
                "source": "cowork_break",
                "content": "坐得够久了。起来活动一下吧。",
                "scheduled_at": "2026-06-05T10:00:00",
                "meta": {"origin": "cowork_50m"},
            }
        ],
        add_to_short_term=seen.append,
    )

    assert integrated[0]["role"] == "system"
    assert seen == []
    with session_scope(engine) as s:
        assert s.query(Message).count() == 0
