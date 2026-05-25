"""OpenAI-compatible Provider。

用于 OpenAI、OpenRouter、DeepSeek 等兼容 Chat Completions API 的服务。
OpenRouter 只需要把 base_url 设为 https://openrouter.ai/api/v1。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from mybuddy.config import LLMConfig

from .base import BaseLLMProvider, LLMResponse, Message, Role, ToolCall, ToolSpec

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
TRANSIENT_RETRY_DELAYS = (0.5, 1.0, 2.0)


class OpenAICompatibleProvider(BaseLLMProvider):
    def __init__(self, cfg: LLMConfig) -> None:
        self._cfg = cfg
        self._client = AsyncOpenAI(
            api_key=cfg.api_key or "missing-key",
            base_url=_base_url_for(cfg),
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
        api_messages = _to_openai_messages(messages, system=system)
        kwargs: dict[str, Any] = {
            "model": model or self._cfg.model,
            "messages": api_messages,
            "temperature": temperature if temperature is not None else self._cfg.temperature,
            "max_tokens": max_tokens or self._cfg.max_tokens,
        }
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]

        resp = await self._create_with_retries(kwargs)
        return _from_openai_response(resp)

    async def _create_with_retries(self, kwargs: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt, delay in enumerate((*TRANSIENT_RETRY_DELAYS, 0.0), start=1):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except Exception as e:
                last_error = e
                if not _is_transient_error(e) or attempt > len(TRANSIENT_RETRY_DELAYS):
                    raise
                logger.warning(
                    "openai-compatible transient error, retrying in %.1fs: %s",
                    delay,
                    _error_summary(e),
                )
                await asyncio.sleep(delay)
        raise RuntimeError("openai-compatible request failed") from last_error


def _base_url_for(cfg: LLMConfig) -> str | None:
    if cfg.base_url:
        return cfg.base_url
    if cfg.provider == "openrouter":
        return OPENROUTER_BASE_URL
    if cfg.provider == "deepseek":
        return DEEPSEEK_BASE_URL
    return None


def _to_openai_messages(messages: list[Message], *, system: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages:
        if msg.role == Role.SYSTEM:
            out.append({"role": "system", "content": msg.content})
        elif msg.role == Role.TOOL:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                }
            )
        elif msg.role == Role.ASSISTANT and msg.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": msg.role.value, "content": msg.content})
    return out


def _to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters or {"type": "object", "properties": {}},
        },
    }


def _from_openai_response(resp: Any) -> LLMResponse:
    choice = resp.choices[0] if resp.choices else None
    if choice is None:
        return LLMResponse(text="", finish_reason="stop")

    msg = choice.message
    tool_calls: list[ToolCall] = []
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = tc.function
        tool_calls.append(
            ToolCall(
                id=tc.id,
                name=fn.name,
                arguments=_parse_tool_args(fn.arguments),
            )
        )

    usage = {}
    if getattr(resp, "usage", None):
        usage = {
            "input_tokens": getattr(resp.usage, "prompt_tokens", 0),
            "output_tokens": getattr(resp.usage, "completion_tokens", 0),
        }

    return LLMResponse(
        text=msg.content or "",
        tool_calls=tool_calls,
        finish_reason=getattr(choice, "finish_reason", "stop") or "stop",
        usage=usage,
    )


def _parse_tool_args(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


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
