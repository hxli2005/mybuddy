"""LLM Provider 抽象层。

所有 Provider 实现统一的 generate 接口,上层 Agent 只面向本模块的
Message / ToolSpec / LLMResponse,未来切换本地 Hermes 或其他云端模型
时只需新增 Provider,不影响业务代码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolSpec(BaseModel):
    """工具描述,parameters 使用 JSON Schema。"""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """模型请求的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """统一消息格式。

    assistant 角色可携带 tool_calls;tool 角色时需带 tool_call_id/name。
    """

    role: Role
    content: str
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class LLMResponse(BaseModel):
    """Provider 的统一返回。"""

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"  # stop | tool_use | length | other
    usage: dict[str, int] = Field(default_factory=dict)


class BaseLLMProvider(ABC):
    """LLM Provider 抽象基类。"""

    @abstractmethod
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
        """根据消息列表生成一次响应。

        - messages: 不含 system(system 走独立参数)
        - tools: 可用工具列表,None 或空表示无工具
        - model/temperature/max_tokens: 覆盖 Provider 默认值
        - system: system prompt,独立于 messages
        """
        raise NotImplementedError
