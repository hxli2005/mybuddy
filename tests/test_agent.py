"""Agent ReAct 循环测试:用 FakeProvider 走完一轮 tool call + 收敛。

M3:Agent 依赖 MemoryManager;测试中用独立 ShortTermMemory 构造。
"""

from __future__ import annotations

from typing import Any

import pytest

from mybuddy.agent import Agent
from mybuddy.config import Config
from mybuddy.learning import TrajectoryLogger
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolCall, ToolSpec
from mybuddy.memory import MemoryManager, ShortTermMemory, UserProfile
from mybuddy.storage import Message as DBMessage
from mybuddy.storage import init_db, session_scope
from mybuddy.tools import ToolRegistry, set_context, tool


class ScriptedProvider(BaseLLMProvider):
    """按预设脚本返回 LLMResponse。"""

    def __init__(self, script: list[LLMResponse]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append({"messages": list(messages), "tools": tools, "system": system})
        if not self._script:
            return LLMResponse(text="(脚本耗尽)", finish_reason="stop")
        return self._script.pop(0)


def _make_memory(engine, config, provider) -> MemoryManager:
    """构造一个无 Chroma 的 MemoryManager(测试用)。"""
    mm = MemoryManager.__new__(MemoryManager)
    mm._engine = engine
    mm._config = config
    mm._ltm = None  # 无 Chroma,只用短期+画像
    mm._provider = provider
    mm._session_id = "test"
    mm._short_term = ShortTermMemory(capacity=config.memory.short_term_size)
    mm._profile = UserProfile(engine, None)
    mm._extractor = None  # 测试中不触发抽取
    mm._recent_turns = []
    mm._turns_since_extract = 0

    # override build_context_section to return empty(no Chroma)
    def _empty_ctx(_user_input: str) -> tuple[str, list[int]]:
        return "", []

    mm.build_context_section = _empty_ctx  # type: ignore[method-assign]

    # override maybe_extract to no-op
    async def _noop() -> bool:
        return False

    mm.maybe_extract = _noop  # type: ignore[method-assign]
    return mm


@pytest.mark.asyncio
async def test_agent_runs_tool_call_then_finishes(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "a.db"))
    set_context(engine=engine, config=cfg)

    reg = ToolRegistry()

    @tool(name="weather", description="mock weather", registry=reg)
    def weather(city: str) -> dict:
        return {"city": city, "condition": "晴"}

    provider = ScriptedProvider(
        [
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="t1", name="weather", arguments={"city": "北京"})],
                finish_reason="tool_use",
            ),
            LLMResponse(text="北京今天天气晴好~", finish_reason="stop"),
        ]
    )

    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=reg,
        memory=memory,
        trajectory_logger=logger,
        max_steps=4,
        engine=engine,
    )

    result = await agent.run("北京天气怎么样?")

    assert result.finish_reason == "stop"
    assert "北京" in result.text
    assert result.steps == 2
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "weather"

    # 第二次 LLM 调用应能看到 tool_result 消息
    second_call_msgs = provider.calls[1]["messages"]
    assistant_msg = [m for m in second_call_msgs if m.role.value == "assistant"]
    assert len(assistant_msg) == 1
    assert len(assistant_msg[0].tool_calls) == 1
    assert assistant_msg[0].tool_calls[0].id == "t1"
    tool_msg = [m for m in second_call_msgs if m.role.value == "tool"]
    assert len(tool_msg) == 1
    assert "晴" in tool_msg[0].content

    with session_scope(engine) as s:
        rows = s.query(DBMessage).order_by(DBMessage.id.asc()).all()
        assert [r.role for r in rows] == ["user", "assistant", "tool", "assistant"]
        assert rows[0].content == "北京天气怎么样?"
        assert rows[1].content == ""
        assert rows[2].content
        assert rows[3].content == "北京今天天气晴好~"


@pytest.mark.asyncio
async def test_agent_persists_plain_chat_messages(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "plain_messages.db"))
    set_context(engine=engine, config=cfg)

    provider = ScriptedProvider([LLMResponse(text="你好，我在。", finish_reason="stop")])
    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=memory,
        trajectory_logger=logger,
        engine=engine,
    )

    result = await agent.run("你好")

    assert result.text == "你好，我在。"
    with session_scope(engine) as s:
        rows = s.query(DBMessage).order_by(DBMessage.id.asc()).all()
        assert [r.role for r in rows] == ["user", "assistant"]
        assert rows[0].content == "你好"
        assert rows[1].content == "你好，我在。"
        assert rows[0].session_id == agent.session_id


@pytest.mark.asyncio
async def test_agent_max_steps_guard(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "b.db"))
    set_context(engine=engine, config=cfg)

    reg = ToolRegistry()

    @tool(name="loop", description="loops", registry=reg)
    def loopfn() -> str:
        return "ok"

    # 永远返回 tool_call,触发 max_steps
    provider = ScriptedProvider(
        [
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id=f"t{i}", name="loop", arguments={})],
                finish_reason="tool_use",
            )
            for i in range(10)
        ]
    )

    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=reg,
        memory=memory,
        trajectory_logger=logger,
        max_steps=3,
    )

    result = await agent.run("无限循环测试")
    assert result.finish_reason == "max_steps"
    assert result.steps == 3
