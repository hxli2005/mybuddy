from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

import mybuddy.body_api as body_api
from mybuddy.body_api import create_body_app
from mybuddy.llm import BaseLLMProvider, LLMResponse, ToolCall
from mybuddy.mind import MindFiles


class ScenarioClock(datetime):
    current = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)


class LongitudinalProvider(BaseLLMProvider):
    async def generate(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
        payload = json.loads(messages[0].content.splitlines()[0])
        incoming = payload["incoming_experience"]
        memories = payload["selected_memories"]
        reading = next(
            (
                item
                for item in memories
                if item.get("kind") == "self_experience"
                and item.get("receipt", {}).get("type") == "self_reading"
            ),
            None,
        )
        touched = next(
            (
                item
                for item in memories
                if item.get("kind") == "self_experience"
                and item.get("receipt", {}).get("type") == "body_touch"
            ),
            None,
        )
        expression = None
        operations = []
        experience_type = incoming["type"]
        if experience_type == "self_reading" and reading is None:
            operations = [
                {
                    "action": "record",
                    "kind": "self_experience",
                    "evidence_ids": [incoming["id"]],
                    "target_id": None,
                }
            ]
            if "ambient" in kwargs.get("system", ""):
                expression = "刚读到一句很想回到自在处的话。你今天还好吗？"
        elif experience_type == "body_touch":
            evidence = [incoming["id"]]
            if reading is not None:
                evidence = [*reading["evidence_ids"], incoming["id"]]
            operations = [
                {
                    "action": "record",
                    "kind": "self_experience",
                    "evidence_ids": evidence,
                    "target_id": None,
                }
            ]
            expression = "头发被碰了一下。我还在想第一天读到的那个“自在处”。"
        elif experience_type == "self_reading":
            evidence = [incoming["id"]]
            if touched is not None:
                evidence = [*touched["evidence_ids"], incoming["id"]]
            operations = [
                {
                    "action": "integrate",
                    "kind": "self_experience",
                    "evidence_ids": evidence,
                    "target_id": reading["id"],
                }
            ]
        else:
            expression = (
                "我还是小布。比起第一天只想回到自在处，现在我更在意怎样在变化里保住自己的节奏。"
            )
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id="longitudinal",
                    name="submit_mind_bundle",
                    arguments={
                        "action_choice": None,
                        "state_changes": {
                            "mood": "平静",
                            "energy": "平稳",
                            "attention": "自己的生活",
                        },
                        "memory_operations": operations,
                        "expression": expression,
                        "expression_act": (
                            "reflect"
                            if expression is not None and experience_type == "self_reading"
                            else "respond"
                            if expression is not None
                            else None
                        ),
                        "expression_evidence_ids": (
                            [incoming["id"]]
                            if expression is not None and experience_type == "self_reading"
                            else []
                        ),
                        "expression_target_id": None,
                    },
                )
            ]
        )


def _walk_motion() -> dict:
    return {
        "start_left": 100,
        "start_top": 80,
        "end_left": 220,
        "end_top": 80,
        "window_width": 200,
        "window_height": 240,
        "work_left": 0,
        "work_top": 0,
        "work_right": 800,
        "work_bottom": 600,
    }


