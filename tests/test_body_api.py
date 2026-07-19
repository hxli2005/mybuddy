import json
from datetime import UTC, datetime, timedelta

import pytest

from mybuddy.body_api import create_body_app
from mybuddy.llm import BaseLLMProvider, LLMResponse, ToolCall
from mybuddy.mind import STATIC_CATCH, MindFiles


class StubProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
        self.calls += 1
        incoming = json.loads(messages[0].content)["incoming_experience"]
        is_reading = incoming is not None and incoming["type"] == "self_reading"
        is_ambient_reading = is_reading and "ambient" in kwargs.get("system", "")
        is_touch = incoming is not None and incoming["type"] == "body_touch"
        touch_zone = incoming.get("zone") if is_touch else None
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="submit_mind_bundle",
                    arguments={
                        "state_changes": {
                            "mood": "放松",
                            "energy": "平稳",
                            "attention": "看着刚读到的句子"
                            if is_reading
                            else "感觉到触碰"
                            if is_touch
                            else "听你说话",
                        },
                        "memory_operations": []
                        if is_touch
                        else [
                            {
                                "action": "record",
                                "kind": "self_experience" if is_reading else "user_fact",
                                "content": "读到羁鸟恋旧林时有一点想回到自在处"
                                if is_reading
                                else "用户今天终于忙完了",
                                "evidence_ids": [incoming["id"]],
                                "target_id": None,
                            }
                        ],
                        "expression": "刚读到一句很想回到自在处的话。你今天还好吗？"
                        if is_ambient_reading
                        else None
                        if is_reading
                        else "呀，碰到我头发了。"
                        if touch_zone == "head"
                        else "唔，碰到我衣角了。"
                        if touch_zone == "body"
                        else "忙完就好。先在我这儿松口气。",
                    },
                )
            ]
        )


