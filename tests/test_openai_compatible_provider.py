from __future__ import annotations

import pytest

from mybuddy.config import LLMConfig
from mybuddy.llm import Message, Role, ToolCall
from mybuddy.llm.openai_compatible import (
    OPENROUTER_BASE_URL,
    OpenAICompatibleProvider,
    _base_url_for,
    _from_openai_response,
    _to_openai_messages,
)


def test_openrouter_default_base_url() -> None:
    cfg = LLMConfig(provider="openrouter", api_key="x")

    assert _base_url_for(cfg) == OPENROUTER_BASE_URL


def test_openai_message_serialization_with_tools() -> None:
    messages = [
        Message(role=Role.USER, content="北京天气"),
        Message(
            role=Role.ASSISTANT,
            content="我查一下",
            tool_calls=[
                ToolCall(id="call_1", name="submit_bundle", arguments={"content": "候选"}),
            ],
        ),
        Message(role=Role.TOOL, content='{"condition":"晴"}', tool_call_id="call_1"),
    ]

    out = _to_openai_messages(messages, system="system prompt")

    assert out[0] == {"role": "system", "content": "system prompt"}
    assert out[2]["tool_calls"][0]["function"]["name"] == "submit_bundle"
    assert '"content": "候选"' in out[2]["tool_calls"][0]["function"]["arguments"]
    assert out[3]["role"] == "tool"
    assert out[3]["tool_call_id"] == "call_1"


def test_openai_response_parses_tool_calls() -> None:
    fn = type("Fn", (), {"name": "submit_bundle", "arguments": '{"content":"候选"}'})()
    tc = type("TC", (), {"id": "call_1", "function": fn})()
    msg = type("Msg", (), {"content": "", "tool_calls": [tc]})()
    choice = type("Choice", (), {"message": msg, "finish_reason": "tool_calls"})()
    resp = type("Resp", (), {"choices": [choice], "usage": None})()

    out = _from_openai_response(resp)

    assert out.tool_calls[0].name == "submit_bundle"
    assert out.tool_calls[0].arguments == {"content": "候选"}


@pytest.mark.asyncio
async def test_openai_compatible_provider_retries_transient_errors(monkeypatch) -> None:
    class TransientError(Exception):
        status_code = 502

    class Completions:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs):  # noqa: ANN003 —— 同步客户端,经 asyncio.to_thread 调用
            self.calls += 1
            if self.calls == 1:
                raise TransientError("bad gateway")
            msg = type("Msg", (), {"content": "ok", "tool_calls": []})()
            choice = type("Choice", (), {"message": msg, "finish_reason": "stop"})()
            return type("Resp", (), {"choices": [choice], "usage": None})()

    class Chat:
        def __init__(self) -> None:
            self.completions = Completions()

    class Client:
        def __init__(self) -> None:
            self.chat = Chat()

    provider = OpenAICompatibleProvider(LLMConfig(provider="openrouter", api_key="x"))
    provider._client = Client()  # type: ignore[assignment]

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("mybuddy.llm.openai_compatible.asyncio.sleep", _no_sleep)
    resp = await provider.generate([Message(role=Role.USER, content="hi")])

    assert resp.text == "ok"
    assert provider._client.chat.completions.calls == 2
