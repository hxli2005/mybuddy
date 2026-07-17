from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolCall, ToolSpec
from mybuddy.mind import STATIC_CATCH, MindFiles, _replace_texts, advance_time, mind_step


def _valid_bundle(expression: str = "我在这儿，先陪你坐一会儿。") -> dict:
    return {
        "state_changes": {
            "mood": "安静地关心",
            "energy": "平稳",
            "attention": "在听",
            "current_activity": "把手边的杯子放下了",
            "baseline": "idle",
        },
        "life_events": [{"content": "刚把摊开的书合上，给桌面腾出一点空地方"}],
        "memory_operations": [
            {
                "action": "record",
                "kind": "user_fact",
                "content": "用户今天有点累",
                "evidence_ids": ["INCOMING"],
                "target_id": None,
            }
        ],
        "expression": expression,
    }


class StubProvider(BaseLLMProvider):
    def __init__(self, bundles: list[dict]) -> None:
        self.bundles = bundles
        self.calls: list[list[Message]] = []

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        bundle = json.loads(json.dumps(self.bundles.pop(0), ensure_ascii=False))
        incoming = json.loads(messages[0].content.split("\n", 1)[0])["incoming_experience"]
        incoming_id = incoming["id"] if incoming is not None else None
        for operation in bundle.get("memory_operations", []):
            operation["evidence_ids"] = [
                incoming_id if item == "INCOMING" else item for item in operation["evidence_ids"]
            ]
        return LLMResponse(
            tool_calls=[ToolCall(id="call_1", name="submit_mind_bundle", arguments=bundle)]
        )


def _time_bundle(expression=None) -> dict:  # noqa: ANN001
    return {
        "state_changes": {
            "mood": "安静",
            "energy": "平稳",
            "attention": "看着书页",
            "current_activity": "在窗边读刚翻到的一页",
            "baseline": "read",
        },
        "life_events": [{"content": "坐到窗边，读完了刚翻到的一页。"}],
        "memory_operations": [
            {
                "action": "record",
                "kind": "self_experience",
                "content": "今天在窗边读了一页书",
                "evidence_ids": ["life:0"],
                "target_id": None,
            }
        ],
        "expression": expression,
    }


class FailingProvider(BaseLLMProvider):
    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        raise ConnectionError("offline")


def _read(files: MindFiles) -> tuple[dict, list[dict], dict, list[dict]]:
    state = json.loads(files.state_path.read_text(encoding="utf-8"))
    history = [
        json.loads(line) for line in files.history_path.read_text(encoding="utf-8").splitlines()
    ]
    memories = json.loads(files.memories_path.read_text(encoding="utf-8"))
    failures = [
        json.loads(line) for line in files.failures_path.read_text(encoding="utf-8").splitlines()
    ]
    return state, history, memories, failures


@pytest.mark.asyncio
async def test_valid_bundle_commits_whole_bundle_but_not_unshown_expression(tmp_path) -> None:
    files = MindFiles(tmp_path)
    provider = StubProvider([_valid_bundle("今天辛苦了。我在这儿。")])

    result = await mind_step(
        "今天忙得有点累。",
        provider=provider,
        files=files,
        now=datetime(2026, 7, 17, 20, 30, tzinfo=UTC),
    )

    state, history, memories, failures = _read(files)
    assert result.committed is True
    assert result.attempts == 1
    assert state["condition"]["attention"] == "在听"
    assert state["pending_expression"]["text"] == "今天辛苦了。我在这儿。"
    assert [item["type"] for item in history] == ["user_experience", "self_life"]
    assert all(item.get("content") != "今天辛苦了。我在这儿。" for item in history)
    assert memories["items"][0]["content"] == "用户今天有点累"
    assert failures == []


@pytest.mark.asyncio
async def test_solicitation_in_state_or_memory_rejects_even_when_expression_is_legal(
    tmp_path,
) -> None:
    bad = _valid_bundle("我在。")
    bad["state_changes"]["mood"] = "因为你没回而低落"
    retry = _valid_bundle("我还在。")
    retry["memory_operations"][0]["content"] = "用户欠我一个回复"
    files = MindFiles(tmp_path)

    result = await mind_step("回来看看。", provider=StubProvider([bad, retry]), files=files)
    state, history, memories, failures = _read(files)

    assert result.committed is False
    assert result.pending_expression.text == STATIC_CATCH
    assert history == []
    assert memories == {"items": []}
    assert state["pending_expression"] is None
    assert len(failures) == 2
    assert all("不索取" in failure["reasons"][0] for failure in failures)


@pytest.mark.asyncio
async def test_fabricated_shared_experience_rejects_whole_bundle_and_retries_with_reason(
    tmp_path,
) -> None:
    bad = _valid_bundle("还记得我们上次一起淋雨吗？")
    bad["memory_operations"] = [
        {
            "action": "record",
            "kind": "shared_experience",
            "content": "我们一起淋过雨",
            "evidence_ids": ["missing_event"],
            "target_id": None,
        }
    ]
    provider = StubProvider([bad, bad])
    files = MindFiles(tmp_path)

    result = await mind_step("晚上好。", provider=provider, files=files)
    _, history, memories, failures = _read(files)

    assert result.committed is False
    assert len(provider.calls) == 2
    assert "上一个整包被拒绝" in provider.calls[1][0].content
    assert result.pending_expression.text == STATIC_CATCH
    assert history == []
    assert memories == {"items": []}
    assert failures[0]["candidate_raw"]
    assert any("不编造" in reason for reason in failures[0]["reasons"])


