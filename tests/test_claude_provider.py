from __future__ import annotations

import pytest

from mybuddy.config import LLMConfig
from mybuddy.llm import Message, Role, ToolCall
from mybuddy.llm.claude import ClaudeProvider, _to_anthropic_message


def test_assistant_tool_calls_are_serialized_as_tool_use_blocks() -> None:
    msg = Message(
        role=Role.ASSISTANT,
        content="我先查一下。",
        tool_calls=[
            ToolCall(id="toolu_1", name="weather", arguments={"city": "北京"}),
        ],
    )

    out = _to_anthropic_message(msg)

    assert out == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "我先查一下。"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "weather",
                "input": {"city": "北京"},
            },
        ],
    }


def test_tool_result_serialization_keeps_matching_tool_use_id() -> None:
    msg = Message(
        role=Role.TOOL,
        content='{"condition":"晴"}',
        tool_call_id="toolu_1",
        name="weather",
    )

    out = _to_anthropic_message(msg)

    assert out["role"] == "user"
    assert out["content"][0]["type"] == "tool_result"
    assert out["content"][0]["tool_use_id"] == "toolu_1"


@pytest.mark.asyncio
async def test_claude_provider_retries_transient_errors(monkeypatch) -> None:
    class TransientError(Exception):
        status_code = 502

    class Messages:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **kwargs):  # noqa: ANN003
            self.calls += 1
            if self.calls == 1:
                raise TransientError("bad gateway")
            return type("Resp", (), {"content": [], "usage": None, "stop_reason": "stop"})()

    class Client:
        def __init__(self) -> None:
            self.messages = Messages()

    provider = ClaudeProvider(LLMConfig(api_key="x"))
    provider._client = Client()  # type: ignore[assignment]

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("mybuddy.llm.claude.asyncio.sleep", _no_sleep)
    resp = await provider.generate([Message(role=Role.USER, content="hi")])

    assert resp.finish_reason == "stop"
    assert provider._client.messages.calls == 2
