"""身体与最小心智之间唯一的本机 HTTP 窄桥。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mybuddy.config import load_config
from mybuddy.llm import BaseLLMProvider, make_provider
from mybuddy.mind import (
    RECENT_EVENT_LIMIT,
    MindFiles,
    PendingExpression,
    advance_time,
    mind_step,
)

TIME_RETRY_INTERVAL = timedelta(minutes=5)


class BodyEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=160)
    type: Literal["chat"]
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("event_id")
    @classmethod
    def normalize_event_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class BodyStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shown_id: str | None = Field(default=None, min_length=1, max_length=160)
    event: BodyEvent | None = None


class BodyStepResponse(BaseModel):
    baseline: dict[str, str]
    expression: PendingExpression | None
    shown_confirmed: bool
    event_status: Literal["none", "processed", "duplicate", "waiting_for_shown"]
    time_status: Literal["not_due", "advanced", "failed", "waiting_for_shown"]


class BodyBridge:
    """单写顺序：shown → 至多一个 event → 当前非破坏视图。"""

    def __init__(self, *, provider: BaseLLMProvider, files: MindFiles) -> None:
        self.provider = provider
        self.files = files
        self._write_lock = asyncio.Lock()
        self._next_time_attempt_at: datetime | None = None

    async def step(self, request: BodyStepRequest) -> BodyStepResponse:
        async with self._write_lock:
            now = datetime.now(UTC).astimezone()
            shown_confirmed = self._confirm_shown(request.shown_id, now)
            event_status = await self._process_event(request.event, now)
            time_status = await self._advance_time(now) if request.event is None else "not_due"
            state, _, _ = self.files.load(now)
            pending = state.get("pending_expression")
            return BodyStepResponse(
                baseline={
                    str(key): str(value) for key, value in dict(state.get("condition", {})).items()
                },
                expression=PendingExpression.model_validate(pending) if pending else None,
                shown_confirmed=shown_confirmed,
                event_status=event_status,
                time_status=time_status,
            )

    def _confirm_shown(self, shown_id: str | None, now: datetime) -> bool:
        if shown_id is None:
            return False
        state, history, memories = self.files.load(now)
        pending = state.get("pending_expression")
        if not isinstance(pending, dict) or pending.get("id") != shown_id:
            return False
        expression = PendingExpression.model_validate(pending)
        history.append(
            {
                "id": f"shown_{expression.id.removeprefix('expr_')}",
                "type": "shared_expression",
                "content": expression.text,
                "expression_id": expression.id,
                "occurred_at": now.isoformat(),
            }
        )
        state["pending_expression"] = None
        self.files.commit(state, history, memories)
        return True

    async def _process_event(
        self, event: BodyEvent | None, now: datetime
    ) -> Literal["none", "processed", "duplicate", "waiting_for_shown"]:
        if event is None:
            return "none"
        state, _, _ = self.files.load(now)
        recent = [item for item in state.get("recent_event_ids", []) if isinstance(item, str)]
        if event.event_id in recent:
            return "duplicate"
        if state.get("pending_expression") is not None:
            return "waiting_for_shown"

        result = await mind_step(
            event.content,
            provider=self.provider,
            files=self.files,
            now=now,
            event_id=event.event_id,
        )
        if not result.committed:
            state, history, memories = self.files.load(now)
            state["pending_expression"] = result.pending_expression.model_dump()
            state["recent_event_ids"] = [*recent, event.event_id][-RECENT_EVENT_LIMIT:]
            self.files.commit(state, history, memories)
        return "processed"

    async def _advance_time(
        self, now: datetime
    ) -> Literal["not_due", "advanced", "failed", "waiting_for_shown"]:
        state, _, _ = self.files.load(now)
        if state.get("pending_expression") is not None:
            return "waiting_for_shown"
        if self._next_time_attempt_at is not None and now < self._next_time_attempt_at:
            return "not_due"
        result = await advance_time(provider=self.provider, files=self.files, now=now)
        if result.status == "failed":
            self._next_time_attempt_at = now + TIME_RETRY_INTERVAL
        else:
            self._next_time_attempt_at = None
        return result.status


def create_body_app(
    config_path: str = "config.yaml",
    data_dir: str | Path = "data/mini",
    *,
    provider: BaseLLMProvider | None = None,
):
    """创建只暴露身体 step 的应用；provider 参数只用于测试和本地验收。"""
    try:
        from fastapi import FastAPI
    except ModuleNotFoundError as error:  # pragma: no cover
        raise RuntimeError("缺少 API 依赖，请运行: uv sync --extra api") from error

    if provider is None:
        cfg = load_config(config_path)
        if not cfg.llm.api_key:
            raise RuntimeError("当前模型配置缺少 api_key")
        provider = make_provider(cfg.llm)

    bridge = BodyBridge(provider=provider, files=MindFiles(data_dir))
    app = FastAPI(title="MyBuddy body bridge", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.body_bridge = bridge

    @app.post("/api/body/step", response_model=BodyStepResponse)
    async def body_step(request: BodyStepRequest) -> BodyStepResponse:
        return await bridge.step(request)

    return app
