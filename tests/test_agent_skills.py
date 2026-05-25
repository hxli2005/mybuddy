"""Agent 与 Skills 子系统的集成:匹配注入 + curator 触发。"""

from __future__ import annotations

from typing import Any

import pytest

from mybuddy.agent import Agent
from mybuddy.config import Config
from mybuddy.learning import SkillCurator, SkillRegistry, TrajectoryLogger
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolCall, ToolSpec
from mybuddy.storage import init_db
from mybuddy.tools import ToolRegistry, set_context, tool

from .test_agent import _make_memory


class ScriptedProvider(BaseLLMProvider):
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
        self.calls.append({"messages": list(messages), "system": system})
        if not self._script:
            return LLMResponse(text="(脚本耗尽)", finish_reason="stop")
        return self._script.pop(0)


@pytest.mark.asyncio
async def test_skill_match_injects_into_system_prompt(tmp_path) -> None:
    """user_input 命中 trigger 时,skill 步骤应出现在 system prompt 里。"""
    cfg = Config()
    engine = init_db(str(tmp_path / "a.db"))
    set_context(engine=engine, config=cfg)

    # 准备一个高置信度 skill
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "greet.md").write_text(
        """---
name: 早安问候流程
triggers: ["早上好", "早安"]
success_count: 5
fail_count: 0
confidence: 0.83
archived: false
---
步骤:
1. 温柔回应
2. 聊聊今天计划
""",
        encoding="utf-8",
    )
    registry = SkillRegistry.load_all(skills_dir)

    provider = ScriptedProvider([LLMResponse(text="早呀~今天想做什么?", finish_reason="stop")])
    tools_reg = ToolRegistry()
    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")

    agent = Agent(
        provider=provider,
        config=cfg,
        registry=tools_reg,
        memory=memory,
        trajectory_logger=logger,
        skill_registry=registry,
    )

    result = await agent.run("早上好啊")

    # system prompt 里应有 skill 提示
    system_text = provider.calls[0]["system"]
    assert "做法建议" in system_text
    assert "早安问候流程" in system_text
    assert "温柔回应" in system_text

    # AgentResult 正确上报 triggered_skills
    assert "早安问候流程" in result.triggered_skills
    assert result.trajectory.meta.get("triggered_skills") == ["早安问候流程"]


@pytest.mark.asyncio
async def test_skill_match_does_nothing_when_no_hit(tmp_path) -> None:
    cfg = Config()
    engine = init_db(str(tmp_path / "b.db"))
    set_context(engine=engine, config=cfg)

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "a.md").write_text(
        """---
name: 天气问答
triggers: ["天气"]
confidence: 0.8
---
步骤:
1. 查天气
""",
        encoding="utf-8",
    )
    registry = SkillRegistry.load_all(skills_dir)

    provider = ScriptedProvider([LLMResponse(text="好的。", finish_reason="stop")])
    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")

    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=memory,
        trajectory_logger=logger,
        skill_registry=registry,
    )
    result = await agent.run("随便聊聊")
    assert result.triggered_skills == []
    assert "做法建议" not in provider.calls[0]["system"]


@pytest.mark.asyncio
async def test_curator_triggered_on_complex_task(tmp_path) -> None:
    """≥3 次 tool 调用 + stop → curator.maybe_curate 被触发一次。"""
    cfg = Config()
    engine = init_db(str(tmp_path / "c.db"))
    set_context(engine=engine, config=cfg)

    tools_reg = ToolRegistry()

    @tool(name="t1", description="noop", registry=tools_reg)
    def t1() -> str:
        return "ok"

    @tool(name="t2", description="noop", registry=tools_reg)
    def t2() -> str:
        return "ok"

    @tool(name="t3", description="noop", registry=tools_reg)
    def t3() -> str:
        return "ok"

    # 3 步工具调用,最后收敛
    provider = ScriptedProvider(
        [
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="a", name="t1", arguments={})],
                finish_reason="tool_use",
            ),
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="b", name="t2", arguments={})],
                finish_reason="tool_use",
            ),
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="c", name="t3", arguments={})],
                finish_reason="tool_use",
            ),
            LLMResponse(text="搞定!", finish_reason="stop"),
        ]
    )

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    registry = SkillRegistry(skills_dir)

    # 记录 curator 调用次数
    class SpyCurator(SkillCurator):
        def __init__(self) -> None:
            self.called: int = 0

        async def maybe_curate(self, traj):  # noqa: ANN001
            self.called += 1
            return None

    curator = SpyCurator()

    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=tools_reg,
        memory=memory,
        trajectory_logger=logger,
        skill_registry=registry,
        skill_curator=curator,
        max_steps=6,
    )

    result = await agent.run("连环任务")
    # 让异步 create_task 跑完
    import asyncio as _aio

    await _aio.sleep(0)
    await _aio.sleep(0)

    assert result.finish_reason == "stop"
    assert len(result.tool_calls) == 3
    assert curator.called == 1


@pytest.mark.asyncio
async def test_curator_not_triggered_when_too_few_tool_calls(tmp_path) -> None:
    """<3 次 tool 调用 → curator 不触发。"""
    cfg = Config()
    engine = init_db(str(tmp_path / "d.db"))
    set_context(engine=engine, config=cfg)

    provider = ScriptedProvider([LLMResponse(text="好的。", finish_reason="stop")])

    class SpyCurator(SkillCurator):
        def __init__(self) -> None:
            self.called: int = 0

        async def maybe_curate(self, traj):  # noqa: ANN001
            self.called += 1
            return None

    curator = SpyCurator()

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=memory,
        trajectory_logger=logger,
        skill_registry=SkillRegistry(skills_dir),
        skill_curator=curator,
    )
    await agent.run("随便问问")
    import asyncio as _aio

    await _aio.sleep(0)
    assert curator.called == 0


@pytest.mark.asyncio
async def test_agent_without_skills_system_still_works(tmp_path) -> None:
    """不注入 skill_registry 时 Agent 行为应与 M5 一致。"""
    cfg = Config()
    engine = init_db(str(tmp_path / "e.db"))
    set_context(engine=engine, config=cfg)

    provider = ScriptedProvider([LLMResponse(text="ok", finish_reason="stop")])
    memory = _make_memory(engine, cfg, provider)
    logger = TrajectoryLogger(tmp_path / "traj")

    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=memory,
        trajectory_logger=logger,
    )
    result = await agent.run("hi")
    assert result.text == "ok"
    assert result.triggered_skills == []
    assert result.related_claim_ids == []