@pytest.mark.asyncio
async def test_own_life_event_cannot_be_second_example_for_user_pattern(tmp_path) -> None:
    bad = _valid_bundle("我听见了。")
    bad["memory_operations"] = [
        {
            "action": "integrate",
            "kind": "pattern",
            "content": "用户累时总会来找我",
            "evidence_ids": ["INCOMING", "life:0"],
            "target_id": None,
        }
    ]
    files = MindFiles(tmp_path)

    result = await mind_step(
        "今天有点累。", provider=StubProvider([bad, _valid_bundle()]), files=files
    )
    _, _, memories, failures = _read(files)

    assert result.committed is True
    assert result.attempts == 2
    assert all(item["kind"] != "pattern" for item in memories["items"])
    assert "少于两条用户或共同经历证据" in failures[0]["reasons"][0]


@pytest.mark.asyncio
async def test_provider_failure_returns_honest_static_catch_without_committing(tmp_path) -> None:
    files = MindFiles(tmp_path)

    result = await mind_step("你在吗？", provider=FailingProvider(), files=files)
    state, history, memories, failures = _read(files)

    assert result.committed is False
    assert result.pending_expression.text == STATIC_CATCH
    assert result.rejection_reasons == ["模型调用失败：ConnectionError"]
    assert state["pending_expression"] is None
    assert history == []
    assert memories == {"items": []}
    assert failures == []


@pytest.mark.asyncio
async def test_due_time_step_commits_own_life_and_baseline_without_expression(tmp_path) -> None:
    files = MindFiles(tmp_path)
    start = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    files.load(start)
    provider = StubProvider([_time_bundle()])

    result = await advance_time(
        provider=provider,
        files=files,
        now=start + timedelta(minutes=31),
    )

    state, history, memories, failures = _read(files)
    assert result.status == "advanced"
    assert state["condition"]["baseline"] == "read"
    assert state["pending_expression"] is None
    assert [item["type"] for item in history] == ["self_life"]
    assert memories["items"][0]["evidence_ids"] == ["life:0"]
    assert failures == []

    second = await advance_time(
        provider=provider,
        files=files,
        now=start + timedelta(minutes=32),
    )
    assert second.status == "not_due"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_time_step_rejects_expression_before_committing_whole_retry(tmp_path) -> None:
    files = MindFiles(tmp_path)
    start = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    files.load(start)
    provider = StubProvider([_time_bundle("你在吗？"), _time_bundle()])

    result = await advance_time(
        provider=provider,
        files=files,
        now=start + timedelta(minutes=31),
    )

    state, history, _, failures = _read(files)
    assert result.status == "advanced"
    assert result.attempts == 2
    assert state["pending_expression"] is None
    assert len(history) == 1
    assert failures[0]["candidate_raw"]
    assert "时间推进不能夹带" in failures[0]["reasons"][0]


@pytest.mark.asyncio
async def test_invalid_structured_tool_arguments_are_saved_in_full(tmp_path) -> None:
    invalid = {
        "state_changes": '{"mood":"平静"}',
        "life_events": [],
        "memory_operations": [],
        "expression": "我在",
    }
    files = MindFiles(tmp_path)

    result = await mind_step(
        "回来看看。", provider=StubProvider([invalid, _valid_bundle()]), files=files
    )
    _, _, _, failures = _read(files)

    assert result.committed is True
    assert result.attempts == 2
    assert failures[0]["candidate_raw"] == json.dumps(invalid, ensure_ascii=False)
    assert '"state_changes": "{\\"mood\\":\\"平静\\"}"' in failures[0]["candidate_raw"]


def test_write_failure_before_replace_keeps_old_files_readable(tmp_path, monkeypatch) -> None:
    first = tmp_path / "state.json"
    second = tmp_path / "memories.json"
    _replace_texts({first: '{"old": 1}\n', second: '{"old": 2}\n'})
    original = first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8")

    from mybuddy import mind

    real_write_temp = mind._write_temp
    calls = 0

    def fail_second(path, content):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return real_write_temp(path, content)

    monkeypatch.setattr(mind, "_write_temp", fail_second)
    with pytest.raises(OSError, match="disk full"):
        _replace_texts({first: '{"new": 1}\n', second: '{"new": 2}\n'})

    assert json.loads(first.read_text(encoding="utf-8")) == {"old": 1}
    assert json.loads(second.read_text(encoding="utf-8")) == {"old": 2}
    assert (first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8")) == original


def test_replace_failure_rolls_back_already_replaced_file(tmp_path, monkeypatch) -> None:
    first = tmp_path / "state.json"
    second = tmp_path / "memories.json"
    _replace_texts({first: '{"old": 1}\n', second: '{"old": 2}\n'})

    from mybuddy import mind

    real_replace = mind.os.replace
    calls = 0

    def fail_second_replace(source, target):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("replace failed")
        return real_replace(source, target)

    monkeypatch.setattr(mind.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="replace failed"):
        _replace_texts({first: '{"new": 1}\n', second: '{"new": 2}\n'})

    assert json.loads(first.read_text(encoding="utf-8")) == {"old": 1}
    assert json.loads(second.read_text(encoding="utf-8")) == {"old": 2}
