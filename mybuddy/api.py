"""FastAPI 后端:小布引擎的桥接面。

单用户后端:复用现有 Agent、Memory、FeedbackBus 装配,只暴露对话与
vpet 桥端点(status / chat / vpet-* / feedback)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

try:
    # fastapi 是可选依赖(api extra):FastAPI 需要在运行时解析路由签名注解,装了就用真符号;
    # 未装时走 stdlib `mybuddy web` 路径,注解因 __future__ annotations 永不求值,占位即可。
    from fastapi import Request
except ModuleNotFoundError:  # pragma: no cover - 未装 api extra 的环境
    Request = Any  # type: ignore[misc, assignment]

from mybuddy._time import (
    configure_time_offset,
    time_offset_minutes,
)
from mybuddy._time import (
    localnow as _localnow,
)
from mybuddy._time import (
    utcnow as _utcnow,
)
from mybuddy.agent import Agent
from mybuddy.body import (
    FOOD_CATALOG,
    PhysioBusyError,
    PhysioEngine,
    PhysioSnapshot,
    enqueue_crossed_murmurs,
)
from mybuddy.config import Config, ensure_dirs, load_config
from mybuddy.emotion import EmotionDetector, EmotionTracker
from mybuddy.integrations.vpet import (
    BRIDGE_VERSION,
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
from mybuddy.memory import LongTermMemory, MemoryManager
from mybuddy.scheduler import MyBuddyScheduler
from mybuddy.storage import (
    Message as StoredMessage,
)
from mybuddy.storage import (
    PendingMessage,
    PhysioDaily,
    VPetEvent,
    append_message,
    count_vpet_escalations_today,
    drain_pending,
    get_message_content,
    init_db,
    latest_assistant_message_id,
    list_undelivered,
    mark_vpet_event_result,
    record_vpet_event,
    session_scope,
    update_vpet_event_context,
)
from mybuddy.tools import (
    ToolRegistry,
    set_context,
    setup_memory_tool,
    setup_skill_tool,
    use_context,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.llm import BaseLLMProvider


logger = logging.getLogger(__name__)
WORK_STOP_TEXT = "忙完啦。先松口气,剩下的待会儿再说。"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class VPetEventRequest(BaseModel):
    event: str
    count: int = 1
    body_state: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    want_reply: bool = False
    client_event_id: str | None = None


class VPetDrainRequest(BaseModel):
    digest: bool = False


class FeedbackRequest(BaseModel):
    label: str
    turn_id: str | None = None


@dataclass
class AppState:
    config_path: str
    max_steps: int = 6
    enable_scheduler: bool = True
    cfg: Config | None = None
    engine: Engine | None = None
    provider: BaseLLMProvider | None = None
    ltm: LongTermMemory | None = None
    skill_registry: SkillRegistry | None = None
    scheduler: MyBuddyScheduler | None = None
    agent: Agent | None = None
    physio: PhysioEngine | None = None
    feedback_bus: FeedbackBus | None = None
    last_turn_id: str | None = None
    last_triggered_skills: list[str] = field(default_factory=list)
    _body_state_warning_emitted: bool = False
    agent_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def startup(self) -> None:
        cfg = load_config(self.config_path)
        configure_time_offset(acceptance_mode=cfg.vpet.acceptance_mode)
        ensure_dirs(cfg)
        engine = init_db(cfg.paths.db_file)
        physio = PhysioEngine(engine, cfg.physio) if cfg.physio.enabled else None
        ltm = LongTermMemory(
            persist_dir=cfg.paths.chroma_dir,
            embedding_model=cfg.memory.embedding_model,
        )
        ltm.normalize_metadata()
        provider = make_provider(cfg.llm) if cfg.llm.api_key else None
        logger = TrajectoryLogger(cfg.paths.trajectories_dir)
        skill_registry = SkillRegistry.load_all(cfg.paths.skills_dir)

        scheduler: MyBuddyScheduler | None = None
        if cfg.scheduler.enabled and self.enable_scheduler:
            scheduler = MyBuddyScheduler(cfg)
            scheduler.start()
            _restore_cowork_sessions(scheduler, engine)
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
        self.skill_registry = skill_registry
        self.scheduler = scheduler
        self.agent = agent
        self.physio = physio
        self.feedback_bus = feedback_bus
        self._repair_pending_shared_moments()
        self._flush_touch_shared_moments()

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

    async def chat_payload(
        self,
        message: str,
        *,
        source: str = "chat",
        enable_tools: bool = True,
        meta: dict[str, Any] | None = None,
        physio: dict[str, Any] | None = None,
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
                physio_payload = physio
                if physio_payload is None and self.physio is not None:
                    snapshot = self.physio.apply_chat()
                    self._handle_murmurs(snapshot)
                    physio_payload = snapshot.to_dict()
                # 睡眠窗内用户仍可主动聊天,但主动队列必须留到醒后,不能借聊天
                # 路径偷跑并被标成已交付。
                allow_pending = not bool(
                    isinstance(physio_payload, dict) and physio_payload.get("sleeping")
                )
                pending_before = (
                    _integrate_pending_messages(
                        engine,
                        session_id=self.agent.session_id,
                        items=drain_pending(engine),
                        add_to_short_term=self.agent._memory.add_message,
                    )
                    if allow_pending
                    else []
                )
                result = await self.agent.run(
                    message.strip(),
                    source=source,
                    enable_tools=enable_tools,
                    meta=meta,
                    physio=physio_payload,
                    # v2 起请求 body_state 只落兼容遥测,不进入提示词。
                    body_state=None,
                )
                result_text = result.text
                tool_calls = list(result.tool_calls)
                pending_after = (
                    _integrate_pending_messages(
                        engine,
                        session_id=self.agent.session_id,
                        items=drain_pending(engine),
                        add_to_short_term=self.agent._memory.add_message,
                    )
                    if allow_pending
                    else []
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
        self._flush_touch_shared_moments()
        self._warn_legacy_body_state(body_state)
        normalized_body = normalize_body_state(body_state)
        body_state_used = False
        event_log_id: int | None = None
        flags = _server_flags(self.cfg)
        engine = self.engine
        if engine is not None:
            _record_flags_change(engine, flags)
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
                body_state=None,
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
        physio_payload = self.physio.snapshot().to_dict() if self.physio is not None else None
        self._record_physio_conflicts(
            physio=physio_payload,
            text=str(chat.get("text") or ""),
            source_event=event,
            turn_id=turn_id,
            message_id=message_id,
            client_flags=client_flags,
        )
        return chat_to_vpet_payload(chat, source_event=event)

    def vpet_pending_payload(
        self,
        *,
        drain: bool = False,
        digest: bool = False,
        client_flags: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        engine = _require(self.engine)
        flags = _server_flags(self.cfg)
        if not drain:
            payload = pending_to_vpet_payload(list_undelivered(engine), drained=False)
        elif digest:
            payload = self._vpet_digest_pending_payload(client_flags=client_flags)
        else:
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
            payload = pending_to_vpet_payload(items, drained=True)
        payload["server_flags"] = flags
        return payload

    async def vpet_event_payload(
        self,
        req: VPetEventRequest,
        *,
        client_flags: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._flush_touch_shared_moments()
        engine = _require(self.engine)
        event = req.event.strip()
        if event not in {
            "touch_head",
            "touch_body",
            "feed",
            "user_back",
            "work_start",
            "work_stop",
            "presence_heartbeat",
            "notice_shown",
        }:
            raise RuntimeError("unsupported vpet event")
        count = _clamp_int(
            req.count,
            low=1,
            high=20 if event == "presence_heartbeat" else 50,
        )
        body_state = normalize_body_state(req.body_state)
        self._warn_legacy_body_state(req.body_state)
        context = dict(req.context) if isinstance(req.context, dict) else {}
        if event in {"work_start", "work_stop"} and not str(context.get("session_id") or "").strip():
            raise RuntimeError("work event requires context.session_id")
        if event == "notice_shown" and (
            not str(context.get("source") or "").strip()
            or not str(context.get("shown_at") or "").strip()
        ):
            raise RuntimeError("notice_shown requires context.source and context.shown_at")
        if event == "notice_shown":
            context["client_shown_at"] = context["shown_at"]
            context["shown_at"] = _localnow().isoformat(timespec="seconds")
        flags = _server_flags(self.cfg)
        _record_flags_change(engine, flags)
        body_state_used = False
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
            return _vpet_event_replay_payload(engine, row)

        physio_payload: dict[str, Any] | None = None
        if self.physio is not None and event in {"touch_head", "touch_body", "feed"}:
            if event == "feed":
                requested_item = str(context.get("item") or "water")
                resolved_item = requested_item if requested_item in FOOD_CATALOG else "water"
                if requested_item not in FOOD_CATALOG:
                    logger.warning("unknown vpet food item %r; fallback to water", requested_item)
                snapshot = self.physio.apply_feed(resolved_item)
                self._handle_murmurs(snapshot)
                physio_payload = snapshot.to_dict()
                context_update = {
                    "item": resolved_item,
                    "requested_item": requested_item,
                    "physio_delta": physio_payload.get("delta", {}),
                    "physio": physio_payload,
                }
            else:
                snapshot = self.physio.apply_touch(count=count)
                self._handle_murmurs(snapshot)
                physio_payload = snapshot.to_dict()
                context_update = {
                    "physio_delta": physio_payload.get("delta", {}),
                    "physio": physio_payload,
                }
            updated = update_vpet_event_context(engine, row["id"], context_update)
            if updated is not None:
                row = updated
            if event == "feed":
                self._record_feed_shared_moment(row)

        if event == "work_start":
            session_id = str(context["session_id"])
            due_at = _utcnow() + timedelta(minutes=50)
            if self.scheduler is not None and self.scheduler.running:
                self.scheduler.schedule_cowork_break(session_id=session_id, run_at=due_at)
            updated = update_vpet_event_context(
                engine,
                row["id"],
                {"session_id": session_id, "break_due_at": due_at.isoformat(timespec="seconds")},
            )
            if updated is not None:
                row = updated

        if event == "work_stop":
            session_id = str(context["session_id"])
            duration = _cowork_duration_minutes(engine, session_id, stop_event_id=row["id"])
            update_vpet_event_context(
                engine,
                row["id"],
                {"session_id": session_id, "duration_minutes": duration or 0},
            )
            if duration is None:
                mark_vpet_event_result(
                    engine,
                    row["id"],
                    replied=False,
                    gate_reason="unknown_work_session",
                )
                return {
                    "ok": True,
                    "bridge": BRIDGE_VERSION,
                    "replied": False,
                    "gate_reason": "unknown_work_session",
                    "event_log_id": row["id"],
                    "duration_minutes": 0,
                }
            if self.scheduler is not None:
                self.scheduler.cancel_cowork_break(session_id)
            budget_engine = self.physio or PhysioEngine(engine, _require(self.cfg).physio)
            can_speak = budget_engine.claim_work_stop_speech()
            mark_vpet_event_result(engine, row["id"], replied=can_speak)
            if not can_speak:
                return {
                    "ok": True,
                    "bridge": BRIDGE_VERSION,
                    "replied": False,
                    "gate_reason": "work_stop_daily_limit",
                    "event_log_id": row["id"],
                    "duration_minutes": duration,
                }
            payload = chat_to_vpet_payload(
                {
                    "text": WORK_STOP_TEXT,
                    "pending_messages": [],
                    "finish_reason": "stop",
                },
                source_event="work_stop",
            )
            payload.update(
                {
                    "replied": True,
                    "event_log_id": row["id"],
                    "duration_minutes": duration,
                }
            )
            return payload

        if not req.want_reply or event in {
            "feed",
            "work_start",
            "presence_heartbeat",
            "notice_shown",
        }:
            mark_vpet_event_result(engine, row["id"], replied=False)
            return {
                "ok": True,
                "bridge": BRIDGE_VERSION,
                "replied": False,
                "gate_reason": None,
                "event_log_id": row["id"],
                **({"physio": physio_payload} if physio_payload is not None else {}),
            }

        cfg = _require(self.cfg)
        if event == "user_back":
            if not flags["physical_proactive"]:
                return self._mark_vpet_event_gate(row["id"], "physical_proactive_disabled")
        else:
            if not _touch_escalation_eligible(engine, row, context):
                return self._mark_vpet_event_gate(row["id"], "touch_not_eligible")
            if not flags["touch_escalation"]:
                return self._mark_vpet_event_gate(row["id"], "escalation_disabled")
            if count_vpet_escalations_today(engine) >= max(
                0, cfg.vpet.touch_escalation_daily_limit
            ):
                return self._mark_vpet_event_gate(row["id"], "budget_exceeded")
        if self.agent_lock.locked():
            return self._mark_vpet_event_gate(row["id"], "agent_busy")
        if self.agent is None:
            raise RuntimeError("LLM api_key 未配置,无法对话")

        await self.agent_lock.acquire()
        try:
            prompt_count = (
                _clamp_int(context.get("window_count"), low=1, high=50)
                if event in {"touch_head", "touch_body"}
                else count
            )
            synthetic_input = _vpet_event_prompt(event, count=prompt_count, context=context)
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
                    physio=physio_payload,
                    body_state=None,
                )
        finally:
            self.agent_lock.release()

        message_id = latest_assistant_message_id(engine, turn_id=result.trajectory.turn_id)
        reply_text = (
            str(result.text or "").strip()
            if event == "user_back"
            else _short_vpet_reaction(result.text)
        )
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
                "pending_messages": [],
            },
            source_event=event,
        )
        payload["replied"] = True
        payload["event_log_id"] = row["id"]
        self._record_physio_conflicts(
            physio=physio_payload,
            text=reply_text,
            source_event=event,
            turn_id=result.trajectory.turn_id,
            message_id=message_id,
            client_flags=client_flags,
        )
        return payload

    def vpet_state_payload(self) -> dict[str, Any]:
        cfg = _require(self.cfg)
        engine = _require(self.engine)
        snapshot_obj = self.physio.snapshot() if self.physio is not None else None
        if snapshot_obj is not None:
            self._handle_murmurs(snapshot_obj)
        snapshot = snapshot_obj.to_dict() if snapshot_obj is not None else None
        flags = _server_flags(cfg)
        _record_flags_change(engine, flags)
        return {
            "ok": True,
            "bridge": BRIDGE_VERSION,
            "server_time": _localnow().isoformat(timespec="seconds"),
            "time_offset_minutes": time_offset_minutes() if cfg.vpet.acceptance_mode else 0,
            "physio": snapshot,
            "idle_hint": _vpet_idle_hint(engine, snapshot, cfg),
            "warmth": _vpet_warmth(engine),
            "server_flags": flags,
            "day_index": _vpet_day_index(engine),
        }

    def _handle_murmurs(self, snapshot: PhysioSnapshot) -> None:
        if self.physio is None or self.engine is None:
            return
        enqueue_crossed_murmurs(
            self.engine,
            self.physio,
            snapshot,
            server_flags=_server_flags(self.cfg),
            day_index=_vpet_day_index(self.engine),
        )

    def _warn_legacy_body_state(self, value: dict[str, Any] | None) -> None:
        if value is None or self._body_state_warning_emitted:
            return
        self._body_state_warning_emitted = True
        logger.warning("VPet body_state 已弃用并被忽略；请改用 GET /api/vpet/state")

    def _record_feed_shared_moment(self, event_row: dict[str, Any]) -> None:
        if self.ltm is None or self.engine is None:
            return
        context = event_row.get("context") if isinstance(event_row.get("context"), dict) else {}
        local_date = str(context.get("local_date") or _localnow().date().isoformat())
        with session_scope(self.engine) as session:
            daily = session.get(PhysioDaily, local_date)
            item_ids = _safe_json_list(daily.feed_items_json if daily is not None else "[]")
        names = [str(FOOD_CATALOG[item]["name"]) for item in item_ids if item in FOOD_CATALOG]
        if not names:
            return
        day_word = "今天" if local_date == _localnow().date().isoformat() else "那天"
        content = f"{day_word}你请我吃了{'和'.join(names)}"
        uid = f"vpet_feed_{local_date}"
        try:
            updated = self.ltm.update(
                uid,
                content=content,
                metadata={
                    "type": "shared_moment",
                    "source": "vpet_feed",
                    "item": item_ids,
                    "date": local_date,
                    "importance": 0.8,
                },
            )
            if updated is None:
                self.ltm.add(
                    content,
                    mem_type="shared_moment",
                    uid=uid,
                    extra_meta={
                        "source": "vpet_feed",
                        "item": item_ids,
                        "date": local_date,
                        "importance": 0.8,
                    },
                )
            update_vpet_event_context(self.engine, event_row["id"], {"memory_pending": False})
        except Exception:  # noqa: BLE001 —— 身体事件不能被档案写入失败回滚
            logger.exception("feed shared_moment 写入失败;event=%s", event_row["id"])
            update_vpet_event_context(self.engine, event_row["id"], {"memory_pending": True})

    def _repair_pending_shared_moments(self) -> None:
        """启动时重放已提交事件里失败的 feed 档案写入。"""
        if self.ltm is None or self.engine is None:
            return
        with session_scope(self.engine) as session:
            rows = (
                session.query(VPetEvent)
                .filter(VPetEvent.event == "feed")
                .order_by(VPetEvent.id.asc())
                .all()
            )
            pending = [
                {"id": row.id, "context": context}
                for row in rows
                if (context := _safe_json(row.context_json)).get("memory_pending") is True
            ]
        for event_row in pending:
            self._record_feed_shared_moment(event_row)

    def _flush_touch_shared_moments(self) -> None:
        """跨天首次交互时把前一日高频触摸聚合成共同记忆。"""
        if self.ltm is None or self.engine is None:
            return
        today = _localnow().date().isoformat()
        with session_scope(self.engine) as session:
            rows = (
                session.query(PhysioDaily)
                .filter(PhysioDaily.local_date < today)
                .filter(PhysioDaily.touch_count >= 5)
                .filter(PhysioDaily.touch_memory_written.is_(False))
                .order_by(PhysioDaily.local_date.asc())
                .all()
            )
            pending = [(row.local_date, row.touch_count) for row in rows]
        for local_date, count in pending:
            uid = f"vpet_touch_{local_date}"
            try:
                self.ltm.add(
                    f"那天被你 rua 了好多下,一共 {count} 次",
                    mem_type="shared_moment",
                    uid=uid,
                    extra_meta={
                        "source": "vpet_touch",
                        "date": local_date,
                        "touch_count": count,
                        "importance": 0.7,
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception("touch shared_moment 写入失败;date=%s", local_date)
                continue
            with session_scope(self.engine) as session:
                row = session.get(PhysioDaily, local_date)
                if row is not None:
                    row.touch_memory_written = True

    def _mark_vpet_event_gate(self, event_id: int, gate_reason: str) -> dict[str, Any]:
        engine = _require(self.engine)
        mark_vpet_event_result(engine, event_id, gate_reason=gate_reason)
        return {
            "ok": True,
            "bridge": BRIDGE_VERSION,
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
                if pm.source == "body_murmur":
                    pm.delivered_at = now
                    discarded_count += 1
                    telemetry.append(
                        {
                            "event": "pending_discarded",
                            "reason": "digest_body_murmur",
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

    def _record_physio_conflicts(
        self,
        *,
        physio: dict[str, Any] | None,
        text: str,
        source_event: str,
        turn_id: str | None,
        message_id: int | None,
        client_flags: dict[str, Any] | None = None,
    ) -> None:
        if not physio or not text or self.engine is None:
            return
        reasons = _physio_conflicts(text, physio)
        if not reasons:
            return
        record_vpet_event(
            self.engine,
            event="body_state_conflict",
            count=len(reasons),
            context={
                "source_event": source_event,
                "turn_id": turn_id,
                "message_id": message_id,
                "reasons": reasons,
                "physio": physio,
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
            "bridge": BRIDGE_VERSION,
            "protocol": {"state": True, "cowork": True},
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


def create_app(config_path: str = "config.yaml", max_steps: int = 6):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import JSONResponse
    except ModuleNotFoundError as e:  # pragma: no cover - 只有未安装 api extra 时触发
        raise RuntimeError("缺少 API 依赖,请运行: uv sync --extra api") from e

    state = AppState(config_path=config_path, max_steps=max_steps)

    @asynccontextmanager
    async def _lifespan(_app):  # noqa: ANN001
        state.startup()
        try:
            yield
        finally:
            state.shutdown()

    app = FastAPI(title="MyBuddy API", lifespan=_lifespan)
    app.state.mybuddy = state

    def _vpet_error(code: str, message: str, status_code: int = 400):
        return JSONResponse(
            {"ok": False, "error": {"code": code, "message": message}},
            status_code=status_code,
        )

    @app.exception_handler(PhysioBusyError)
    async def _physio_busy(_request: Request, exception: PhysioBusyError):
        return JSONResponse(
            {
                "ok": False,
                "error": {"code": "physio_busy", "message": str(exception)},
            },
            status_code=503,
        )

    @app.middleware("http")
    async def _bridge_auth_middleware(request: Request, call_next):  # noqa: ANN001
        token = _bridge_token(state)
        if token and _requires_bridge_auth(request.url.path):
            if request.headers.get("X-MyBuddy-Token", "") != token:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/")
    async def index() -> dict[str, Any]:
        return {"ok": True, "service": "mybuddy"}

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return state.status_payload()

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> dict[str, Any]:
        if state.agent is None:
            raise HTTPException(status_code=400, detail="LLM api_key 未配置,无法对话")
        return await state.chat_payload(req.message)

    @app.get("/api/vpet/status")
    async def vpet_status() -> dict[str, Any]:
        return state.vpet_status_payload()

    @app.get("/api/vpet/state")
    async def vpet_state() -> dict[str, Any]:
        return state.vpet_state_payload()

    @app.post("/api/vpet/event")
    async def vpet_event(req: VPetEventRequest, request: Request):  # noqa: ANN202
        try:
            return await state.vpet_event_payload(
                req,
                client_flags=_parse_client_flags(
                    request.headers.get("X-MyBuddy-Client-Flags")
                ),
            )
        except PhysioBusyError:
            raise
        except RuntimeError as e:
            return _vpet_error("invalid_request", str(e))

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

    @app.post("/api/feedback")
    async def feedback(req: FeedbackRequest) -> dict[str, Any]:
        try:
            return state.feedback_payload(req.label, req.turn_id)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return app


def _server_flags(cfg: Config | None) -> dict[str, bool]:
    if cfg is None:
        return {
            "physio_injection": False,
            "touch_escalation": False,
            "physical_proactive": False,
        }
    flags = {
        "physio_injection": bool(cfg.vpet.physio_injection),
        "touch_escalation": bool(cfg.vpet.touch_escalation),
        "physical_proactive": bool(cfg.vpet.physical_proactive),
    }
    today = _localnow().date()
    if date(2026, 8, 2) <= today <= date(2026, 8, 17):
        flags["physio_injection"] = True
        if today <= date(2026, 8, 3):
            flags["touch_escalation"] = True
            flags["physical_proactive"] = True
        else:
            flags["touch_escalation"] = (today - date(2026, 8, 4)).days % 2 == 0
            flags["physical_proactive"] = today <= date(2026, 8, 10)
    return flags


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
    today = _localnow().date()
    with session_scope(engine) as s:
        first = s.query(VPetEvent.created_at).order_by(VPetEvent.created_at.asc()).first()
    if first is None or first[0] is None:
        return 1
    first_local = first[0].replace(tzinfo=UTC).astimezone(_localnow().tzinfo).date()
    return max((today - first_local).days + 1, 1)


def _vpet_idle_hint(engine: Engine, physio: dict[str, Any] | None, cfg: Config) -> str:
    if physio and physio.get("sleeping"):
        return "sleep"
    if _cowork_open_sessions(engine):
        return "work"
    levels = physio.get("levels", {}) if physio else {}
    if levels.get("tired"):
        return "nap"
    if levels.get("hungry"):
        return "gaze"
    if levels.get("bright"):
        return "stretch"
    if levels.get("low"):
        return "gaze"
    with session_scope(engine) as session:
        latest_user = (
            session.query(StoredMessage.content)
            .filter(StoredMessage.role == "user")
            .order_by(StoredMessage.id.desc())
            .first()
        )
    life = cfg.persona.character_life
    signals = " ".join(
        [
            life.today_status,
            life.current_mood,
            life.recent_self_event,
            str(latest_user[0] if latest_user else ""),
        ]
    )
    if _contains_any(signals, ["读", "书", "文献", "阅读"]):
        return "read"
    if _contains_any(signals, ["写", "便签", "论文", "报告", "代码", "整理"]):
        return "write"
    if _contains_any(signals, ["窗", "发呆", "走神", "想起"]):
        return "gaze"
    return "read" if _vpet_day_index(engine) % 2 else "write"


def _vpet_warmth(engine: Engine) -> float:
    cutoff = _utcnow() - timedelta(days=7)
    with session_scope(engine) as s:
        interactions = (
            s.query(VPetEvent)
            .filter(VPetEvent.created_at >= cutoff)
            .filter(
                VPetEvent.event.in_(
                    ["chat", "user_chat", "touch_head", "touch_body", "feed", "work_stop"]
                )
            )
            .count()
        )
    daily_average = interactions / 7.0
    return round(max(0.0, min(1.0, daily_average / 5.0)), 3)


def _record_flags_change(engine: Engine, flags: dict[str, bool]) -> None:
    today = _localnow().date()
    if not (date(2026, 8, 2) <= today <= date(2026, 8, 17)):
        return
    with session_scope(engine) as session:
        latest = session.query(VPetEvent).order_by(VPetEvent.id.desc()).first()
        previous = _safe_json(latest.server_flags_json) if latest is not None else {}
    if previous == flags:
        return
    record_vpet_event(
        engine,
        event="flags_changed",
        context={"previous": previous, "current": flags},
        server_flags=flags,
        day_index=_vpet_day_index(engine),
    )


def _vpet_event_prompt(event: str, *, count: int, context: dict[str, Any]) -> str:
    if event == "user_back":
        return (
            "用户刚回到电脑前。请像熟人一样用一句很短的自然问候接住这次回场，"
            "优先引用 system 里 living_state.recent_self_event 对应的近期真实话题；"
            "不要编造记忆，不要连续提问，不要展开建议。"
        )
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


def _touch_escalation_eligible(
    engine: Engine,
    event_row: dict[str, Any],
    client_context: dict[str, Any],
) -> bool:
    """服务端复核“当天首次或 30 秒窗口第 5 次”，防止重启重复首次升格。"""
    if int(client_context.get("window_count") or 0) >= 5:
        return True
    row_context = event_row.get("context") if isinstance(event_row.get("context"), dict) else {}
    local_date = str(row_context.get("local_date") or "")
    with session_scope(engine) as session:
        previous = (
            session.query(VPetEvent.context_json)
            .filter(VPetEvent.event.in_(["touch_head", "touch_body"]))
            .filter(VPetEvent.id < int(event_row["id"]))
            .all()
        )
    return not any(
        str(_safe_json(context_json).get("local_date") or "") == local_date
        for (context_json,) in previous
    )


def _digest_text(sources: list[str], discarded_count: int) -> str:
    if any(source in sources for source in ("nudge", "dynamic")):
        return "你不在的时候我攒了一件事:一次想叫你歇会儿。"
    if discarded_count:
        return "你不在的时候有些过期问候,我已经替你收掉了。"
    return ""


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


def _physio_conflicts(text: str, physio: dict[str, Any]) -> list[str]:
    clean = str(text or "")
    levels = physio.get("levels") if isinstance(physio.get("levels"), dict) else {}
    reasons: list[str] = []
    if levels.get("hungry") and _contains_any(clean, ["不饿", "吃饱", "饱了", "刚吃", "吃撑"]):
        reasons.append("hunger_low_but_reply_satiated")
    if levels.get("tired") and _contains_any(clean, ["不困", "精力满满", "元气满满", "精神得很"]):
        reasons.append("energy_low_but_reply_energetic")
    if levels.get("low") and _contains_any(clean, ["心情很好", "超开心", "开心得很"]):
        reasons.append("mood_low_but_reply_happy")
    if (physio.get("sleeping") or physio.get("woken")) and _contains_any(
        clean, ["一点都不困", "刚睡醒精神很好", "精神满满"]
    ):
        reasons.append("sleeping_or_woken_but_reply_alert")
    return reasons


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(item in text for item in needles)


def _vpet_event_replay_payload(engine: Engine, row: dict[str, Any]) -> dict[str, Any]:
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    if row.get("replied"):
        text = get_message_content(engine, row.get("message_id"))
        if row.get("event") == "work_stop":
            text = text or WORK_STOP_TEXT
        else:
            text = _short_vpet_reaction(text)
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
        if "duration_minutes" in context:
            payload["duration_minutes"] = context["duration_minutes"]
        return payload
    payload = {
        "ok": True,
        "bridge": BRIDGE_VERSION,
        "replied": False,
        "gate_reason": row.get("gate_reason"),
        "event_log_id": row.get("id"),
    }
    if isinstance(context.get("physio"), dict):
        payload["physio"] = context["physio"]
    if "duration_minutes" in context:
        payload["duration_minutes"] = context["duration_minutes"]
    return payload


def _restore_cowork_sessions(scheduler: MyBuddyScheduler, engine: Engine) -> None:
    """从事件账本恢复未闭合共处会话的 50 分钟 job。"""
    open_sessions = _cowork_open_sessions(engine)
    now = _utcnow()
    for session_id, started_at in open_sessions.items():
        due_at = started_at + timedelta(minutes=50)
        scheduler.schedule_cowork_break(
            session_id=session_id,
            run_at=max(due_at, now + timedelta(seconds=1)),
        )


def _cowork_open_sessions(
    engine: Engine,
    *,
    before_event_id: int | None = None,
) -> dict[str, Any]:
    with session_scope(engine) as session:
        query = (
            session.query(VPetEvent)
            .filter(VPetEvent.event.in_(["work_start", "work_stop"]))
        )
        if before_event_id is not None:
            query = query.filter(VPetEvent.id < before_event_id)
        rows = query.order_by(VPetEvent.id.asc()).all()
    open_sessions: dict[str, Any] = {}
    for row in rows:
        context = _safe_json(row.context_json)
        session_id = str(context.get("session_id") or "")
        if not session_id:
            continue
        if row.event == "work_start":
            open_sessions[session_id] = row.created_at
        else:
            open_sessions.pop(session_id, None)
    return open_sessions


def _cowork_duration_minutes(
    engine: Engine,
    session_id: str,
    *,
    stop_event_id: int,
) -> int | None:
    with session_scope(engine) as session:
        stop = session.get(VPetEvent, stop_event_id)
    if stop is None:
        return None
    start = _cowork_open_sessions(engine, before_event_id=stop_event_id).get(session_id)
    if start is None:
        return None
    return max(round((stop.created_at - start).total_seconds() / 60.0), 0)


def _safe_json_list(value: str | None) -> list[str]:
    try:
        loaded = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in loaded] if isinstance(loaded, list) else []


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


def _require(value: Any) -> Any:
    if value is None:
        raise RuntimeError("application state is not initialized")
    return value
