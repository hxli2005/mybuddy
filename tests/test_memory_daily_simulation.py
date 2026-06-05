"""Daily conversation simulation for the minimal memory flow."""

from __future__ import annotations

import json

import pytest

from mybuddy.agent import Agent
from mybuddy.config import Config
from mybuddy.learning import TrajectoryLogger
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolSpec
from mybuddy.memory import LongTermMemory, MemoryManager
from mybuddy.storage import init_db
from mybuddy.tools import ToolRegistry


class DailySimulationProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.chat_systems: list[str] = []
        self.extract_prompts: list[str] = []

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
        if system and "关系记忆管理助手" in system:
            self.extract_prompts.append(messages[-1].content if messages else "")
            return LLMResponse(text=_extraction_payload(), finish_reason="stop")

        prompt = system or ""
        self.chat_systems.append(prompt)
        if (
            "## 未完成话题" in prompt
            and "## 共同经历" in prompt
            and "## 偏好与避雷" in prompt
        ):
            return LLMResponse(
                text=(
                    "嗯，我不催你，也不打鸡血。"
                    "我们只拿报告开头第一句话，不处理整篇。"
                ),
                finish_reason="stop",
            )
        return LLMResponse(text="我在，先慢慢说。", finish_reason="stop")


def _extraction_payload() -> str:
    return json.dumps(
        {
            "facts": ["用户这周在准备项目报告，报告开头一直拖着没写。"],
            "profile_fields": {"当前项目": "周五项目报告"},
            "claims": [
                {
                    "claim": "用户可能经常拖延写作任务",
                    "confidence": 0.5,
                }
            ],
            "relationship_memories": {
                "preference": [
                    {
                        "title": "不要打鸡血",
                        "content": "用户明确不喜欢打鸡血式鼓励，越说越烦。",
                        "triggers": ["鼓励", "拖延", "写报告"],
                        "confidence": 0.85,
                    }
                ],
                "shared_moment": [
                    {
                        "title": "报告开头缩小到一句话",
                        "content": "用户上次在拖延报告时，接受了把开头缩小到一句话的低压方式。",
                        "triggers": ["不想写", "拖延", "报告"],
                        "callback_style": "直接按这个方式做，不要包装成专门术语",
                        "confidence": 0.85,
                    }
                ],
                "open_thread": [
                    {
                        "title": "周五项目报告开头",
                        "content": "用户还没有写周五项目报告的开头。",
                        "contact_reason": "用户说报告开头一直拖着没写",
                        "triggers": ["报告", "开头", "周五"],
                        "confidence": 0.8,
                    }
                ],
            },
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_daily_conversation_generates_minimal_memory_and_uses_it(tmp_path) -> None:
    cfg = Config()
    cfg.memory.extract_after_turns = 3
    engine = init_db(str(tmp_path / "daily_memory.db"))
    ltm = LongTermMemory(persist_dir=tmp_path / "memory")
    provider = DailySimulationProvider()
    memory = MemoryManager(
        engine=engine,
        config=cfg,
        ltm=ltm,
        provider=provider,
        session_id="daily-sim",
    )
    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=memory,
        trajectory_logger=TrajectoryLogger(tmp_path / "traj"),
        engine=engine,
    )

    await agent.run("我这周要写周五的项目报告，开头一直拖着没写。")
    await agent.run("我不喜欢那种打鸡血式鼓励，越说我越烦。")
    await agent.run("别催我，也别给我鼓劲，帮我把报告开头缩到第一句话就行。")

    assert len(provider.extract_prompts) == 1
    assert sorted(item["metadata"]["type"] for item in ltm.list_all()) == [
        "open_thread",
        "preference",
        "profile",
        "shared_moment",
    ]
    assert len(ltm.list_all(mem_type="profile")) == 1
    assert len(ltm.list_all(mem_type="preference")) == 1
    assert len(ltm.list_all(mem_type="shared_moment")) == 1
    assert len(ltm.list_all(mem_type="open_thread")) == 1
    assert memory.profile.get_all_fields() == {"当前项目": "周五项目报告"}
    assert memory.profile.get_all_claims() == []

    result = await agent.run("我又不想写报告开头了。")
    system_prompt = provider.chat_systems[-1]

    assert "## 未完成话题" in system_prompt
    assert "## 共同经历" in system_prompt
    assert "## 偏好与避雷" in system_prompt
    assert "## 关于用户" in system_prompt
    assert "## 关于用户的认知" not in system_prompt
    assert result.related_claim_ids == []
    assert "第一句话" in result.text
    assert "不打鸡血" in result.text
