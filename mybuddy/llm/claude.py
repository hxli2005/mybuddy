"""Anthropic Claude Provider。

使用官方 anthropic SDK 的异步客户端,把统一的 Message / ToolSpec 转换为
Anthropic messages API 格式,响应再转回 LLMResponse。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from anthropic import Anthropic

from mybuddy.config import LLMConfig

from .base import (
    BaseLLMProvider,
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolSpec,
)

logger = logging.getLogger(__name__)

TRANSIENT_RETRY_DELAYS = (0.5, 1.0, 2.0)


class ClaudeProvider(BaseLLMProvider):
    def __init__(self, cfg: LLMConfig) -> None:
        if not cfg.api_key:
            # 允许不带 key 构造(方便 init 阶段 smoke test),实际 generate 时由 SDK 报错
            pass
        self._cfg = cfg
        # 用同步客户端而非 AsyncAnthropic:部分环境下 httpx 的 async 传输会在 TLS 阶段
        # 被重置(ConnectError(EndOfStream)),同步路径正常。请求统一经 asyncio.to_thread
        # 执行,既规避该问题又不阻塞事件循环。
        self._client = Anthropic(
            api_key=cfg.api_key or None,
            base_url=cfg.base_url or None,
        )

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
        api_messages = [_to_anthropic_message(m) for m in messages]
        kwargs: dict[str, Any] = {
            "model": model or self._cfg.model,
            "max_tokens": max_tokens or self._cfg.max_tokens,
            "temperature": temperature if temperature is not None else self._cfg.temperature,
            "messages": api_messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [_to_anthropic_tool(t) for t in tools]

        resp = await self._create_with_retries(kwargs)
        return _from_anthropic_response(resp)

    async def _create_with_retries(self, kwargs: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt, delay in enumerate((*TRANSIENT_RETRY_DELAYS, 0.0), start=1):
            try:
                return await asyncio.to_thread(self._client.messages.create, **kwargs)
            except Exception as e:
                last_error = e
                if not _is_transient_error(e) or attempt > len(TRANSIENT_RETRY_DELAYS):
                    raise
                logger.warning(
                    "anthropic transient error, retrying in %.1fs: %s",
                    delay,
                    _error_summary(e),
                )
                await asyncio.sleep(delay)
        raise RuntimeError("anthropic request failed") from last_error


def _to_anthropic_message(msg: Message) -> dict[str, Any]:
    """把 Message 转成 Anthropic messages 数组条目。

    - user:直接用 content 字符串
    - assistant:无工具调用时用字符串;有工具调用时输出 text/tool_use blocks
    - tool:作为 user 消息里的 tool_result block
    - system:调用方不应该把 system 放进 messages,这里兜底抛错
    """
    if msg.role == Role.SYSTEM:
        raise ValueError("system 角色应通过 generate(system=...) 传入,不放在 messages 里")
    if msg.role == Role.TOOL:
        if not msg.tool_call_id:
            raise ValueError("tool 消息必须带 tool_call_id")
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id,
                    "content": msg.content,
                }
            ],
        }
    if msg.role == Role.ASSISTANT and msg.tool_calls:
        content: list[dict[str, Any]] = []
        if msg.content:
            content.append({"type": "text", "text": msg.content})
        for tc in msg.tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                }
            )
        return {"role": "assistant", "content": content}
    return {"role": msg.role.value, "content": msg.content}


def _to_anthropic_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.parameters or {"type": "object", "properties": {}},
    }


def _from_anthropic_response(resp: Any) -> LLMResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in resp.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=dict(block.input) if block.input else {},
                )
            )
    usage = {}
    if getattr(resp, "usage", None):
        usage = {
            "input_tokens": getattr(resp.usage, "input_tokens", 0),
            "output_tokens": getattr(resp.usage, "output_tokens", 0),
        }
    return LLMResponse(
        text="".join(text_parts),
        tool_calls=tool_calls,
        finish_reason=getattr(resp, "stop_reason", "stop") or "stop",
        usage=usage,
    )


def _is_transient_error(err: Exception) -> bool:
    status = getattr(err, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    name = type(err).__name__.lower()
    return any(part in name for part in ("timeout", "connection", "internalservererror"))


def _error_summary(err: Exception) -> str:
    status = getattr(err, "status_code", None)
    if status is not None:
        return f"{type(err).__name__}(status={status})"
    return type(err).__name__


def make_provider(cfg: LLMConfig) -> BaseLLMProvider:
    """按配置构造 Provider。"""
    if cfg.provider == "anthropic":
        return ClaudeProvider(cfg)
    if cfg.provider in {"openai", "openrouter", "deepseek"}:
        from .openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(cfg)
    raise NotImplementedError(f"暂不支持 Provider: {cfg.provider}")
