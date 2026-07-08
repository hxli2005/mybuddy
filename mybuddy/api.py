"""FastAPI 后端 + 静态前端入口。

这是演示用单用户后端:复用现有 Agent、Memory、Tools、FeedbackBus 装配,
并把 `frontend/` 里的静态页面托管出来。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

from mybuddy._time import utcnow as _utcnow
from mybuddy.agent import Agent
from mybuddy.config import Config, PersonaConfig, ensure_dirs, load_config
from mybuddy.emotion import EmotionDetector, EmotionTracker
from mybuddy.integrations.vpet import (
    chat_to_vpet_payload,
    normalize_body_state,
    pending_to_vpet_payload,
)
from mybuddy.learning import (
    FeedbackBus,
    FeedbackEvent,
    SkillCurator,
    SkillRegistry,
    TrajectoryLogger,
    make_skill_subscriber,
    make_trajectory_subscriber,
)
from mybuddy.llm import Message as LLMMessage
from mybuddy.llm import Role, make_provider
from mybuddy.memory import LongTermMemory, MemoryManager, UserProfile
from mybuddy.scheduler import MyBuddyScheduler
from mybuddy.storage import (
    Message as StoredMessage,
)
from mybuddy.storage import (
    Note,
    PendingMessage,
    Reminder,
    UserSummaryRecord,
    VPetEvent,
    append_message,
    bind_external_account,
    count_vpet_escalations_today,
    create_user,
    delete_user_persona,
    drain_pending,
    get_message_content,
    get_user,
    init_db,
    latest_assistant_message_id,
    list_messages,
    list_undelivered,
    list_user_summaries,
    mark_vpet_event_result,
    record_vpet_event,
    resolve_user_persona,
    session_scope,
    set_user_daily_limit,
    set_user_persona,
    set_user_status,
)
from mybuddy.tools import (
    ToolRegistry,
    set_context,
    setup_memory_tool,
    setup_skill_tool,
    use_context,
)
from mybuddy.tools.reminder import parse_reminder_time

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.llm import BaseLLMProvider


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class VPetChatRequest(BaseModel):
    message: str = Field(min_length=1)
    event: str = "chat"
    body_state: dict[str, Any] | None = None


class VPetEventRequest(BaseModel):
    event: str
    count: int = 1
    body_state: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    want_reply: bool = False
    client_event_id: str | None = None


class VPetDrainRequest(BaseModel):
    digest: bool = False


class OpenAICompatMessage(BaseModel):
    role: str
    content: Any


class OpenAIChatCompletionRequest(BaseModel):
    model: str = "mybuddy"
    messages: list[OpenAICompatMessage] = Field(default_factory=list)
    stream: bool = False


class FeedbackRequest(BaseModel):
    label: str
    turn_id: str | None = None


class ProfileFieldUpdateRequest(BaseModel):
    value: str = Field(min_length=1)


class MemoryUpdateRequest(BaseModel):
    content: str | None = None
    metadata: dict[str, Any] | None = None


class PersonaUpdateRequest(BaseModel):
    name: str | None = None
    style: str | None = None
    language: str | None = None
    relationship: str | None = None
    tone: str | None = None
    boundaries: str | None = None
    response_habits: list[str] | None = None
    roleplay_style: dict[str, Any] | None = None
    character_life: dict[str, Any] | None = None
    relationship_model: dict[str, Any] | None = None
    address_user: str | None = None


class NoteCreateRequest(BaseModel):
    content: str = Field(min_length=1)
    title: str | None = None
    tags: list[str] | None = None


class NoteUpdateRequest(BaseModel):
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None


class UserCreateRequest(BaseModel):
    display_name: str = Field(min_length=1)
    daily_message_limit: int = Field(default=30, ge=0)


class UserUpdateRequest(BaseModel):
    status: str | None = None
    daily_message_limit: int | None = Field(default=None, ge=0)


class UserQQBindRequest(BaseModel):
    external_id: str = Field(min_length=1)
    display_name: str | None = None


class UserPersonaUpdateRequest(PersonaUpdateRequest):
    pass


class ReminderUpdateRequest(BaseModel):
    status: str


class SkillUpdateRequest(BaseModel):
    archived: bool | None = None


@dataclass
class AppState:
    config_path: str
    max_steps: int = 6
    enable_scheduler: bool = True
    cfg: Config | None = None
    engine: Engine | None = None
    provider: BaseLLMProvider | None = None
    ltm: LongTermMemory | None = None
    profile: UserProfile | None = None
    skill_registry: SkillRegistry | None = None
    scheduler: MyBuddyScheduler | None = None
    agent: Agent | None = None
    feedback_bus: FeedbackBus | None = None
    last_turn_id: str | None = None
    last_triggered_skills: list[str] = field(default_factory=list)
    agent_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def startup(self) -> None:
        cfg = load_config(self.config_path)
        ensure_dirs(cfg)
        engine = init_db(cfg.paths.db_file)
        ltm = LongTermMemory(
            persist_dir=cfg.paths.chroma_dir,
            embedding_model=cfg.memory.embedding_model,
        )
        ltm.normalize_metadata()
        provider = make_provider(cfg.llm) if cfg.llm.api_key else None
        logger = TrajectoryLogger(cfg.paths.trajectories_dir)
        profile = UserProfile(engine, ltm)
        skill_registry = SkillRegistry.load_all(cfg.paths.skills_dir)

        scheduler: MyBuddyScheduler | None = None
        if cfg.scheduler.enabled and self.enable_scheduler:
            scheduler = MyBuddyScheduler(cfg)
            scheduler.start()
            _restore_reminders(scheduler, engine)
            scheduler.schedule_daily_greeting(cfg.scheduler.daily_greeting)
            scheduler.schedule_dream_job(cfg.scheduler.dream_job, config_path=self.config_path)

        agent: Agent | None = None
        feedback_bus: FeedbackBus | None = None
        if provider is not None:
            registry = ToolRegistry.default()
            memory = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=provider)
            setup_memory_tool(ltm)
            setup_skill_tool(skill_registry)
            feedback_bus = FeedbackBus()
            feedback_bus.subscribe(make_trajectory_subscriber(logger))
            feedback_bus.subscribe(make_skill_subscriber(skill_registry))
            agent = Agent(
                provider=provider,
                config=cfg,
                registry=registry,
                memory=memory,
                trajectory_logger=logger,
                max_steps=self.max_steps,
                emotion_detector=EmotionDetector(provider, cfg.llm.small_model),
                emotion_tracker=EmotionTracker(window=5),
                engine=engine,
                scheduler=scheduler,
                skill_registry=skill_registry,
                skill_curator=SkillCurator(provider, skill_registry, model=cfg.llm.small_model),
            )
            set_context(
                engine=engine,
                config=cfg,
                scheduler=scheduler,
                provider=provider,
                long_term=ltm,
            )
        else:
            set_context(engine=engine, config=cfg, scheduler=scheduler, long_term=ltm)

        self.cfg = cfg
        self.engine = engine
        self.provider = provider
        self.ltm = ltm
        self.profile = profile
        self.skill_registry = skill_registry
        self.scheduler = scheduler
        self.agent = agent
        self.feedback_bus = feedback_bus

    def shutdown(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown()

    def status_payload(self) -> dict[str, Any]:
        cfg = _require(self.cfg)
        scheduler_jobs = self.scheduler.list_jobs() if self.scheduler is not None else []
        return {
            "configured": bool(cfg.llm.api_key),
            "persona": cfg.persona.model_dump(),
            "model": cfg.llm.model,
            "tools": ToolRegistry.default().names(),
            "scheduler_jobs": scheduler_jobs,
            "memory_dir": cfg.paths.chroma_dir,
        }

    def persona_payload(self) -> dict[str, Any]:
        cfg = _require(self.cfg)
        return {"persona": cfg.persona.model_dump()}

    def update_persona_payload(self, updates: dict[str, Any]) -> dict[str, Any]:
        cfg = _require(self.cfg)
        merged = cfg.persona.model_dump()
        merged.update(_clean_persona_updates(updates))
        persona = PersonaConfig.model_validate(merged)
        _write_persona_config(self.config_path, persona)

        updated_cfg = load_config(self.config_path)
        self._sync_config(updated_cfg)
        return {"persona": updated_cfg.persona.model_dump()}

    def _sync_config(self, cfg: Config) -> None:
        self.cfg = cfg
        if self.agent is not None:
            self.agent._config = cfg
            self.agent._memory._config = cfg
        if self.scheduler is not None:
            self.scheduler._config = cfg
            if self.scheduler.running:
                self.scheduler.schedule_daily_greeting(cfg.scheduler.daily_greeting)
        set_context(
            engine=self.engine,
            config=cfg,
            scheduler=self.scheduler,
            provider=self.provider,
            long_term=self.ltm,
        )

    async def chat_payload(
        self,
        message: str,
        *,
        source: str = "chat",
        enable_tools: bool = True,
        meta: dict[str, Any] | None = None,
        body_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.agent is None:
            raise RuntimeError("LLM api_key 未配置,无法对话")
        async with self.agent_lock:
            # 工具上下文是 ContextVar,按线程/按 task 隔离。startup() 在主线程(或 FastAPI
            # 启动协程)里 set_context,但实际跑对话时:web.py 用 ThreadingHTTPServer 每请求
            # 开新线程 + asyncio.run(全新 context),FastAPI 每请求是独立 task —— 两种情况下
            # 启动时设的上下文都传不到这里,工具(web_search/weather 等)会拿到 config=None。
            # 因此在真正会调用工具的请求协程内重新建立上下文(与多用户 ChatService 同款做法)。
            with use_context(
                engine=self.engine,
                config=self.cfg,
                scheduler=self.scheduler,
                provider=self.provider,
                long_term=self.ltm,
                skill_registry=self.skill_registry,
            ):
                engine = _require(self.engine)
                pending_before = _integrate_pending_messages(
                    engine,
                    session_id=self.agent.session_id,
                    items=drain_pending(engine),
                    add_to_short_term=self.agent._memory.add_message,
                )
                result = await self.agent.run(
                    message.strip(),
                    source=source,
                    enable_tools=enable_tools,
                    meta=meta,
                    body_state=body_state,
                )
                result_text = result.text
                tool_calls = list(result.tool_calls)
                if enable_tools:
                    deterministic_tools = await _run_deterministic_demo_tools(
                        message, tool_calls, self
                    )
                    if deterministic_tools:
                        tool_calls.extend(deterministic_tools)
                result_text = _append_tool_summary(result_text, tool_calls)
                pending_after = _integrate_pending_messages(
                    engine,
                    session_id=self.agent.session_id,
                    items=drain_pending(engine),
                    add_to_short_term=self.agent._memory.add_message,
                )
                self.last_turn_id = result.trajectory.turn_id
                self.last_triggered_skills = list(result.triggered_skills)
                return {
                    "text": result_text,
                    "turn_id": result.trajectory.turn_id,
                    "steps": result.steps,
                    "finish_reason": result.finish_reason,
                    "tool_calls": tool_calls,
                    "emotion": result.emotion.to_dict() if result.emotion else None,
                    "emotional_support": result.emotional_support,
                    "triggered_skills": result.triggered_skills,
                    "search_sources": result.search_sources,
                    "pending_messages": pending_before + pending_after,
                }

    async def vpet_chat_payload(
        self,
        message: str,
        *,
        event: str = "chat",
        body_state: dict[str, Any] | None = None,
        client_flags: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_body = normalize_body_state(body_state)
        body_state_used = bool(self.cfg and self.cfg.vpet.body_state_injection and normalized_body)
        event_log_id: int | None = None
        flags = _server_flags(self.cfg)
        engine = self.engine
        if engine is not None:
            row, _ = record_vpet_event(
                engine,
                event=event or "chat",
                count=1,
                body_state=normalized_body,
                context={
                    "kind": "chat",
                    "message_length": len(message.strip()),
                    "body_state_used": body_state_used,
                },
                want_reply=True,
                client_flags=client_flags or {},
                server_flags=flags,
                last_emotion_label=_last_emotion_label(self),
                day_index=_vpet_day_index(engine),
            )
            event_log_id = int(row["id"])
        vpet_meta = {
            "vpet": {
                "event": event,
                "body_state_present": bool(normalized_body),
                "body_state_used": body_state_used,
                "client_flags": client_flags or {},
                "server_flags": flags,
                **({"event_log_id": event_log_id} if event_log_id is not None else {}),
            }
        }
        try:
            chat = await self.chat_payload(
                message,
                source="vpet_chat",
                meta=vpet_meta,
                body_state=normalized_body if body_state_used else None,
            )
        except TypeError as e:
            # 兼容少量测试/外部嵌入直接 monkeypatch 旧签名 chat_payload(message)。
            if "unexpected keyword argument" not in str(e):
                raise
            chat = await self.chat_payload(message)
        turn_id = str(chat.get("turn_id") or "") or None
        message_id = (
            latest_assistant_message_id(engine, turn_id=turn_id)
            if engine is not None and turn_id is not None
            else None
        )
        if engine is not None and event_log_id is not None:
            mark_vpet_event_result(
                engine,
                event_log_id,
                replied=bool(chat.get("text")),
                turn_id=turn_id,
                message_id=message_id,
            )
        text = str(chat.get("text") or "")
        self._record_body_state_conflicts(
            body_state=normalized_body,
            text=text,
            source_event=event,
            turn_id=turn_id,
            message_id=message_id,
            client_flags=client_flags,
        )
        return chat_to_vpet_payload(chat, source_event=event)

    async def openai_chat_completion_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str = "mybuddy",
    ) -> dict[str, Any]:
        user_text = _last_user_message_text(messages)
        if not user_text:
            raise RuntimeError("messages 中缺少 user 内容")
        chat = await self.chat_payload(user_text)
        vpet = chat_to_vpet_payload(chat, source_event="chat")
        finish_reason = chat.get("finish_reason") or "stop"
        if finish_reason not in {"stop", "length", "tool_calls", "content_filter"}:
            finish_reason = "stop"
        turn_id = str(chat.get("turn_id") or int(time.time()))
        return {
            "id": f"chatcmpl-mybuddy-{turn_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model or "mybuddy",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": chat.get("text") or "",
                    },
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "mybuddy": {
                "turn_id": chat.get("turn_id"),
                "emotion": chat.get("emotion"),
                "emotional_support": chat.get("emotional_support"),
                "action": vpet["action"],
                "expression": vpet["expression"],
                "pending": vpet["pending"],
            },
        }

    def vpet_pending_payload(
        self,
        *,
        drain: bool = False,
        digest: bool = False,
        client_flags: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        engine = _require(self.engine)
        if not drain:
            return pending_to_vpet_payload(list_undelivered(engine), drained=False)
        if digest:
            return self._vpet_digest_pending_payload(client_flags=client_flags)

        items = drain_pending(engine)
        self._record_vpet_pending_delivery_telemetry(
            items,
            event="pending_drained",
            client_flags=client_flags,
        )
        if self.agent is not None:
            items = _integrate_pending_messages(
                engine,
                session_id=self.agent.session_id,
                items=items,
                add_to_short_term=self.agent._memory.add_message,
            )
        return pending_to_vpet_payload(items, drained=True)

    async def vpet_event_payload(
        self,
        req: VPetEventRequest,
        *,
        client_flags: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        engine = _require(self.engine)
        event = req.event.strip()
        if event not in {"touch_head", "touch_body", "feed", "user_back"}:
            raise RuntimeError("unsupported vpet event")
        count = _clamp_int(req.count, low=1, high=50)
        body_state = normalize_body_state(req.body_state)
        context = req.context if isinstance(req.context, dict) else {}
        flags = _server_flags(self.cfg)
        body_state_used = bool(self.cfg and self.cfg.vpet.body_state_injection and body_state)
        row, created = record_vpet_event(
            engine,
            event=event,
            count=count,
            body_state=body_state,
            context=context,
            want_reply=bool(req.want_reply),
            client_event_id=req.client_event_id,
            client_flags=client_flags or {},
            server_flags=flags,
            last_emotion_label=_last_emotion_label(self),
            day_index=_vpet_day_index(engine),
        )
        if not created:
            if not req.want_reply:
                return {
                    "ok": True,
                    "replied": False,
                    "gate_reason": row.get("gate_reason"),
                    "event_log_id": row["id"],
                }
            return _vpet_event_replay_payload(engine, row)

        if not req.want_reply or event == "user_back":
            mark_vpet_event_result(engine, row["id"], replied=False)
            return {
                "ok": True,
                "replied": False,
                "gate_reason": None,
                "event_log_id": row["id"],
            }

        cfg = _require(self.cfg)
        if not cfg.vpet.touch_escalation:
            return self._mark_vpet_event_gate(row["id"], "escalation_disabled")
        if self.agent_lock.locked():
            return self._mark_vpet_event_gate(row["id"], "agent_busy")
        if count_vpet_escalations_today(engine) >= max(0, cfg.vpet.touch_escalation_daily_limit):
            return self._mark_vpet_event_gate(row["id"], "budget_exceeded")
        if self.agent is None:
            raise RuntimeError("LLM api_key 未配置,无法对话")

        await self.agent_lock.acquire()
        try:
            synthetic_input = _vpet_event_prompt(event, count=count, context=context)
            with use_context(
                engine=self.engine,
                config=self.cfg,
                scheduler=self.scheduler,
                provider=self.provider,
                long_term=self.ltm,
                skill_registry=self.skill_registry,
            ):
                result = await self.agent.run(
                    synthetic_input,
                    source="vpet_event",
                    enable_tools=False,
                    meta={
                        "vpet": {
                            "event": event,
                            "count": count,
                            "body_state_present": bool(body_state),
                            "body_state_used": body_state_used,
                            "client_event_id": req.client_event_id,
                            "event_log_id": row["id"],
                            "client_flags": client_flags or {},
                            "server_flags": flags,
                        }
                    },
                    body_state=body_state if body_state_used else None,
                )
        finally:
            self.agent_lock.release()

        message_id = latest_assistant_message_id(engine, turn_id=result.trajectory.turn_id)
        reply_text = _short_vpet_reaction(result.text)
        if message_id is not None and reply_text != result.text:
            _update_message_content(engine, message_id, reply_text)
        mark_vpet_event_result(
            engine,
            row["id"],
            escalated=True,
            replied=True,
            turn_id=result.trajectory.turn_id,
            message_id=message_id,
        )
        payload = chat_to_vpet_payload(
            {
                "text": reply_text,
                "turn_id": result.trajectory.turn_id,
                "finish_reason": result.finish_reason,
                "emotion": result.emotion.to_dict() if result.emotion else None,
                "emotional_support": result.emotional_support,
                "tool_calls": result.tool_calls,
                "triggered_skills": result.triggered_skills,
                "search_sources": result.search_sources,
                "pending_messages": [],
            },
            source_event=event,
        )
        payload["replied"] = True
        payload["event_log_id"] = row["id"]
        self._record_body_state_conflicts(
            body_state=body_state,
            text=reply_text,
            source_event=event,
            turn_id=result.trajectory.turn_id,
            message_id=message_id,
            client_flags=client_flags,
        )
        return payload

    def _mark_vpet_event_gate(self, event_id: int, gate_reason: str) -> dict[str, Any]:
        engine = _require(self.engine)
        mark_vpet_event_result(engine, event_id, gate_reason=gate_reason)
        return {
            "ok": True,
            "replied": False,
            "gate_reason": gate_reason,
            "event_log_id": event_id,
        }

    def _vpet_digest_pending_payload(
        self,
        *,
        client_flags: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        engine = _require(self.engine)
        cfg = _require(self.cfg)
        now = _utcnow()
        reminder_cutoff = now - timedelta(minutes=cfg.vpet.reminder_overdue_after_minutes)
        greeting_cutoff = now - timedelta(minutes=cfg.vpet.greeting_discard_after_minutes)
        events: list[dict[str, Any]] = []
        digest_sources: list[str] = []
        discarded_count = 0
        flags = _server_flags(cfg)
        telemetry: list[dict[str, Any]] = []

        with session_scope(engine) as s:
            rows = (
                s.query(PendingMessage)
                .filter(PendingMessage.delivered_at.is_(None))
                .filter(PendingMessage.scheduled_at <= now)
                .order_by(PendingMessage.scheduled_at.asc())
                .all()
            )
            for pm in rows:
                meta = _safe_json(pm.meta_json)
                base = {
                    "id": pm.id,
                    "source": pm.source,
                    "content": pm.content,
                    "scheduled_at": pm.scheduled_at.isoformat(timespec="seconds"),
                    "meta": meta,
                }
                if pm.source == "reminder" and pm.scheduled_at <= reminder_cutoff:
                    overdue = dict(base)
                    overdue["persistent"] = True
                    overdue["interrupt"] = False
                    overdue["meta"] = {**meta, "overdue": True}
                    overdue["role"] = "system"
                    events.append(overdue)
                    pm.delivered_at = now
                    if "reminder" not in digest_sources:
                        digest_sources.append("reminder")
                    telemetry.append(
                        {
                            "event": "pending_overdue",
                            "pending_message_id": pm.id,
                            "source": pm.source,
                            "reason": "overdue_reminder",
                        }
                    )
                    continue
                if pm.source == "greeting" and pm.scheduled_at <= greeting_cutoff:
                    pm.delivered_at = now
                    discarded_count += 1
                    telemetry.append(
                        {
                            "event": "pending_discarded",
                            "reason": "stale_greeting",
                            "pending_message_id": pm.id,
                            "source": pm.source,
                        }
                    )
                    continue
                if pm.source in {"nudge", "dynamic"}:
                    pm.delivered_at = now
                    if pm.source not in digest_sources:
                        digest_sources.append(pm.source)
                    telemetry.append(
                        {
                            "event": "pending_digested",
                            "pending_message_id": pm.id,
                            "source": pm.source,
                        }
                    )
                    continue

                pm.delivered_at = now
                events.append(base)
                telemetry.append(
                    {
                        "event": "pending_drained",
                        "pending_message_id": pm.id,
                        "source": pm.source,
                    }
                )

        for item in telemetry:
            event_name = str(item.pop("event"))
            record_vpet_event(
                engine,
                event=event_name,
                count=1,
                context=item,
                client_flags=client_flags or {},
                server_flags=flags,
                day_index=_vpet_day_index(engine),
            )

        payload = pending_to_vpet_payload(events, drained=True)
        payload["digest"] = {
            "text": _digest_text(digest_sources, discarded_count),
            "sources": digest_sources,
            "discarded_count": discarded_count,
        }
        return payload

    def _record_vpet_pending_delivery_telemetry(
        self,
        items: list[dict[str, Any]],
        *,
        event: str,
        client_flags: dict[str, Any] | None = None,
    ) -> None:
        if not items or self.engine is None:
            return
        flags = _server_flags(self.cfg)
        for item in items:
            record_vpet_event(
                self.engine,
                event=event,
                count=1,
                context={
                    "pending_message_id": item.get("id"),
                    "source": item.get("source"),
                },
                client_flags=client_flags or {},
                server_flags=flags,
                day_index=_vpet_day_index(self.engine),
            )

    def _record_body_state_conflicts(
        self,
        *,
        body_state: dict[str, Any],
        text: str,
        source_event: str,
        turn_id: str | None,
        message_id: int | None,
        client_flags: dict[str, Any] | None = None,
    ) -> None:
        if not body_state or not text or self.engine is None:
            return
        reasons = _body_state_conflicts(text, body_state)
        if not reasons:
            return
        record_vpet_event(
            self.engine,
            event="body_state_conflict",
            count=len(reasons),
            body_state=body_state,
            context={
                "source_event": source_event,
                "turn_id": turn_id,
                "message_id": message_id,
                "reasons": reasons,
                "text_sample": text[:80],
            },
            client_flags=client_flags or {},
            server_flags=_server_flags(self.cfg),
            day_index=_vpet_day_index(self.engine),
        )

    def vpet_status_payload(self) -> dict[str, Any]:
        status = self.status_payload()
        return {
            "ok": True,
            "bridge": "vpet-bridge/1",
            "configured": status["configured"],
            "persona": status.get("persona", {}),
            "model": status.get("model"),
            "actions": [
                "talk",
                "happy",
                "comfort",
                "concern",
                "safety",
                "thinking",
                "greet",
                "remind",
                "notify",
                "react",
                "idle",
            ],
        }

    def feedback_payload(self, label: str, turn_id: str | None = None) -> dict[str, Any]:
        if self.feedback_bus is None:
            raise RuntimeError("反馈总线未初始化")
        tid = turn_id or self.last_turn_id
        if not tid:
            raise RuntimeError("没有可反馈的对话轮次")
        clean_label = label.strip()
        self.feedback_bus.publish(
            FeedbackEvent(
                turn_id=tid,
                label=clean_label,
                meta={"triggered_skills": list(self.last_triggered_skills)},
            )
        )
        return {"ok": True, "turn_id": tid, "label": clean_label}

    def profile_payload(self) -> dict[str, Any]:
        p = _require(self.profile)
        return {
            "fields": p.get_all_fields(),
        }

    def messages_payload(self, *, limit: int = 100, session_id: str | None = None) -> dict[str, Any]:
        engine = _require(self.engine)
        return {"messages": list_messages(engine, limit=limit, session_id=session_id)}

    def users_payload(self) -> dict[str, Any]:
        engine = _require(self.engine)
        return {"users": [_user_summary_payload(item) for item in list_user_summaries(engine)]}

    def create_user_payload(
        self,
        *,
        display_name: str,
        daily_message_limit: int = 30,
    ) -> dict[str, Any]:
        clean_name = display_name.strip()
        if not clean_name:
            raise RuntimeError("测试用户名称为空")
        engine = _require(self.engine)
        user = create_user(
            engine,
            display_name=clean_name,
            daily_message_limit=daily_message_limit,
        )
        return {"user": _find_user_summary_payload(engine, user.id)}

    def update_user_payload(
        self,
        user_id: int,
        *,
        status: str | None = None,
        daily_message_limit: int | None = None,
    ) -> dict[str, Any]:
        if status is None and daily_message_limit is None:
            raise RuntimeError("没有可更新的测试用户字段")
        engine = _require(self.engine)
        updated = None
        if status is not None:
            updated = set_user_status(engine, user_id, _clean_user_status(status))
        if daily_message_limit is not None:
            updated = set_user_daily_limit(engine, user_id, daily_message_limit)
        if updated is None:
            raise RuntimeError(f"测试用户不存在:id={user_id}")
        return {"user": _find_user_summary_payload(engine, user_id)}

    def bind_user_qq_payload(
        self,
        user_id: int,
        *,
        external_id: str,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        clean_external_id = external_id.strip()
        if not clean_external_id:
            raise RuntimeError("QQ external_id 为空")
        engine = _require(self.engine)
        try:
            bind_external_account(
                engine,
                user_id=user_id,
                provider="qq",
                external_id=clean_external_id,
                display_name=(display_name or "").strip(),
            )
        except ValueError as e:
            raise RuntimeError(str(e)) from e
        return {"user": _find_user_summary_payload(engine, user_id)}

    def user_persona_payload(self, user_id: int) -> dict[str, Any]:
        engine = _require(self.engine)
        cfg = _require(self.cfg)
        if get_user(engine, user_id) is None:
            raise RuntimeError(f"测试用户不存在:id={user_id}")
        resolved = resolve_user_persona(
            engine,
            user_id=user_id,
            default_persona=cfg.persona,
        )
        return {
            "user_id": user_id,
            "inherits_default": resolved.inherits_default,
            "version": resolved.version,
            "persona": resolved.persona.model_dump(),
        }

    def update_user_persona_payload(self, user_id: int, updates: dict[str, Any]) -> dict[str, Any]:
        engine = _require(self.engine)
        cfg = _require(self.cfg)
        if get_user(engine, user_id) is None:
            raise RuntimeError(f"测试用户不存在:id={user_id}")
        resolved = resolve_user_persona(
            engine,
            user_id=user_id,
            default_persona=cfg.persona,
        )
        merged = resolved.persona.model_dump()
        merged.update(_clean_persona_updates(updates))
        persona = PersonaConfig.model_validate(merged)
        try:
            record = set_user_persona(engine, user_id=user_id, persona=persona)
        except ValueError as e:
            raise RuntimeError(str(e)) from e
        return {
            "user_id": user_id,
            "inherits_default": False,
            "version": record.version,
            "persona": record.persona.model_dump(),
        }

    def delete_user_persona_payload(self, user_id: int) -> dict[str, Any]:
        engine = _require(self.engine)
        cfg = _require(self.cfg)
        if get_user(engine, user_id) is None:
            raise RuntimeError(f"测试用户不存在:id={user_id}")
        delete_user_persona(engine, user_id)
        return {
            "user_id": user_id,
            "inherits_default": True,
            "version": "default",
            "persona": cfg.persona.model_dump(),
        }

    def update_profile_field_payload(self, key: str, value: str) -> dict[str, Any]:
        clean_key = key.strip()
        clean_value = value.strip()
        if not clean_key:
            raise RuntimeError("画像字段名为空")
        if not clean_value:
            raise RuntimeError("画像字段值为空")
        p = _require(self.profile)
        p.set_field(clean_key, clean_value)
        return {"field": {"key": clean_key, "value": clean_value}}

    def delete_profile_field_payload(self, key: str) -> dict[str, Any]:
        clean_key = key.strip()
        if not clean_key:
            raise RuntimeError("画像字段名为空")
        p = _require(self.profile)
        if not p.delete_field(clean_key):
            raise RuntimeError(f"画像字段不存在:{clean_key}")
        return {"ok": True, "key": clean_key}

    def memory_payload(self) -> dict[str, Any]:
        cfg = _require(self.cfg)
        ltm = _require(self.ltm)
        ltm.normalize_metadata()
        base = Path(cfg.paths.chroma_dir)
        return {
            "archive": ltm.list_all()[:50],
            "conversations": _read_jsonl_tail(base / "conversations", limit=20),
            "raw": _read_jsonl_tail(base / "raw", limit=20),
        }

    def update_memory_payload(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_id = memory_id.strip()
        if not clean_id:
            raise RuntimeError("记忆 ID 为空")
        ltm = _require(self.ltm)
        clean_content = content.strip() if content is not None else None
        if content is not None and not clean_content:
            raise RuntimeError("记忆内容为空")
        original = _find_memory_item(ltm, clean_id)
        if original is None:
            raise RuntimeError(f"记忆不存在:id={clean_id}")
        updated = ltm.update(clean_id, content=clean_content, metadata=_clean_memory_metadata(metadata))
        if updated is None:
            raise RuntimeError(f"记忆不存在:id={clean_id}")
        if self.engine is not None:
            _sync_memory_backing_update(self.engine, original, updated)
        return {"memory": updated}

    def delete_memory_payload(self, memory_id: str) -> dict[str, Any]:
        clean_id = memory_id.strip()
        if not clean_id:
            raise RuntimeError("记忆 ID 为空")
        ltm = _require(self.ltm)
        item = _find_memory_item(ltm, clean_id)
        if item is None:
            raise RuntimeError(f"记忆不存在:id={clean_id}")
        if self.engine is not None:
            _sync_memory_backing_delete(self.engine, item)
        ltm.delete(clean_id)
        return {"ok": True, "id": clean_id}

    def reminders_payload(self) -> dict[str, Any]:
        engine = _require(self.engine)
        with session_scope(engine) as s:
            rows = s.query(Reminder).order_by(Reminder.trigger_at.asc()).limit(30).all()
            items = [
                {
                    "id": r.id,
                    "content": r.content,
                    "trigger_at": r.trigger_at.isoformat(timespec="minutes"),
                    "status": r.status,
                }
                for r in rows
            ]
        return {"reminders": items, "pending_messages": list_undelivered(engine)}

    def update_reminder_payload(self, reminder_id: int, status: str) -> dict[str, Any]:
        if status != "cancelled":
            raise RuntimeError("目前只支持把提醒取消为 cancelled")
        engine = _require(self.engine)
        with session_scope(engine) as s:
            row = s.query(Reminder).filter(Reminder.id == reminder_id).one_or_none()
            if row is None:
                raise RuntimeError(f"提醒不存在:id={reminder_id}")
            if row.status != "pending":
                raise RuntimeError(f"状态非 pending,无法取消:{row.status}")
            row.status = "cancelled"
            item = {
                "id": row.id,
                "content": row.content,
                "trigger_at": row.trigger_at.isoformat(timespec="minutes"),
                "status": row.status,
            }
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.cancel_reminder(reminder_id)
        return {"reminder": item}

    def skills_payload(self) -> dict[str, Any]:
        registry = _require(self.skill_registry)
        return {
            "skills": [
                {
                    "name": s.name,
                    "triggers": s.triggers,
                    "confidence": s.confidence,
                    "success_count": s.success_count,
                    "fail_count": s.fail_count,
                    "archived": s.archived,
                }
                for s in registry.all(include_archived=True)
            ]
        }

    def update_skill_payload(self, name: str, archived: bool | None) -> dict[str, Any]:
        if archived is None:
            raise RuntimeError("archived is required")
        registry = _require(self.skill_registry)
        skill = registry.get(name)
        if skill is None:
            raise RuntimeError(f"skill 不存在:{name}")
        skill.archived = archived
        registry.save(skill)
        return {
            "skill": {
                "name": skill.name,
                "triggers": skill.triggers,
                "confidence": skill.confidence,
                "success_count": skill.success_count,
                "fail_count": skill.fail_count,
                "archived": skill.archived,
            }
        }

    def notes_payload(self, limit: int = 30) -> dict[str, Any]:
        engine = _require(self.engine)
        with session_scope(engine) as s:
            rows = s.query(Note).order_by(Note.created_at.desc()).limit(limit).all()
            notes = [_note_payload(row) for row in rows]
        return {"notes": notes}

    def create_note_payload(
        self,
        *,
        content: str,
        title: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        clean_content = content.strip()
        if not clean_content:
            raise RuntimeError("笔记内容为空")
        clean_title = (title or "").strip() or clean_content[:30]
        tag_list = _clean_note_tags(tags)
        engine = _require(self.engine)
        with session_scope(engine) as s:
            row = Note(
                title=clean_title,
                content=clean_content,
                tags_json=json.dumps(tag_list, ensure_ascii=False) if tag_list else None,
            )
            s.add(row)
            s.flush()
            note = _note_payload(row)
        ltm = self.ltm
        if ltm is not None:
            ltm.add(
                clean_content,
                mem_type="note",
                uid=f"note_{note['id']}",
                extra_meta={
                    "sql_id": note["id"],
                    "title": clean_title,
                    "tags": ",".join(tag_list),
                    "source": "user_note",
                    "importance": 0.85,
                },
            )
        return {"note": note}

    def update_note_payload(
        self,
        note_id: int,
        *,
        content: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        clean_content = content.strip() if content is not None else None
        if content is not None and not clean_content:
            raise RuntimeError("笔记内容为空")
        clean_title = title.strip() if title is not None else None
        tag_list = _clean_note_tags(tags) if tags is not None else None

        engine = _require(self.engine)
        with session_scope(engine) as s:
            row = s.query(Note).filter(Note.id == note_id).one_or_none()
            if row is None:
                raise RuntimeError(f"笔记不存在:id={note_id}")
            if clean_content is not None:
                row.content = clean_content
            if clean_title is not None:
                row.title = clean_title or row.content[:30]
            if tag_list is not None:
                row.tags_json = json.dumps(tag_list, ensure_ascii=False) if tag_list else None
            s.flush()
            note = _note_payload(row)

        ltm = self.ltm
        if ltm is not None:
            ltm.update(
                f"note_{note_id}",
                content=note["content"],
                metadata={
                    "type": "note",
                    "sql_id": note_id,
                    "title": note["title"],
                    "tags": note["tags"],
                    "source": "user_note",
                    "importance": 0.85,
                },
            )
        return {"note": note}

    def delete_note_payload(self, note_id: int) -> dict[str, Any]:
        engine = _require(self.engine)
        with session_scope(engine) as s:
            row = s.query(Note).filter(Note.id == note_id).one_or_none()
            if row is None:
                raise RuntimeError(f"笔记不存在:id={note_id}")
            s.delete(row)
        if self.ltm is not None:
            self.ltm.delete(f"note_{note_id}")
        return {"ok": True, "id": note_id}


def create_app(config_path: str = "config.yaml", max_steps: int = 6):
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import FileResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ModuleNotFoundError as e:  # pragma: no cover - 只有未安装 api extra 时触发
        raise RuntimeError("缺少 API 依赖,请运行: uv sync --extra api") from e

    state = AppState(config_path=config_path, max_steps=max_steps)
    app = FastAPI(title="MyBuddy Demo API")
    app.state.mybuddy = state

    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    static_dir = _frontend_static_dir(frontend_dir)
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.middleware("http")
    async def _bridge_auth_middleware(request: Request, call_next):  # noqa: ANN001
        token = _bridge_token(state)
        if token and _requires_bridge_auth(request.url.path):
            if request.headers.get("X-MyBuddy-Token", "") != token:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.on_event("startup")
    async def _startup() -> None:
        state.startup()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        state.shutdown()

    @app.get("/")
    async def index():
        from fastapi.responses import HTMLResponse

        path = _frontend_index_path(frontend_dir)
        if path is None:
            return HTMLResponse(_frontend_not_built_html(frontend_dir), status_code=503)
        return FileResponse(path)

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return state.status_payload()

    @app.get("/api/persona")
    async def persona() -> dict[str, Any]:
        return state.persona_payload()

    @app.put("/api/persona")
    async def update_persona(req: PersonaUpdateRequest) -> dict[str, Any]:
        return state.update_persona_payload(req.model_dump(exclude_none=True))

    @app.post("/api/persona")
    async def update_persona_post(req: PersonaUpdateRequest) -> dict[str, Any]:
        return state.update_persona_payload(req.model_dump(exclude_none=True))

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> dict[str, Any]:
        if state.agent is None:
            raise HTTPException(status_code=400, detail="LLM api_key 未配置,无法对话")
        return await state.chat_payload(req.message)

    @app.get("/api/vpet/status")
    async def vpet_status() -> dict[str, Any]:
        return state.vpet_status_payload()

    @app.post("/api/vpet/chat")
    async def vpet_chat(req: VPetChatRequest, request: Request) -> dict[str, Any]:
        if state.agent is None:
            raise HTTPException(status_code=400, detail="LLM api_key 未配置,无法对话")
        return await state.vpet_chat_payload(
            req.message,
            event=req.event,
            body_state=req.body_state,
            client_flags=_parse_client_flags(request.headers.get("X-MyBuddy-Client-Flags")),
        )

    @app.post("/api/vpet/event")
    async def vpet_event(req: VPetEventRequest, request: Request) -> dict[str, Any]:
        try:
            return await state.vpet_event_payload(
                req,
                client_flags=_parse_client_flags(
                    request.headers.get("X-MyBuddy-Client-Flags")
                ),
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/vpet/pending")
    async def vpet_pending() -> dict[str, Any]:
        return state.vpet_pending_payload(drain=False)

    @app.post("/api/vpet/pending/drain")
    async def vpet_pending_drain(req: VPetDrainRequest, request: Request) -> dict[str, Any]:
        return state.vpet_pending_payload(
            drain=True,
            digest=req.digest,
            client_flags=_parse_client_flags(request.headers.get("X-MyBuddy-Client-Flags")),
        )

    @app.post("/v1/chat/completions")
    async def openai_chat_completions(req: OpenAIChatCompletionRequest) -> dict[str, Any]:
        if req.stream:
            raise HTTPException(status_code=400, detail="stream=true 暂不支持")
        if state.agent is None:
            raise HTTPException(status_code=400, detail="LLM api_key 未配置,无法对话")
        try:
            return await state.openai_chat_completion_payload(
                messages=[m.model_dump() for m in req.messages],
                model=req.model,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/messages")
    async def messages(limit: int = 100, session_id: str | None = None) -> dict[str, Any]:
        return state.messages_payload(limit=limit, session_id=session_id)

    @app.get("/api/users")
    async def users() -> dict[str, Any]:
        return state.users_payload()

    @app.post("/api/users")
    async def create_test_user(req: UserCreateRequest) -> dict[str, Any]:
        try:
            return state.create_user_payload(
                display_name=req.display_name,
                daily_message_limit=req.daily_message_limit,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.patch("/api/users/{user_id}")
    async def update_test_user(user_id: int, req: UserUpdateRequest) -> dict[str, Any]:
        try:
            return state.update_user_payload(
                user_id,
                status=req.status,
                daily_message_limit=req.daily_message_limit,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/users/{user_id}/qq")
    async def bind_test_user_qq(user_id: int, req: UserQQBindRequest) -> dict[str, Any]:
        try:
            return state.bind_user_qq_payload(
                user_id,
                external_id=req.external_id,
                display_name=req.display_name,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/users/{user_id}/persona")
    async def user_persona(user_id: int) -> dict[str, Any]:
        try:
            return state.user_persona_payload(user_id)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.put("/api/users/{user_id}/persona")
    async def update_user_persona(
        user_id: int,
        req: UserPersonaUpdateRequest,
    ) -> dict[str, Any]:
        try:
            return state.update_user_persona_payload(
                user_id,
                req.model_dump(exclude_none=True),
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.delete("/api/users/{user_id}/persona")
    async def delete_user_persona(user_id: int) -> dict[str, Any]:
        try:
            return state.delete_user_persona_payload(user_id)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/feedback")
    async def feedback(req: FeedbackRequest) -> dict[str, Any]:
        try:
            return state.feedback_payload(req.label, req.turn_id)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/profile")
    async def profile() -> dict[str, Any]:
        return state.profile_payload()

    @app.patch("/api/profile/fields/{key}")
    async def update_profile_field(key: str, req: ProfileFieldUpdateRequest) -> dict[str, Any]:
        try:
            return state.update_profile_field_payload(key, req.value)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.delete("/api/profile/fields/{key}")
    async def delete_profile_field(key: str) -> dict[str, Any]:
        try:
            return state.delete_profile_field_payload(key)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/memory")
    async def memory() -> dict[str, Any]:
        return state.memory_payload()

    @app.patch("/api/memory/archive/{memory_id}")
    async def update_memory(memory_id: str, req: MemoryUpdateRequest) -> dict[str, Any]:
        try:
            return state.update_memory_payload(
                memory_id,
                content=req.content,
                metadata=req.metadata,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.delete("/api/memory/archive/{memory_id}")
    async def delete_memory(memory_id: str) -> dict[str, Any]:
        try:
            return state.delete_memory_payload(memory_id)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/reminders")
    async def reminders() -> dict[str, Any]:
        return state.reminders_payload()

    @app.patch("/api/reminders/{reminder_id}")
    async def update_reminder(reminder_id: int, req: ReminderUpdateRequest) -> dict[str, Any]:
        try:
            return state.update_reminder_payload(reminder_id, req.status)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/skills")
    async def skills() -> dict[str, Any]:
        return state.skills_payload()

    @app.patch("/api/skills/{name}")
    async def update_skill(name: str, req: SkillUpdateRequest) -> dict[str, Any]:
        try:
            return state.update_skill_payload(name, req.archived)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/notes")
    async def notes() -> dict[str, Any]:
        return state.notes_payload()

    @app.post("/api/notes")
    async def create_note(req: NoteCreateRequest) -> dict[str, Any]:
        try:
            return state.create_note_payload(content=req.content, title=req.title, tags=req.tags)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.patch("/api/notes/{note_id}")
    async def update_note(note_id: int, req: NoteUpdateRequest) -> dict[str, Any]:
        try:
            return state.update_note_payload(
                note_id,
                content=req.content,
                title=req.title,
                tags=req.tags,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.delete("/api/notes/{note_id}")
    async def delete_note(note_id: int) -> dict[str, Any]:
        try:
            return state.delete_note_payload(note_id)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return app


def _frontend_static_dir(frontend_dir: Path) -> Path:
    dist = frontend_dir / "dist"
    return dist if (dist / "index.html").exists() else frontend_dir


def _frontend_index_path(frontend_dir: Path) -> Path | None:
    """构建后的前端入口;未构建(无 dist)时返回 None。

    不再退回 frontend/index.html —— 那个文件根本不存在(Vite 入口在 src/index.html 且引用
    原始 /main.tsx,本服务也不会转译),只会让 / 回一个莫名的 404 file not found。
    """
    dist_index = frontend_dir / "dist" / "index.html"
    return dist_index if dist_index.exists() else None


def _frontend_not_built_html(frontend_dir: Path) -> str:
    """前端未构建时 / 返回的可读提示页(取代莫名的 404)。"""
    return (
        "<!doctype html><meta charset='utf-8'><title>MyBuddy · 前端未构建</title>"
        '<div style="font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;'
        'padding:0 1.5rem;line-height:1.7;color:#333">'
        "<h1>前端尚未构建</h1>"
        "<p>本服务托管的是前端构建产物 <code>frontend/dist/</code>,当前不存在。</p>"
        "<p>先构建前端再刷新本页:</p>"
        '<pre style="background:#f4f4f5;padding:1rem;border-radius:8px;overflow:auto">'
        f"cd {frontend_dir}\nnpm install\nnpm run build</pre>"
        "<p>本地开发也可用 <code>npm run dev</code> 起 Vite(它会把 <code>/api</code> 代理到本服务)。</p>"
        "</div>"
    )


def _user_summary_payload(item: UserSummaryRecord) -> dict[str, Any]:
    usage_today = dict(sorted(item.usage_today.items()))
    return {
        "id": item.user.id,
        "display_name": item.user.display_name,
        "status": item.user.status,
        "daily_message_limit": item.user.daily_message_limit,
        "usage_today": usage_today,
        "usage_total_today": sum(usage_today.values()),
        "has_custom_persona": item.has_custom_persona,
        "external_accounts": [
            {
                "provider": account.provider,
                "external_id": account.external_id,
                "display_name": account.display_name,
            }
            for account in item.external_accounts
        ],
    }


def _find_user_summary_payload(engine: Engine, user_id: int) -> dict[str, Any]:
    for item in list_user_summaries(engine):
        if item.user.id == user_id:
            return _user_summary_payload(item)
    raise RuntimeError(f"测试用户不存在:id={user_id}")


def _clean_user_status(status: str) -> str:
    clean = status.strip().lower()
    if clean not in {"active", "disabled"}:
        raise RuntimeError("测试用户状态只支持 active 或 disabled")
    return clean


def _note_payload(row: Note) -> dict[str, Any]:
    tags: list[str] = []
    if row.tags_json:
        try:
            loaded = json.loads(row.tags_json)
            if isinstance(loaded, list):
                tags = [str(t) for t in loaded if str(t).strip()]
        except json.JSONDecodeError:
            tags = []
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "tags": tags,
        "created_at": row.created_at.isoformat(timespec="minutes"),
        "updated_at": row.updated_at.isoformat(timespec="minutes"),
    }


def _clean_note_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,，\s]+", value)
    elif isinstance(value, list | tuple | set):
        raw = list(value)
    else:
        raw = [value]
    return [str(t).strip() for t in raw if str(t).strip()]


def _clean_memory_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    blocked = {"id", "created_at", "updated_at"}
    clean: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key).strip()
        if not clean_key or clean_key in blocked:
            continue
        if isinstance(item, str):
            clean[clean_key] = item.strip()
        elif isinstance(item, bool | int | float):
            clean[clean_key] = item
        elif isinstance(item, list):
            clean[clean_key] = [str(x).strip() for x in item if str(x).strip()]
    return clean


def _find_memory_item(ltm: LongTermMemory, memory_id: str) -> dict[str, Any] | None:
    for item in ltm.list_all():
        if item.get("id") == memory_id:
            return item
    return None


def _memory_sql_id(item: dict[str, Any], prefix: str) -> int | None:
    meta = item.get("metadata", {}) or {}
    raw = meta.get("sql_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    item_id = str(item.get("id") or "")
    prefix_text = f"{prefix}_"
    if item_id.startswith(prefix_text) and item_id.removeprefix(prefix_text).isdigit():
        return int(item_id.removeprefix(prefix_text))
    return None


def _sync_memory_backing_update(
    engine: Engine,
    original: dict[str, Any],
    updated: dict[str, Any],
) -> None:
    meta = updated.get("metadata", {}) or original.get("metadata", {}) or {}
    mem_type = str(meta.get("type") or "")
    if mem_type == "note":
        note_id = _memory_sql_id(updated, "note") or _memory_sql_id(original, "note")
        if note_id is None:
            return
        with session_scope(engine) as s:
            row = s.query(Note).filter(Note.id == note_id).one_or_none()
            if row is None:
                return
            row.content = updated["content"]
            title = meta.get("title")
            if isinstance(title, str) and title.strip():
                row.title = title.strip()[:128]
            if "tags" in meta:
                tags = _clean_note_tags(meta.get("tags"))
                row.tags_json = json.dumps(tags, ensure_ascii=False) if tags else None
        return
    # 命题已合并为 SQLite 单一真相源,不再以档案卡形式经记忆端点编辑。


def _sync_memory_backing_delete(engine: Engine, item: dict[str, Any]) -> None:
    meta = item.get("metadata", {}) or {}
    mem_type = str(meta.get("type") or "")
    if mem_type == "note":
        note_id = _memory_sql_id(item, "note")
        if note_id is None:
            return
        with session_scope(engine) as s:
            row = s.query(Note).filter(Note.id == note_id).one_or_none()
            if row is not None:
                s.delete(row)
        return
    # 命题已合并为 SQLite 单一真相源,不再以档案卡形式经记忆端点删除。


def _server_flags(cfg: Config | None) -> dict[str, bool]:
    if cfg is None:
        return {
            "body_state_injection": False,
            "touch_escalation": False,
            "physical_proactive": False,
        }
    return {
        "body_state_injection": bool(cfg.vpet.body_state_injection),
        "touch_escalation": bool(cfg.vpet.touch_escalation),
        "physical_proactive": bool(cfg.vpet.physical_proactive),
    }


def _parse_client_flags(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        str(k): v
        for k, v in loaded.items()
        if isinstance(v, bool | int | float | str) or v is None
    }


def _bridge_token(state: AppState) -> str:
    cfg = state.cfg
    if cfg is None:
        try:
            cfg = load_config(state.config_path)
        except Exception:
            return ""
    return cfg.vpet.bridge_token.strip()


def _requires_bridge_auth(path: str) -> bool:
    return path.startswith("/api/") or path.startswith("/v1/")


def _clamp_int(value: Any, *, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def _vpet_day_index(engine: Engine) -> int:
    today = _utcnow().date()
    with session_scope(engine) as s:
        first = s.query(VPetEvent.created_at).order_by(VPetEvent.created_at.asc()).first()
    if first is None or first[0] is None:
        return 1
    return max((today - first[0].date()).days + 1, 1)


def _vpet_event_prompt(event: str, *, count: int, context: dict[str, Any]) -> str:
    if event == "touch_head":
        return (
            f"用户刚刚摸了摸你的头,30 秒内共 {count} 次。"
            "这不是普通聊天输入;请只给一句很短的自然反应,像桌宠被轻轻碰到后的即时回应。"
            "不要展开新话题,不要反问,不要给建议。"
        )
    if event == "touch_body":
        return (
            f"用户刚刚轻轻碰了碰你/戳了戳你,30 秒内共 {count} 次。"
            "这不是普通聊天输入;请只给一句很短的自然反应,像桌宠被轻轻碰到后的即时回应。"
            "不要展开新话题,不要反问,不要给建议。"
        )
    item = str(context.get("item") or "").strip() or "一点东西"
    return (
        f"用户刚刚给你喂了{item}。"
        "这不是普通聊天输入;请只给一句很短的自然反应,像桌宠被轻轻碰到后的即时回应。"
        "不要展开新话题,不要反问,不要给建议。"
    )


def _digest_text(sources: list[str], discarded_count: int) -> str:
    parts: list[str] = []
    if "reminder" in sources:
        parts.append("一个提醒")
    if any(source in sources for source in ("nudge", "dynamic")):
        parts.append("还有一次想叫你歇会儿" if parts else "一次想叫你歇会儿")
    if not parts:
        if discarded_count:
            return "你不在的时候有些过期问候,我已经替你收掉了。"
        return ""
    return f"你不在的时候我攒了{_cn_count(len(parts))}件事:{'、'.join(parts)}。"


def _cn_count(value: int) -> str:
    return {1: "一", 2: "两", 3: "三"}.get(value, str(value))


def _last_emotion_label(state: AppState) -> str | None:
    agent = state.agent
    tracker = getattr(agent, "_emotion_tracker", None) if agent is not None else None
    latest = getattr(tracker, "latest", None)
    if callable(latest):
        item = latest()
        if item is not None:
            return str(getattr(item, "label", "") or "") or None
    items = (
        getattr(tracker, "_results", None)
        or getattr(tracker, "_items", None)
        or getattr(tracker, "_window", None)
    )
    if items:
        last = list(items)[-1]
        return str(getattr(last, "label", "") or "") or None
    return None


def _short_vpet_reaction(text: str, *, limit: int = 28) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    match = re.search(r"[。！？!?]", clean)
    if match is not None:
        clean = clean[: match.end()]
    else:
        clean = re.split(r"[；;]", clean, maxsplit=1)[0].strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip("，,、；;。！？!? ")


def _update_message_content(engine: Engine, message_id: int, text: str) -> None:
    with session_scope(engine) as s:
        row = s.query(StoredMessage).filter(StoredMessage.id == message_id).one_or_none()
        if row is not None:
            row.content = text


def _body_state_conflicts(text: str, body_state: dict[str, Any]) -> list[str]:
    clean = str(text or "")
    reasons: list[str] = []
    food = _body_state_number(body_state, "food")
    drink = _body_state_number(body_state, "drink")
    feeling = _body_state_number(body_state, "feeling")
    health = _body_state_number(body_state, "health")
    strength = _body_state_number(body_state, "strength")
    mode = str(body_state.get("mode") or "")

    if food is not None and food <= 30 and _contains_any(clean, ["不饿", "吃饱", "饱了", "刚吃", "吃撑"]):
        reasons.append("food_low_but_reply_satiated")
    if food is not None and food >= 70 and _contains_any(clean, ["饿坏", "饿扁", "饿得不行", "肚子空"]):
        reasons.append("food_high_but_reply_hungry")
    if drink is not None and drink <= 30 and _contains_any(clean, ["不渴", "刚喝", "喝饱", "水还温"]):
        reasons.append("drink_low_but_reply_not_thirsty")
    if feeling is not None and feeling <= 30 and _contains_any(clean, ["心情很好", "超开心", "开心得很", "很高兴"]):
        reasons.append("feeling_low_but_reply_happy")
    if strength is not None and strength <= 30 and _contains_any(clean, ["精力满满", "元气满满", "有力气", "不累"]):
        reasons.append("strength_low_but_reply_energetic")
    if (
        (health is not None and health <= 30) or mode in {"Ill", "PoorCondition"}
    ) and _contains_any(clean, ["身体很好", "很健康", "没不舒服", "状态很好"]):
        reasons.append("health_low_or_bad_mode_but_reply_healthy")
    return reasons


def _body_state_number(body_state: dict[str, Any], key: str) -> float | None:
    value = body_state.get(key)
    if isinstance(value, int | float):
        return float(value)
    return None


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(item in text for item in needles)


def _vpet_event_replay_payload(engine: Engine, row: dict[str, Any]) -> dict[str, Any]:
    if row.get("replied"):
        text = _short_vpet_reaction(get_message_content(engine, row.get("message_id")))
        payload = chat_to_vpet_payload(
            {
                "text": text,
                "turn_id": row.get("turn_id"),
                "finish_reason": "stop",
                "pending_messages": [],
            },
            source_event=str(row.get("event") or "event"),
        )
        payload["replied"] = True
        payload["event_log_id"] = row.get("id")
        return payload
    return {
        "ok": True,
        "replied": False,
        "gate_reason": row.get("gate_reason"),
        "event_log_id": row.get("id"),
    }


def _restore_reminders(scheduler: MyBuddyScheduler, engine: Engine) -> None:
    now = _utcnow()
    with session_scope(engine) as s:
        rows = (
            s.query(Reminder)
            .filter(Reminder.status == "pending")
            .filter(Reminder.trigger_at > now)
            .all()
        )
        pending = [(r.id, r.trigger_at) for r in rows]
    for rid, trigger in pending:
        scheduler.schedule_reminder(rid, trigger)


def _clean_persona_updates(updates: dict[str, Any]) -> dict[str, Any]:
    allowed = set(PersonaConfig.model_fields)
    clean: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in allowed or value is None:
            continue
        if key == "response_habits":
            if isinstance(value, list):
                clean[key] = [str(item).strip() for item in value if str(item).strip()]
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        clean[key] = value
    return clean


def _write_persona_config(config_path: str, persona: PersonaConfig) -> None:
    path = Path(config_path)
    replacement = yaml.safe_dump(
        {"persona": persona.model_dump()},
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    if not path.exists():
        path.write_text(replacement + "\n", encoding="utf-8")
        return

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.startswith("persona:")), None)
    if start is None:
        suffix = "" if text.endswith("\n") else "\n"
        path.write_text(text + suffix + "\n" + replacement + "\n", encoding="utf-8")
        return

    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.startswith("#") or (line.strip() and not line.startswith((" ", "\t"))):
            break
        if line.strip() == "":
            break
        end += 1

    new_text = "".join(lines[:start]) + replacement + "\n" + "".join(lines[end:])
    path.write_text(new_text, encoding="utf-8")


WEATHER_INTENT_RE = re.compile(r"(天气|气温|下雨|降雨|温度|weather)", re.I)


async def _run_deterministic_demo_tools(
    message: str,
    existing_tool_calls: list[dict[str, Any]],
    state: AppState,
) -> list[dict[str, Any]]:
    """演示稳定性补偿:明显工具意图但模型未调用/传错参数时,后端校正。"""
    reminder_calls = _repair_or_run_reminder(message, existing_tool_calls, state)
    weather_calls = await _run_weather_fallback(message, existing_tool_calls)
    return reminder_calls + weather_calls


async def _run_weather_fallback(
    message: str,
    existing_tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if any(call.get("name") == "weather" for call in existing_tool_calls):
        return []
    if not WEATHER_INTENT_RE.search(message):
        return []
    city = _extract_weather_city(message)
    if not city:
        return []
    result_text = await ToolRegistry.default().execute("weather", {"city": city})
    return [
        {
            "id": "det_weather",
            "name": "weather",
            "arguments": {"city": city},
            "result": result_text,
            "source": "backend_intent_fallback",
        }
    ]


REMINDER_INTENT_RE = re.compile(r"(提醒我|提醒一下|记得提醒|叫我|闹钟|remind)", re.I)


def _repair_or_run_reminder(
    message: str,
    existing_tool_calls: list[dict[str, Any]],
    state: AppState,
) -> list[dict[str, Any]]:
    parsed = _parse_reminder_request(message)
    if parsed is None:
        return []
    content, trigger = parsed
    trigger_iso = trigger.isoformat(timespec="minutes")
    existing = [c for c in existing_tool_calls if c.get("name") == "set_reminder"]
    if existing:
        for call in existing:
            result = _safe_json(call.get("result"))
            reminder_id = result.get("id")
            old_time = result.get("trigger_at") or call.get("arguments", {}).get("time")
            if old_time == trigger_iso:
                continue
            if isinstance(reminder_id, int):
                _update_reminder_time(state, reminder_id, trigger)
            result.update(
                {
                    "ok": True,
                    "id": reminder_id,
                    "content": result.get("content") or content,
                    "trigger_at": trigger_iso,
                    "scheduled": result.get("scheduled", False),
                    "corrected": True,
                }
            )
            call["arguments"] = {"content": result["content"], "time": trigger_iso}
            call["result"] = json.dumps(result, ensure_ascii=False)
            call["source"] = "backend_time_correction"
        return []

    reminder_id = _create_reminder(state, content, trigger)
    return [
        {
            "id": "det_reminder",
            "name": "set_reminder",
            "arguments": {"content": content, "time": trigger_iso},
            "result": json.dumps(
                {
                    "ok": True,
                    "id": reminder_id,
                    "content": content,
                    "trigger_at": trigger_iso,
                    "scheduled": False,
                },
                ensure_ascii=False,
            ),
            "source": "backend_time_fallback",
        }
    ]


def _parse_reminder_request(message: str) -> tuple[str, Any] | None:
    if not REMINDER_INTENT_RE.search(message):
        return None
    try:
        trigger = parse_reminder_time(message)
    except (TypeError, ValueError):
        return None
    content = _extract_reminder_content(message)
    if not content:
        content = "提醒事项"
    return content, trigger


def _extract_reminder_content(message: str) -> str:
    text = message.strip()
    text = re.sub(r".*?提醒我", "", text)
    text = re.sub(r".*?提醒一下", "", text)
    text = re.sub(r".*?叫我", "", text)
    text = re.sub(r"(今天|明天|后天|大后天)?(上午|下午|晚上|早上|中午|凌晨)?[零〇一二两三四五六七八九十0-9]{1,3}\s*点\s*(半|[零〇一二两三四五六七八九十0-9]{1,3}分?)?", "", text)
    text = re.sub(r"[0-9]{1,2}\s*[:：]\s*[0-9]{1,2}", "", text)
    text = text.strip(" ，,。.!！?？")
    return text


def _create_reminder(state: AppState, content: str, trigger: Any) -> int:
    engine = _require(state.engine)
    with session_scope(engine) as s:
        row = Reminder(content=content, trigger_at=trigger, status="pending")
        s.add(row)
        s.flush()
        reminder_id = row.id
    if state.scheduler is not None and state.scheduler.running:
        state.scheduler.schedule_reminder(reminder_id, trigger)
    return reminder_id


def _update_reminder_time(state: AppState, reminder_id: int, trigger: Any) -> None:
    engine = _require(state.engine)
    with session_scope(engine) as s:
        row = s.query(Reminder).filter(Reminder.id == reminder_id).one_or_none()
        if row is not None:
            row.trigger_at = trigger
            row.status = "pending"
    if state.scheduler is not None and state.scheduler.running:
        state.scheduler.schedule_reminder(reminder_id, trigger)


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _last_user_message_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if str(msg.get("role") or "").lower() != "user":
            continue
        text = _message_content_text(msg.get("content"))
        if text:
            return text
    return ""


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {None, "text", "input_text"}:
                    value = item.get("text") or item.get("content")
                    if value:
                        parts.append(str(value))
            elif item:
                parts.append(str(item))
        return "\n".join(p.strip() for p in parts if p.strip()).strip()
    if content is None:
        return ""
    return str(content).strip()


def _extract_weather_city(message: str) -> str:
    text = message.strip()
    text = re.sub(r"[?？!！。,.，]", "", text)
    text = re.sub(r"(请问|帮我|查询|查一下|看一下|现在|今天|当前|的)", "", text)
    text = re.sub(r"(天气怎么样|天气如何|天气|气温|温度|会下雨吗|下雨吗|weather)", "", text, flags=re.I)
    return text.strip() or "北京"


def _append_tool_summary(text: str, tool_calls: list[dict[str, Any]]) -> str:
    reminder_call = next((c for c in tool_calls if c.get("name") == "set_reminder"), None)
    if reminder_call is not None:
        data = _safe_json(reminder_call.get("result"))
        trigger = data.get("trigger_at")
        content = data.get("content")
        if trigger and content:
            summary = f"已设置提醒:{trigger} 提醒你{content}。"
            return f"{text}\n\n{summary}" if text and summary not in text else text or summary

    weather_call = next((c for c in tool_calls if c.get("name") == "weather"), None)
    if weather_call is None:
        return text
    try:
        data = json.loads(weather_call.get("result", "{}"))
    except json.JSONDecodeError:
        return text
    if not isinstance(data, dict):
        return text
    summary = (
        f"{data.get('city', '')}当前{data.get('condition', '天气信息可用')}, "
        f"{data.get('temperature_c', '-')}°C, 湿度 {data.get('humidity', '-')}%, "
        f"风速 {data.get('wind_kph', '-')} km/h。"
    )
    if text and summary in text:
        return text
    return f"{text}\n\n{summary}" if text else summary


CONVERSATIONAL_PENDING_SOURCES = {"nudge", "dynamic", "greeting"}


def _integrate_pending_messages(
    engine,
    *,
    session_id: str,
    items: list[dict[str, Any]],
    add_to_short_term: Callable[[LLMMessage], None] | None = None,
) -> list[dict[str, Any]]:
    """把主动触达转成 assistant 对话消息,同时保留提醒类 system 语义。"""
    integrated: list[dict[str, Any]] = []
    for item in items:
        enriched = dict(item)
        source = str(enriched.get("source") or "")
        content = str(enriched.get("content") or "")
        if source in CONVERSATIONAL_PENDING_SOURCES and content:
            meta = {
                "source": "pending_message",
                "pending_source": source,
                "pending_message_id": enriched.get("id"),
                "scheduled_at": enriched.get("scheduled_at"),
                "pending_meta": enriched.get("meta") or {},
            }
            message_id = append_message(
                engine,
                session_id=session_id,
                role=Role.ASSISTANT.value,
                content=content,
                meta=meta,
            )
            if add_to_short_term is not None:
                add_to_short_term(LLMMessage(role=Role.ASSISTANT, content=content))
            enriched["role"] = Role.ASSISTANT.value
            enriched["message_id"] = message_id
        else:
            enriched["role"] = "system"
        integrated.append(enriched)
    return integrated


def _read_jsonl_tail(directory: Path, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not directory.exists():
        return rows
    for path in sorted(directory.glob("*.jsonl"), reverse=True):
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(rows) >= limit:
                return rows
    return rows


def _require(value: Any) -> Any:
    if value is None:
        raise RuntimeError("application state is not initialized")
    return value