class UnavailableProvider(BaseLLMProvider):
    async def generate(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
        raise RuntimeError("network down")


class RejectingProvider(BaseLLMProvider):
    async def generate(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
        return LLMResponse(tool_calls=[ToolCall(id="bad", name="submit_mind_bundle", arguments={})])


@pytest.fixture
def api(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    provider = StubProvider()
    app = create_body_app(data_dir=tmp_path, provider=provider)
    return TestClient(app), provider, tmp_path


def _history(data_dir):  # noqa: ANN001, ANN202
    path = data_dir / "history.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_expression_is_non_destructive_and_event_id_is_idempotent(api) -> None:
    client, provider, data_dir = api
    event = {"event_id": "chat-001", "type": "chat", "content": "今天终于忙完了。"}

    first = client.post("/api/body/step", json={"event": event})
    assert first.status_code == 200
    first_body = first.json()
    expression = first_body["expression"]
    before_shown = _history(data_dir)

    repeated = client.post("/api/body/step", json={"event": event})
    assert repeated.status_code == 200
    assert repeated.json()["event_status"] == "duplicate"
    assert repeated.json()["expression"] == expression
    assert _history(data_dir) == before_shown
    assert provider.calls == 1
    assert expression["text"] not in [item["content"] for item in before_shown]

    confirmed = client.post("/api/body/step", json={"shown_id": expression["id"], "event": event})
    assert confirmed.status_code == 200
    assert confirmed.json() == {
        "activity": None,
        "activity_confirmed": False,
        "expression": None,
        "shown_confirmed": True,
        "event_status": "duplicate",
        "time_status": "not_due",
        "mind_status": "not_run",
    }
    after_shown = _history(data_dir)
    assert after_shown[:-1] == before_shown
    assert after_shown[-1]["type"] == "shared_expression"
    assert after_shown[-1]["content"] == expression["text"]
    assert after_shown[-1]["expression_id"] == expression["id"]
    assert provider.calls == 1

    repeated_receipt = client.post("/api/body/step", json={"shown_id": expression["id"]})
    assert repeated_receipt.json()["shown_confirmed"] is False
    assert _history(data_dir) == after_shown


def test_new_event_waits_in_body_until_previous_expression_is_shown(api) -> None:
    client, provider, _ = api
    first = client.post(
        "/api/body/step",
        json={"event": {"event_id": "first", "type": "chat", "content": "第一句"}},
    ).json()
    second = client.post(
        "/api/body/step",
        json={"event": {"event_id": "second", "type": "chat", "content": "第二句"}},
    ).json()

    assert second["event_status"] == "waiting_for_shown"
    assert second["expression"] == first["expression"]
    assert provider.calls == 1


def test_same_step_confirms_shown_before_processing_next_event(api) -> None:
    client, provider, data_dir = api
    first = client.post(
        "/api/body/step",
        json={"event": {"event_id": "first", "type": "chat", "content": "第一句"}},
    ).json()
    second = client.post(
        "/api/body/step",
        json={
            "shown_id": first["expression"]["id"],
            "event": {"event_id": "second", "type": "chat", "content": "第二句"},
        },
    ).json()

    assert second["shown_confirmed"] is True
    assert second["event_status"] == "processed"
    assert second["expression"] is not None
    assert provider.calls == 2
    history = _history(data_dir)
    assert [item["type"] for item in history] == [
        "user_experience",
        "memory_operation",
        "shared_expression",
        "user_experience",
        "memory_operation",
    ]
    assert history[2]["expression_id"] == first["expression"]["id"]
    assert history[3]["content"] == "第二句"


def test_wrong_shown_id_does_not_destroy_pending_expression(api) -> None:
    client, _, _ = api
    first = client.post(
        "/api/body/step",
        json={"event": {"event_id": "first", "type": "chat", "content": "在吗"}},
    ).json()
    wrong = client.post("/api/body/step", json={"shown_id": "expr_wrong"}).json()

    assert wrong["shown_confirmed"] is False
    assert wrong["expression"] == first["expression"]


@pytest.mark.parametrize(
    ("provider", "expected"),
    [(UnavailableProvider(), "unavailable"), (RejectingProvider(), "rejected")],
)
def test_mind_status_does_not_call_a_fallback_connected(provider, expected, tmp_path) -> None:  # noqa: ANN001
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    client = TestClient(create_body_app(data_dir=tmp_path, provider=provider))
    response = client.post(
        "/api/body/step",
        json={"event": {"event_id": "chat-failure", "type": "chat", "content": "在吗"}},
    ).json()

    assert response["event_status"] == "processed"
    assert response["mind_status"] == expected
    assert response["expression"]["text"] == STATIC_CATCH


def test_cross_day_unshown_ambient_is_discarded_without_erasing_life(api) -> None:
    client, provider, data_dir = api
    files = MindFiles(data_dir)
    now = datetime.now(UTC).astimezone()
    state, history, memories = files.load(now)
    history.append({"id": "life-kept", "type": "self_reading", "content": "昨晚读完一页。"})
    memories["items"] = [{"id": "memory-kept", "kind": "self_experience"}]
    state["pending_expression"] = {
        "id": "expr-stale",
        "text": "我刚读完这一页。",
        "created_at": (now - timedelta(days=1)).isoformat(),
        "kind": "ambient",
    }
    files.commit(state, history, memories)

    response = client.post("/api/body/step", json={}).json()
    final_state, final_history, final_memories = files.load(now)

    assert response["expression"] is None
    assert response["mind_status"] == "not_run"
    assert final_state["pending_expression"] is None
    assert final_history == history
    assert final_memories == memories
    assert provider.calls == 0


def test_stale_ambient_with_matching_receipt_is_confirmed_before_discard(api) -> None:
    client, provider, data_dir = api
    files = MindFiles(data_dir)
    now = datetime.now(UTC).astimezone()
    state, history, memories = files.load(now)
    state["pending_expression"] = {
        "id": "expr-stale-shown",
        "text": "昨晚真正显示过的话。",
        "created_at": (now - timedelta(days=1)).isoformat(),
        "kind": "ambient",
    }
    files.commit(state, history, memories)

    response = client.post("/api/body/step", json={"shown_id": "expr-stale-shown"}).json()

    assert response["shown_confirmed"] is True
    assert _history(data_dir)[-1]["content"] == "昨晚真正显示过的话。"
    assert provider.calls == 0


@pytest.mark.parametrize("kind", ["direct", "ambient"])
def test_direct_or_same_day_expression_is_not_discarded(api, kind) -> None:  # noqa: ANN001
    client, provider, data_dir = api
    files = MindFiles(data_dir)
    now = datetime.now(UTC).astimezone()
    state, history, memories = files.load(now)
    created_at = (now - timedelta(days=1)).isoformat() if kind == "direct" else now.isoformat()
    state["pending_expression"] = {
        "id": f"expr-{kind}",
        "text": "仍应等待显示。",
        "created_at": created_at,
        "kind": kind,
    }
    files.commit(state, history, memories)

    response = client.post("/api/body/step", json={}).json()

    assert response["expression"]["id"] == f"expr-{kind}"
    assert response["time_status"] == "waiting_for_shown"
    assert provider.calls == 0


def test_request_rejects_missing_event_id_and_extra_protocol_fields(api) -> None:
    client, _, _ = api
    missing = client.post("/api/body/step", json={"event": {"type": "chat", "content": "在吗"}})
    extra = client.post("/api/body/step", json={"events": []})

    assert missing.status_code == 422
    assert extra.status_code == 422


@pytest.mark.parametrize(
    ("event_type", "zone", "expression"),
    [
        ("touch_head", "head", "呀，碰到我头发了。"),
        ("touch_body", "body", "唔，碰到我衣角了。"),
    ],
)
def test_touch_is_raw_fact_for_mind_without_relationship_score(
    api, event_type: str, zone: str, expression: str
) -> None:
    client, provider, data_dir = api
    event = {"event_id": "touch-001", "type": event_type}

    first = client.post("/api/body/step", json={"event": event})
    assert first.status_code == 200
    body = first.json()
    assert body["event_status"] == "processed"
    assert body["expression"]["text"] == expression

    history = _history(data_dir)
    assert history[0]["type"] == "body_touch"
    assert history[0]["zone"] == zone
    assert "content" not in history[0]
    state = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
    serialized = json.dumps(state, ensure_ascii=False).lower()
    assert all(
        field not in serialized
        for field in ("warmth", "relationship_score", "好感度", "亲密度", "关系分")
    )

    repeated = client.post("/api/body/step", json={"event": event}).json()
    assert repeated["event_status"] == "duplicate"
    assert provider.calls == 1


def test_touch_rejects_body_authored_meaning(api) -> None:
    client, _, _ = api
    response = client.post(
        "/api/body/step",
        json={
            "event": {
                "event_id": "touch-with-meaning",
                "type": "touch_body",
                "content": "用户是在表达喜欢",
            }
        },
    )

    assert response.status_code == 422


def test_due_step_waits_for_completed_read_receipt_before_progress(api) -> None:
    client, provider, data_dir = api
    files = MindFiles(data_dir)
    state, history, memories = files.load(datetime(2020, 1, 1, tzinfo=UTC))
    state["last_step_at"] = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    files.commit(state, history, memories)

    first = client.post("/api/body/step", json={}).json()
    activity = first["activity"]

    assert first["event_status"] == "none"
    assert first["time_status"] == "scheduled"
    assert activity["type"] == "read"
    assert first["expression"] is None
    assert _history(data_dir) == []
    assert provider.calls == 0

    second = client.post("/api/body/step", json={}).json()
    assert second["time_status"] == "waiting_for_activity"
    assert second["activity"] == activity
    assert _history(data_dir) == []
    assert provider.calls == 0

    receipt = {"activity_id": activity["id"], "status": "completed"}
    completed = client.post("/api/body/step", json={"activity_receipt": receipt}).json()
    recorded = _history(data_dir)
    state = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
    assert completed["activity_confirmed"] is True
    assert completed["activity"] is None
    assert [item["type"] for item in recorded] == ["self_reading", "memory_operation"]
    assert recorded[0]["content"].startswith("少无适俗韵")
    assert state["reading"]["next_passage"] == 1
    assert provider.calls == 1

    duplicate = client.post("/api/body/step", json={"activity_receipt": receipt}).json()
    assert duplicate["activity_confirmed"] is True
    assert provider.calls == 1


def test_completed_read_can_offer_caring_ambient_until_body_reports_shown(api) -> None:
    client, provider, data_dir = api
    files = MindFiles(data_dir)
    now = datetime.now(UTC).astimezone()
    state, history, memories = files.load(now)
    state["last_step_at"] = (now - timedelta(minutes=31)).isoformat()
    files.commit(state, history, memories)
    presence = {"present": True, "fullscreen": False}

    first = client.post("/api/body/step", json={"presence": presence}).json()
    activity = first["activity"]

    assert first["time_status"] == "scheduled"
    assert first["expression"] is None
    assert _history(data_dir) == []
    assert provider.calls == 0

    completed = client.post(
        "/api/body/step",
        json={
            "activity_receipt": {"activity_id": activity["id"], "status": "completed"},
            "presence": presence,
        },
    ).json()
    expression = completed["expression"]
    assert completed["activity_confirmed"] is True
    assert expression["kind"] == "ambient"
    assert expression["text"] == "刚读到一句很想回到自在处的话。你今天还好吗？"
    assert [item["type"] for item in _history(data_dir)] == [
        "self_reading",
        "memory_operation",
    ]

    repeated = client.post("/api/body/step", json={"presence": presence}).json()
    assert repeated["time_status"] == "waiting_for_shown"
    assert repeated["expression"] == expression
    assert [item["type"] for item in _history(data_dir)] == [
        "self_reading",
        "memory_operation",
    ]
    assert provider.calls == 1

    shown = client.post(
        "/api/body/step",
        json={"shown_id": expression["id"], "presence": presence},
    ).json()
    assert shown["shown_confirmed"] is True
    assert shown["expression"] is None
    recorded = _history(data_dir)
    assert [item["type"] for item in recorded] == [
        "self_reading",
        "memory_operation",
        "shared_expression",
    ]
    assert recorded[-1]["expression_kind"] == "ambient"


@pytest.mark.parametrize(
    "presence",
    [
        None,
        {"present": False, "fullscreen": False},
        {"present": True, "fullscreen": True},
    ],
)
def test_absent_or_fullscreen_completed_read_stays_silent(api, presence) -> None:  # noqa: ANN001
    client, provider, data_dir = api
    files = MindFiles(data_dir)
    now = datetime.now(UTC).astimezone()
    state, history, memories = files.load(now)
    state["last_step_at"] = (now - timedelta(minutes=31)).isoformat()
    files.commit(state, history, memories)

    payload = {} if presence is None else {"presence": presence}
    scheduled = client.post("/api/body/step", json=payload).json()
    receipt_payload = {
        "activity_receipt": {
            "activity_id": scheduled["activity"]["id"],
            "status": "completed",
        }
    }
    if presence is not None:
        receipt_payload["presence"] = presence
    response = client.post("/api/body/step", json=receipt_payload).json()

    assert scheduled["time_status"] == "scheduled"
    assert response["activity_confirmed"] is True
    assert response["expression"] is None
    assert [item["type"] for item in _history(data_dir)] == [
        "self_reading",
        "memory_operation",
    ]


def test_unanswered_caring_question_creates_zero_debt_or_second_ambient(api) -> None:
    client, provider, data_dir = api
    files = MindFiles(data_dir)
    now = datetime.now(UTC).astimezone()
    state, history, memories = files.load(now)
    state["last_step_at"] = (now - timedelta(minutes=31)).isoformat()
    files.commit(state, history, memories)
    presence = {"present": True, "fullscreen": False}
    scheduled = client.post("/api/body/step", json={"presence": presence}).json()
    first = client.post(
        "/api/body/step",
        json={
            "activity_receipt": {
                "activity_id": scheduled["activity"]["id"],
                "status": "completed",
            },
            "presence": presence,
        },
    ).json()
    client.post(
        "/api/body/step",
        json={"shown_id": first["expression"]["id"], "presence": presence},
    )

    state, history, memories = files.load(now)
    state["last_step_at"] = (now - timedelta(minutes=31)).isoformat()
    files.commit(state, history, memories)
    scheduled_later = client.post("/api/body/step", json={"presence": presence}).json()
    later = client.post(
        "/api/body/step",
        json={
            "activity_receipt": {
                "activity_id": scheduled_later["activity"]["id"],
                "status": "completed",
            },
            "presence": presence,
        },
    ).json()
    recorded = _history(data_dir)

    assert later["activity_confirmed"] is True
    assert later["expression"] is None
    assert sum(item.get("expression_kind") == "ambient" for item in recorded) == 1
    assert all(item["type"] != "user_experience" for item in recorded)
    assert "没回" not in json.dumps(recorded, ensure_ascii=False)
    assert "不理我" not in json.dumps(recorded, ensure_ascii=False)
    assert provider.calls == 2


def test_presence_rejects_extra_body_authored_meaning(api) -> None:
    client, _, _ = api
    response = client.post(
        "/api/body/step",
        json={
            "presence": {
                "present": True,
                "fullscreen": False,
                "meaning": "用户想听我说话",
            }
        },
    )

    assert response.status_code == 422


def test_s14_read_ambient_chat_touch_full_vertical_trace(api) -> None:
    client, _, data_dir = api
    files = MindFiles(data_dir)
    now = datetime.now(UTC).astimezone()
    state, history, memories = files.load(now)
    state["last_step_at"] = (now - timedelta(minutes=31)).isoformat()
    files.commit(state, history, memories)
    presence = {"present": True, "fullscreen": False}

    scheduled = client.post("/api/body/step", json={"presence": presence}).json()
    ambient = client.post(
        "/api/body/step",
        json={
            "activity_receipt": {
                "activity_id": scheduled["activity"]["id"],
                "status": "completed",
            },
            "presence": presence,
        },
    ).json()
    ambient_shown = client.post(
        "/api/body/step",
        json={"shown_id": ambient["expression"]["id"], "presence": presence},
    ).json()
    chat = client.post(
        "/api/body/step",
        json={
            "presence": presence,
            "event": {
                "event_id": "s8-chat",
                "type": "chat",
                "content": "今天终于忙完了。",
            },
        },
    ).json()
    chat_shown = client.post(
        "/api/body/step",
        json={"shown_id": chat["expression"]["id"], "presence": presence},
    ).json()
    touch = client.post(
        "/api/body/step",
        json={
            "presence": presence,
            "event": {"event_id": "s8-touch", "type": "touch_head"},
        },
    ).json()
    touch_shown = client.post(
        "/api/body/step",
        json={"shown_id": touch["expression"]["id"], "presence": presence},
    ).json()

    recorded = _history(data_dir)
    shown_words = [item["content"] for item in recorded if item["type"] == "shared_expression"]
    final_state = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
    trace = {
        "statuses": [
            scheduled["time_status"],
            ambient["activity_confirmed"],
            ambient_shown["shown_confirmed"],
            chat["event_status"],
            chat_shown["shown_confirmed"],
            touch["event_status"],
            touch_shown["shown_confirmed"],
        ],
        "shown_words": shown_words,
        "history_types": [item["type"] for item in recorded],
        "files": sorted(path.name for path in data_dir.iterdir()),
    }
    print("S14_TRACE=" + json.dumps(trace, ensure_ascii=False))

    assert trace["statuses"] == ["scheduled", True, True, "processed", True, "processed", True]
    assert shown_words == [
        "刚读到一句很想回到自在处的话。你今天还好吗？",
        "忙完就好。先在我这儿松口气。",
        "呀，碰到我头发了。",
    ]
    assert trace["files"] == [
        "failures.jsonl",
        "history.jsonl",
        "memories.json",
        "state.json",
    ]
    assert final_state["pending_expression"] is None
