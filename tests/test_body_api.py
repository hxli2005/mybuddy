import json
from datetime import UTC, datetime

import pytest

from mybuddy.body_api import create_body_app
from mybuddy.llm import BaseLLMProvider, LLMResponse, ToolCall
from mybuddy.mind import MindFiles


class StubProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
        self.calls += 1
        incoming = json.loads(messages[0].content)["incoming_experience"]
        is_time_step = incoming is None
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="submit_mind_bundle",
                    arguments={
                        "state_changes": {
                            "mood": "放松",
                            "energy": "平稳",
                            "attention": "听你说话",
                            "current_activity": "把刚翻开的书合上了",
                            "baseline": "read" if is_time_step else "idle",
                        },
                        "life_events": [{"content": "刚才读完了窗边那一页。"}],
                        "memory_operations": [
                            {
                                "action": "record",
                                "kind": "self_experience" if is_time_step else "user_fact",
                                "content": "刚才读完了窗边那一页"
                                if is_time_step
                                else "用户今天终于忙完了",
                                "evidence_ids": ["life:0"] if is_time_step else [incoming["id"]],
                                "target_id": None,
                            }
                        ],
                        "expression": None if is_time_step else "忙完就好。先在我这儿松口气。",
                    },
                )
            ]
        )


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
        "baseline": first_body["baseline"],
        "expression": None,
        "shown_confirmed": True,
        "event_status": "duplicate",
        "time_status": "not_due",
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
        "self_life",
        "shared_expression",
        "user_experience",
        "self_life",
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


def test_request_rejects_missing_event_id_and_extra_protocol_fields(api) -> None:
    client, _, _ = api
    missing = client.post("/api/body/step", json={"event": {"type": "chat", "content": "在吗"}})
    extra = client.post("/api/body/step", json={"events": []})

    assert missing.status_code == 422
    assert extra.status_code == 422


def test_empty_step_advances_due_life_into_continuous_body_baseline(api) -> None:
    client, provider, data_dir = api
    files = MindFiles(data_dir)
    state, history, memories = files.load(datetime(2020, 1, 1, tzinfo=UTC))
    state["last_step_at"] = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    files.commit(state, history, memories)

    first = client.post("/api/body/step", json={}).json()
    recorded = _history(data_dir)

    assert first["event_status"] == "none"
    assert first["time_status"] == "advanced"
    assert first["baseline"]["baseline"] == "read"
    assert first["expression"] is None
    assert [item["type"] for item in recorded] == ["self_life"]
    assert provider.calls == 1

    second = client.post("/api/body/step", json={}).json()
    assert second["time_status"] == "not_due"
    assert second["baseline"] == first["baseline"]
    assert _history(data_dir) == recorded
    assert provider.calls == 1
