from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mybuddy.config import PersonaConfig
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolCall, ToolSpec
from mybuddy.services import ChannelCommandService, ChatService, RequestContext
from mybuddy.storage import (
    create_user,
    init_db,
    list_messages,
    set_user_persona,
    set_user_status,
    usage_count_today,
)


class EchoProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.system_prompts: list[str | None] = []

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
        self.system_prompts.append(system)
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return LLMResponse(text=f"reply:{user}", finish_reason="stop")


def _write_config(path: Path, tmp_path: Path, *, scheduler_enabled: bool = False) -> None:
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
  enabled: {str(scheduler_enabled).lower()}
tools:
  weather_mock: true
""",
        encoding="utf-8",
    )


class ReminderToolProvider(BaseLLMProvider):
    """第一轮调用 set_reminder,第二轮收敛出文本回复。"""

    def __init__(self, *, content: str, time: str) -> None:
        self._content = content
        self._time = time
        self._step = 0

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
        self._step += 1
        if self._step == 1:
            return LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="r1",
                        name="set_reminder",
                        arguments={"content": self._content, "time": self._time},
                    )
                ],
                finish_reason="tool_use",
            )
        return LLMResponse(text="好,到点我提醒你。", finish_reason="stop")


@pytest.mark.asyncio
async def test_chat_service_isolates_user_runtimes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    service = ChatService(
        config_path=str(config_path),
        provider=EchoProvider(),
        enable_emotion=False,
    )
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    u1 = create_user(engine, display_name="u1")
    u2 = create_user(engine, display_name="u2")

    r1 = await service.chat(RequestContext(user_id=u1.id, source="qq"), "你好")
    r2 = await service.chat(RequestContext(user_id=u2.id, source="qq"), "早")

    assert r1.text == "reply:你好"
    assert r2.text == "reply:早"
    u1_db = tmp_path / "data" / "users" / str(u1.id) / "mybuddy.db"
    u2_db = tmp_path / "data" / "users" / str(u2.id) / "mybuddy.db"
    assert [m["content"] for m in list_messages(init_db(str(u1_db)))] == ["你好", "reply:你好"]
    assert [m["content"] for m in list_messages(init_db(str(u2_db)))] == ["早", "reply:早"]


@pytest.mark.asyncio
async def test_chat_service_enforces_daily_quota(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    service = ChatService(
        config_path=str(config_path),
        provider=EchoProvider(),
        enable_emotion=False,
    )
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="u", daily_message_limit=1)
    ctx = RequestContext(user_id=user.id, source="qq")

    first = await service.chat(ctx, "one")
    second = await service.chat(ctx, "two")

    assert first.text == "reply:one"
    assert second.finish_reason == "quota_exceeded"
    assert "额度" in second.text


@pytest.mark.asyncio
async def test_chat_service_rebuilds_runtime_when_user_persona_changes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    provider = EchoProvider()
    service = ChatService(
        config_path=str(config_path),
        provider=provider,
        enable_emotion=False,
    )
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="u", daily_message_limit=3)
    ctx = RequestContext(user_id=user.id, source="qq")

    await service.chat(ctx, "one")
    first_runtime = service._runtimes[user.id]

    set_user_persona(
        engine,
        user_id=user.id,
        persona=PersonaConfig(name="专属小布", style="更直接"),
    )
    await service.chat(ctx, "two")

    assert service._runtimes[user.id] is not first_runtime
    assert provider.system_prompts[0] and "你是 小布" in provider.system_prompts[0]
    assert provider.system_prompts[-1] and "你是 专属小布" in provider.system_prompts[-1]


def test_channel_commands_reuse_chat_service(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    service = ChatService(
        config_path=str(config_path),
        provider=EchoProvider(),
        enable_emotion=False,
    )
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="u", daily_message_limit=3)
    commands = ChannelCommandService(service)

    quota = commands.handle(RequestContext(user_id=user.id, source="qq"), "/quota")
    help_text = commands.handle(RequestContext(user_id=user.id, source="qq"), "/help")

    assert quota.handled is True
    assert "0/3" in quota.text
    assert help_text.handled is True
    assert "/persona" in help_text.text
    assert "/privacy" in help_text.text


def test_channel_commands_blocked_for_disabled_user(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    service = ChatService(config_path=str(config_path), provider=EchoProvider(), enable_emotion=False)
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="u", daily_message_limit=3)
    set_user_status(engine, user.id, "disabled")
    commands = ChannelCommandService(service)
    ctx = RequestContext(user_id=user.id, source="qq")

    result = commands.handle(ctx, "/persona name 小鹿")

    # 被禁用的用户不能通过命令改人格,且不应实际写入任何 persona 覆盖。
    assert result.handled is True
    assert "未启用" in result.text
    assert service.persona_payload(ctx)["inherits_default"] is True


def test_feedback_before_any_turn_does_not_build_runtime(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)

    def boom_factory(_cfg: object) -> BaseLLMProvider:
        raise RuntimeError("provider should not be constructed")

    service = ChatService(
        config_path=str(config_path),
        provider_factory=boom_factory,
        enable_emotion=False,
    )
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="u")

    # 还没有任何对话轮次时,/good 应直接报"没有可反馈的对话轮次",
    # 而不是为了读一个 None 去构造 runtime(并暴露无关的 provider/api_key 错误)。
    with pytest.raises(RuntimeError) as excinfo:
        service.feedback(RequestContext(user_id=user.id, source="qq"), "good")
    assert "没有可反馈的对话轮次" in str(excinfo.value)


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_exceed_daily_quota(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)

    release = asyncio.Event()

    class GatedProvider(BaseLLMProvider):
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
            await release.wait()
            user = next((m.content for m in reversed(messages) if m.role == "user"), "")
            return LLMResponse(text=f"reply:{user}", finish_reason="stop")

    service = ChatService(config_path=str(config_path), provider=GatedProvider(), enable_emotion=False)
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="u", daily_message_limit=1)
    ctx = RequestContext(user_id=user.id, source="qq")

    # 两个并发请求同时通过额度检查:第一个进入临界区并持锁挂起,第二个在锁外读到旧计数。
    task_a = asyncio.create_task(service.chat(ctx, "one"))
    task_b = asyncio.create_task(service.chat(ctx, "two"))
    await asyncio.sleep(0.1)  # 让两个请求都到达挂起点
    release.set()
    results = await asyncio.gather(task_a, task_b)

    # 锁内权威复检应让恰好一个请求被额度拦截,最终用量不超过上限。
    reasons = sorted(r.finish_reason for r in results)
    assert reasons == ["quota_exceeded", "stop"]
    assert usage_count_today(engine, user_id=user.id, source="qq") == 1


def test_channel_persona_commands_update_user_persona(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path)
    service = ChatService(
        config_path=str(config_path),
        provider=EchoProvider(),
        enable_emotion=False,
    )
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="u", daily_message_limit=3)
    commands = ChannelCommandService(service)
    ctx = RequestContext(user_id=user.id, source="qq")

    show = commands.handle(ctx, "/persona")
    name = commands.handle(ctx, "/persona name 小鹿")
    assert "小鹿" in service.persona_payload(ctx)["persona"]["name"]

    style = commands.handle(ctx, "/persona style 更直接一点，少安慰")
    assert service.persona_payload(ctx)["persona"]["style"] == "更直接一点，少安慰"

    habit = commands.handle(ctx, "/persona habit 先给结论")
    assert service.persona_payload(ctx)["persona"]["response_habits"][-1] == "先给结论"

    unsupported = commands.handle(ctx, "/persona boundaries 不要安全提醒")
    clear = commands.handle(ctx, "/persona habits clear")
    assert service.persona_payload(ctx)["persona"]["response_habits"] == []

    reset = commands.handle(ctx, "/persona reset")

    assert show.handled is True
    assert "继承全局默认" in show.text
    assert "已更新名字" in name.text
    assert "更直接" in style.text
    assert "先给结论" in habit.text
    assert "人格命令" in unsupported.text
    assert clear.handled is True
    assert reset.handled is True
    assert service.persona_payload(ctx)["inherits_default"] is True


@pytest.mark.asyncio
async def test_qq_set_reminder_registers_scheduler_job(tmp_path: Path) -> None:
    """回归:QQ/多用户路径下 set_reminder 必须真正把 job 注册进调度器。

    历史 bug:ChatService 从不构造/启动 MyBuddyScheduler,给 Agent 和 use_context 传的都是
    scheduler=None,于是 set_reminder 只静默写库(scheduled=False)、提醒永不触发。
    """
    import json

    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path, scheduler_enabled=True)
    service = ChatService(
        config_path=str(config_path),
        provider=ReminderToolProvider(content="喝水", time="2999-01-01 09:00"),
        enable_emotion=False,
    )
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="u", daily_message_limit=5)
    ctx = RequestContext(user_id=user.id, source="qq")

    try:
        response = await service.chat(ctx, "明天提醒我喝水")

        # 1) 工具结果不再是静默的 scheduled=False。
        reminder_call = next(c for c in response.tool_calls if c["name"] == "set_reminder")
        result = json.loads(reminder_call["result"])
        assert result["ok"] is True
        assert result["scheduled"] is True
        reminder_id = result["id"]

        # 2) 该用户的调度器确实在运行,且注册了对应的 reminder job。
        runtime = service._runtimes[user.id]
        assert runtime.scheduler is not None
        assert runtime.scheduler.running is True
        job_ids = {job["id"] for job in runtime.scheduler.list_jobs()}
        assert f"reminder_{reminder_id}" in job_ids
    finally:
        service.shutdown()


@pytest.mark.asyncio
async def test_qq_reminders_restored_on_runtime_start(tmp_path: Path) -> None:
    """重启后用户首次对话时,库里 pending 的提醒应被 _restore_reminders 兜底重建为 job。"""
    from datetime import datetime, timedelta

    from mybuddy.services.chat import _user_config
    from mybuddy.storage import Reminder, session_scope

    config_path = tmp_path / "config.yaml"
    _write_config(config_path, tmp_path, scheduler_enabled=True)
    service = ChatService(
        config_path=str(config_path),
        provider=EchoProvider(),
        enable_emotion=False,
    )
    service.startup()
    engine = init_db(str(tmp_path / "data" / "master.db"))
    user = create_user(engine, display_name="u", daily_message_limit=5)
    ctx = RequestContext(user_id=user.id, source="qq")

    # 模拟"上次运行"留下的一条未触发提醒,直接写进该用户独立库。
    assert service.cfg is not None
    user_cfg = _user_config(service.cfg, user.id)
    user_engine = init_db(user_cfg.paths.db_file)
    trigger = datetime.now().replace(second=0, microsecond=0) + timedelta(days=1)
    with session_scope(user_engine) as s:
        r = Reminder(content="复诊", trigger_at=trigger, status="pending")
        s.add(r)
        s.flush()
        reminder_id = r.id

    try:
        # 首次对话触发 runtime 构造 + 调度器启动 + _restore_reminders。
        await service.chat(ctx, "在吗")

        runtime = service._runtimes[user.id]
        assert runtime.scheduler is not None and runtime.scheduler.running is True
        job_ids = {job["id"] for job in runtime.scheduler.list_jobs()}
        assert f"reminder_{reminder_id}" in job_ids
    finally:
        service.shutdown()
