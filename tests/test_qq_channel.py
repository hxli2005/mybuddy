from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mybuddy.channels.qq import (
    QQBotAdapter,
    QQBotRunner,
    QQInboundMessage,
    _extract_event_id,
    _make_botpy_intents,
)
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolSpec
from mybuddy.services import ChatService
from mybuddy.storage import bind_external_account, create_user, init_db


class EchoProvider(BaseLLMProvider):
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
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return LLMResponse(text=f"reply:{user}", finish_reason="stop")


def _write_config(path: Path, tmp_path: Path) -> None:
    path.write_text(
        f"""
llm:
  provider: anthropic
  model: test
  api_key: test
memory:
  extract_after_turns: 99
paths:
  data_dir: "{tmp_path / 'data'}"
  db_file: "{tmp_path / 'data' / 'master.db'}"
  chroma_dir: "{tmp_path / 'data' / 'memory'}"
  skills_dir: "{tmp_path / 'data' / 'skills'}"
  trajectories_dir: "{tmp_path / 'data' / 'trajectories'}"
scheduler:
  enabled: false
tools:
  weather_mock: true
channels:
  qq:
    enabled: true
    app_id: app
    app_secret: secret
    allow_auto_create_user: false
""",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_qq_adapter_routes_bound_user_and_dedupes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    service = ChatService(
        config_path=str(config_path),
        provider=EchoProvider(),
        enable_emotion=False,
    )
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="tester")
    bind_external_account(engine, user_id=user.id, provider="qq", external_id="qq-user")
    adapter = QQBotAdapter(
        chat_service=service,
        allow_auto_create_user=False,
        daily_message_limit=30,
    )
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    msg = QQInboundMessage(event_id="evt-1", external_user_id="qq-user", content="你好")
    first = await adapter.handle_message(msg, reply)
    second = await adapter.handle_message(msg, reply)

    assert first == "reply:你好"
    assert second is None
    assert replies == ["reply:你好"]


@pytest.mark.asyncio
async def test_qq_adapter_rejects_unbound_user_when_auto_create_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    service = ChatService(
        config_path=str(config_path),
        provider=EchoProvider(),
        enable_emotion=False,
    )
    service.startup()
    adapter = QQBotAdapter(
        chat_service=service,
        allow_auto_create_user=False,
        daily_message_limit=30,
    )
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    msg = QQInboundMessage(event_id="evt-1", external_user_id="unknown", content="你好")
    result = await adapter.handle_message(msg, reply)

    assert "测试名单" in result
    assert replies == [result]


@pytest.mark.asyncio
async def test_qq_reprocesses_after_user_is_whitelisted(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    service = ChatService(config_path=str(config_path), provider=EchoProvider(), enable_emotion=False)
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    adapter = QQBotAdapter(chat_service=service, allow_auto_create_user=False, daily_message_limit=30)
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    msg = QQInboundMessage(event_id="evt-late", external_user_id="qq-late", content="你好")
    rejected = await adapter.handle_message(msg, reply)
    assert "测试名单" in rejected

    # 管理员把用户加入名单后,平台重投同一 event_id 不应再被永久去重丢弃。
    user = create_user(engine, display_name="late")
    bind_external_account(engine, user_id=user.id, provider="qq", external_id="qq-late")
    processed = await adapter.handle_message(msg, reply)
    assert processed == "reply:你好"
    assert replies[-1] == "reply:你好"


@pytest.mark.asyncio
async def test_qq_reply_failure_keeps_event_retryable(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    service = ChatService(config_path=str(config_path), provider=EchoProvider(), enable_emotion=False)
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="flaky")
    bind_external_account(engine, user_id=user.id, provider="qq", external_id="qq-flaky")
    adapter = QQBotAdapter(chat_service=service, allow_auto_create_user=False, daily_message_limit=30)

    attempts = {"n": 0}

    async def flaky_reply(text: str) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("network down")

    msg = QQInboundMessage(event_id="evt-flaky", external_user_id="qq-flaky", content="你好")
    await adapter.handle_message(msg, flaky_reply)

    # 回复失败后该事件应仍可重投处理,而不是卡在 processing 被永久丢弃。
    ok: list[str] = []

    async def ok_reply(text: str) -> None:
        ok.append(text)

    second = await adapter.handle_message(msg, ok_reply)
    assert second == "reply:你好"
    assert ok == ["reply:你好"]


def test_extract_event_id_is_deterministic_without_native_id() -> None:
    m1 = SimpleNamespace(openid="u1", content="hello world")
    m2 = SimpleNamespace(openid="u1", content="hello world")
    m3 = SimpleNamespace(openid="u1", content="different")
    # 无原生 id 时回退的 event_id 必须只取决于内容,跨进程/重启稳定。
    assert _extract_event_id(m1) == _extract_event_id(m2)
    assert _extract_event_id(m1) != _extract_event_id(m3)


def test_qq_intents_include_public_messages() -> None:
    captured: dict[str, bool] = {}

    class FakeIntents:
        def __init__(self, **kwargs: bool) -> None:
            captured.update(kwargs)
            self.value = 1

    intents = _make_botpy_intents(SimpleNamespace(Intents=FakeIntents))

    assert intents.value == 1
    assert captured["public_messages"] is True
    assert captured["public_guild_messages"] is True
    assert captured["direct_message"] is True


def test_qq_runner_passes_sandbox_to_botpy_client(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    created: dict[str, object] = {}

    class FakeIntents:
        def __init__(self, **kwargs: bool) -> None:
            self.kwargs = kwargs
            self.value = 1

    class FakeClient:
        def __init__(self, *, intents, is_sandbox=False):  # noqa: ANN001
            created["intents"] = intents
            created["is_sandbox"] = is_sandbox

    monkeypatch.setitem(
        __import__("sys").modules,
        "botpy",
        SimpleNamespace(Client=FakeClient, Intents=FakeIntents),
    )
    service = ChatService(
        config_path=str(config_path),
        provider=EchoProvider(),
        enable_emotion=False,
    )
    runner = QQBotRunner(config_path=str(config_path), chat_service=service)
    runner._make_botpy_client()

    assert created["is_sandbox"] is True
    assert created["intents"].kwargs["public_messages"] is True
