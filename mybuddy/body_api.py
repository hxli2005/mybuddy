"""身体与最小心智之间唯一的本机 HTTP 窄桥。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mybuddy.config import load_config
from mybuddy.llm import BaseLLMProvider, make_provider
from mybuddy.mind import (
    READING_PATH,
    MindFiles,
    PendingExpression,
    WalkEvidence,
    advance_time,
    complete_reading,
    complete_walk,
    discard_activity,
    mind_step,
)


class BodyEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=160)
    type: Literal["chat", "touch_head", "touch_body", "raise"]
    content: str | None = Field(default=None, max_length=4000)

    @field_validator("event_id")
    @classmethod
    def normalize_event_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def content_matches_event_type(self) -> BodyEvent:
        if self.type == "chat":
            if self.content is None or not self.content.strip():
                raise ValueError("chat content must not be blank")
        elif self.content is not None:
            raise ValueError("body events carry only the raw type and event_id")
        return self


class BodyPresence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    present: bool
    fullscreen: bool
    surface: Literal["full", "edge"] = "full"


class BodyActivityReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_id: str = Field(min_length=1, max_length=160)
    status: Literal["completed", "interrupted", "failed"]
    reason: (
        Literal[
            "animation_finished",
            "touch",
            "chat",
            "activity_replaced",
            "raise",
            "animation_fault",
            "window_fault",
        ]
        | None
    ) = None
    motion: WalkEvidence | None = None

    @model_validator(mode="after")
    def completed_motion_must_be_a_horizontal_walk(self) -> BodyActivityReceipt:
        allowed_reasons = {
            "completed": {None, "animation_finished"},
            "interrupted": {"touch", "chat", "activity_replaced", "raise"},
            "failed": {"animation_fault", "window_fault"},
        }
        if self.reason not in allowed_reasons[self.status]:
            raise ValueError(f"{self.status} receipt has incompatible reason")
        if self.status == "completed" and self.motion is not None:
            horizontal = abs(self.motion.end_left - self.motion.start_left)
            vertical = abs(self.motion.end_top - self.motion.start_top)
            if horizontal < 1 or vertical > 0.5:
                raise ValueError("completed walk must move horizontally inside one work area")
        return self


class BodyActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["read", "walk"]
    duration_ms: int = Field(default=15_000, ge=0)


class BodyStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shown_id: str | None = Field(default=None, min_length=1, max_length=160)
    activity_receipt: BodyActivityReceipt | None = None
    event: BodyEvent | None = None
    presence: BodyPresence | None = None


class BodyStepResponse(BaseModel):
    activity: BodyActivity | None
    expression: PendingExpression | None
    shown_confirmed: bool
    activity_confirmed: bool
    event_status: Literal["none", "processed", "duplicate", "waiting_for_shown"]
    time_status: Literal["not_due", "scheduled", "waiting_for_activity", "waiting_for_shown"]
    mind_status: Literal["not_run", "accepted", "rejected", "unavailable"]


class BodyBridge:
    """单写顺序：shown → 至多一个 event → 当前非破坏视图。"""

    def __init__(self, *, provider: BaseLLMProvider, files: MindFiles) -> None:
        self.provider = provider
        self.files = files
        self._write_lock = asyncio.Lock()

    async def step(self, request: BodyStepRequest) -> BodyStepResponse:
        async with self._write_lock:
            now = datetime.now(UTC).astimezone()
            shown_confirmed = self._confirm_shown(request.shown_id, now)
            activity_confirmed, receipt_mind_status = await self._confirm_activity(
                request.activity_receipt, request.presence, now
            )
            self._discard_stale_ambient(now, request.presence)
            event_status, mind_status = await self._process_event(request.event, now)
            time_status = (
                self._advance_time(now, request.presence)
                if request.event is None and request.activity_receipt is None
                else "not_due"
            )
            if mind_status == "not_run":
                mind_status = receipt_mind_status
            state, _, _ = self.files.load(now)
            pending = state.get("pending_expression")
            pending_activity = state.get("pending_activity")
            activity = (
                BodyActivity.model_validate(
                    {
                        key: pending_activity[key]
                        for key in ("id", "type", "duration_ms")
                        if key in pending_activity
                    }
                )
                if isinstance(pending_activity, dict)
                else None
            )
            return BodyStepResponse(
                activity=activity,
                expression=PendingExpression.model_validate(pending) if pending else None,
                shown_confirmed=shown_confirmed,
                activity_confirmed=activity_confirmed,
                event_status=event_status,
                time_status=time_status,
                mind_status=mind_status,
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
                "expression_kind": expression.kind,
                "expression_act": expression.act,
                "expression_evidence_ids": expression.evidence_ids,
                "expression_target_id": expression.target_id,
                "occurred_at": now.isoformat(),
            }
        )
        state["pending_expression"] = None
        self.files.commit(state, history, memories)
        return True

    async def _confirm_activity(
        self,
        receipt: BodyActivityReceipt | None,
        presence: BodyPresence | None,
        now: datetime,
    ) -> tuple[bool, Literal["not_run", "accepted", "rejected", "unavailable"]]:
        if receipt is None:
            return False, "not_run"
        state, _, _ = self.files.load(now)
        recent = [item for item in state.get("recent_activity_ids", []) if isinstance(item, str)]
        if receipt.activity_id in recent:
            return True, "not_run"
        pending = state.get("pending_activity")
        if not isinstance(pending, dict) or pending.get("id") != receipt.activity_id:
            return False, "not_run"
        activity_type = pending.get("type")
        if receipt.status != "completed":
            confirmed = discard_activity(receipt.activity_id, files=self.files, now=now)
            return confirmed, "not_run"
        if activity_type == "walk":
            if receipt.motion is None:
                return False, "rejected"
            confirmed = complete_walk(
                receipt.activity_id,
                receipt.motion,
                files=self.files,
                now=now,
            )
            return confirmed, "not_run" if confirmed else "rejected"
        if activity_type != "read" or receipt.motion is not None:
            return False, "rejected"
        result = await complete_reading(
            receipt.activity_id,
            provider=self.provider,
            files=self.files,
            now=now,
            allow_ambient=self._ambient_allowed(presence, now),
        )
        if result.committed:
            return True, "accepted"
        return False, _failure_status(result.rejection_reasons)

    def _discard_stale_ambient(self, now: datetime, presence: BodyPresence | None) -> bool:
        state, history, memories = self.files.load(now)
        pending = state.get("pending_expression")
        if not isinstance(pending, dict) or pending.get("kind") != "ambient":
            return False
        try:
            created_at = datetime.fromisoformat(str(pending["created_at"]))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=now.tzinfo)
        except (KeyError, TypeError, ValueError):
            return False
        if (presence is None or presence.surface != "edge") and created_at.astimezone(
            now.tzinfo
        ).date() >= now.date():
            return False
        state["pending_expression"] = None
        self.files.commit(state, history, memories)
        return True

    async def _process_event(
        self, event: BodyEvent | None, now: datetime
    ) -> tuple[
        Literal["none", "processed", "duplicate", "waiting_for_shown"],
        Literal["not_run", "accepted", "rejected", "unavailable"],
    ]:
        if event is None:
            return "none", "not_run"
        state, _, _ = self.files.load(now)
        recent = [item for item in state.get("recent_event_ids", []) if isinstance(item, str)]
        if event.event_id in recent:
            return "duplicate", "not_run"
        if state.get("pending_expression") is not None:
            return "waiting_for_shown", "not_run"

        if event.type == "chat":
            result = await mind_step(
                event.content,
                provider=self.provider,
                files=self.files,
                now=now,
                event_id=event.event_id,
            )
        elif event.type.startswith("touch_"):
            result = await mind_step(
                None,
                experience_type="body_touch",
                experience_details={"zone": event.type.removeprefix("touch_")},
                fallback_text="碰到我了。刚才脑子没转过来，但这一下我感觉到了。",
                provider=self.provider,
                files=self.files,
                now=now,
                event_id=event.event_id,
            )
        else:
            result = await mind_step(
                None,
                experience_type="body_raise",
                fallback_text="刚才被你提起来又放下了。脑子没转过来，但这一下我感觉到了。",
                provider=self.provider,
                files=self.files,
                now=now,
                event_id=event.event_id,
            )
        mind_status = "accepted" if result.committed else _failure_status(result.rejection_reasons)
        return "processed", mind_status

    def _advance_time(
        self, now: datetime, presence: BodyPresence | None
    ) -> Literal["not_due", "scheduled", "waiting_for_activity", "waiting_for_shown"]:
        if presence is not None and presence.surface == "edge":
            return "not_due"
        state, _, _ = self.files.load(now)
        if state.get("pending_expression") is not None:
            return "waiting_for_shown"
        if state.get("pending_activity") is not None:
            return "waiting_for_activity"
        return advance_time(files=self.files, now=now).status

    def _ambient_allowed(self, presence: BodyPresence | None, now: datetime) -> bool:
        if (
            presence is None
            or not presence.present
            or presence.fullscreen
            or presence.surface == "edge"
        ):
            return False
        _, history, _ = self.files.load(now)
        for item in history:
            if item.get("type") != "shared_expression" or item.get("expression_kind") != "ambient":
                continue
            try:
                occurred_at = datetime.fromisoformat(str(item["occurred_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if occurred_at.astimezone(now.tzinfo).date() == now.date():
                return False
        return True


def _failure_status(reasons: list[str]) -> Literal["rejected", "unavailable"]:
    unavailable = any(reason.startswith("模型调用失败：") for reason in reasons)
    return "unavailable" if unavailable else "rejected"


def create_body_app(
    config_path: str = "config.yaml",
    data_dir: str | Path = "data/mini",
    *,
    provider: BaseLLMProvider | None = None,
    reading_path: str | Path = READING_PATH,
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

    bridge = BodyBridge(provider=provider, files=MindFiles(data_dir, reading_path))
    app = FastAPI(title="MyBuddy body bridge", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.body_bridge = bridge

    @app.post("/api/body/step", response_model=BodyStepResponse)
    async def body_step(request: BodyStepRequest) -> BodyStepResponse:
        return await bridge.step(request)

    return app