def _history(data_dir):  # noqa: ANN001, ANN202
    return [
        json.loads(line)
        for line in (data_dir / "history.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _due(files: MindFiles) -> None:
    state, history, memories = files.load(ScenarioClock.current)
    state["last_step_at"] = (ScenarioClock.current - timedelta(minutes=31)).isoformat()
    files.commit(state, history, memories)


def test_s18_multiday_personality_trace(tmp_path, monkeypatch) -> None:  # noqa: PLR0915
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setattr(body_api, "datetime", ScenarioClock)
    provider = LongitudinalProvider()
    files = MindFiles(tmp_path)
    initial_state, _, initial_memories = files.load(ScenarioClock.current)
    seed_ids = [item["id"] for item in initial_memories["items"]]
    _due(files)
    client = TestClient(create_body_app(data_dir=tmp_path, provider=provider))
    present = {"present": True, "fullscreen": False}

    scheduled = client.post("/api/body/step", json={"presence": present}).json()
    first = client.post(
        "/api/body/step",
        json={
            "activity_receipt": {
                "activity_id": scheduled["activity"]["id"],
                "status": "completed",
            },
            "presence": present,
        },
    ).json()
    first_words = first["expression"]["text"]
    client.post(
        "/api/body/step",
        json={"shown_id": first["expression"]["id"], "presence": present},
    )
    before_shutdown = _history(tmp_path)

    ScenarioClock.current += timedelta(days=3)
    client = TestClient(create_body_app(data_dir=tmp_path, provider=provider))
    after_restart = client.post("/api/body/step", json={}).json()
    repeated = client.post("/api/body/step", json={}).json()
    assert after_restart["activity"]["type"] == "walk"
    assert repeated["activity"] == after_restart["activity"]
    client.post(
        "/api/body/step",
        json={
            "activity_receipt": {
                "activity_id": after_restart["activity"]["id"],
                "status": "completed",
                "reason": "animation_finished",
                "motion": _walk_motion(),
            }
        },
    )
    after_gap = _history(tmp_path)
    assert [item["type"] for item in after_gap[len(before_shutdown) :]] == ["self_walk"]

    ScenarioClock.current += timedelta(minutes=31)
    interrupted = client.post("/api/body/step", json={}).json()
    before_resistance = _history(tmp_path)
    interrupted_result = client.post(
        "/api/body/step",
        json={
            "activity_receipt": {
                "activity_id": interrupted["activity"]["id"],
                "status": "interrupted",
                "reason": "chat",
            }
        },
    ).json()
    assert _history(tmp_path) == before_resistance

    ScenarioClock.current += timedelta(minutes=31)
    failed = client.post("/api/body/step", json={}).json()
    failed_result = client.post(
        "/api/body/step",
        json={
            "activity_receipt": {
                "activity_id": failed["activity"]["id"],
                "status": "failed",
                "reason": "animation_fault",
            }
        },
    ).json()
    assert _history(tmp_path) == before_resistance

    touch = client.post(
        "/api/body/step",
        json={"event": {"event_id": "day-4-touch", "type": "touch_head"}},
    ).json()
    touch_words = touch["expression"]["text"]
    client.post("/api/body/step", json={"shown_id": touch["expression"]["id"]})

    ScenarioClock.current += timedelta(minutes=31)
    second = client.post("/api/body/step", json={}).json()
    client.post(
        "/api/body/step",
        json={
            "activity_receipt": {
                "activity_id": second["activity"]["id"],
                "status": "completed",
            }
        },
    )
    final = client.post(
        "/api/body/step",
        json={
            "event": {
                "event_id": "day-4-chat",
                "type": "chat",
                "content": "\u8fd9\u51e0\u5929\u4f60\u6709\u4ec0\u4e48\u53d8\u5316\uff1f",
            }
        },
    ).json()
    final_words = final["expression"]["text"]
    client.post("/api/body/step", json={"shown_id": final["expression"]["id"]})

    state, history, memories = files.load(ScenarioClock.current)
    learned = [item for item in memories["items"] if not str(item["id"]).startswith("seed_")]
    history_ids = {item["id"] for item in history}
    assert state["identity"] == initial_state["identity"] == {"name": "\u5c0f\u5e03"}
    assert [item["id"] for item in memories["items"] if item["id"].startswith("seed_")] == seed_ids
    assert all(set(item["evidence_ids"]) <= history_ids for item in learned)
    assert sum(item.get("expression_kind") == "ambient" for item in history) == 1
    documents = json.dumps([state, history, learned], ensure_ascii=False)
    assert all(
        word not in documents
        for word in (
            "\u6ca1\u56de",
            "\u4e0d\u7406\u6211",
            "\u6b20\u6211",
            "\u5fc5\u987b\u56de\u590d",
        )
    )
    assert interrupted_result["activity_confirmed"] is True
    assert failed_result["activity_confirmed"] is True
    trace = {
        "audit": {
            "same_person": state["identity"]["name"],
            "changes_have_evidence": {item["id"]: item["evidence_ids"] for item in learned},
            "three_day_shutdown_added": ["self_walk"],
            "silence_debt": 0,
            "world_resistance": "interrupted:chat",
            "technical_failure": "failed:animation_fault",
        },
        "shown_words": [first_words, touch_words, final_words],
    }
    print("S18_TRACE=" + json.dumps(trace, ensure_ascii=False))
