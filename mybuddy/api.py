"""FastAPI 后端 + 静态前端入口。

这是演示用单用户后端:复用现有 Agent、Memory、Tools、FeedbackBus 装配,
并把 `frontend/` 里的静态页面托管出来。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

from mybuddy._time import utcnow
from mybuddy.agent import Agent
from mybuddy.config import Config, ensure_dirs, load_config
from mybuddy.emotion import EmotionDetector, EmotionTracker
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
from mybuddy.llm import Role, Transcriber, make_provider, make_transcriber
from mybuddy.memory import LongTermMemory, MemoryManager, UserProfile
from mybuddy.auth import AuthManager
from mybuddy.safety import CrisisDetector, InputModerator, OutputModerator
from mybuddy.scheduler import MyBuddyScheduler
from mybuddy.therapy import CbtGuide
from mybuddy.storage import (
    Note,
    Reminder,
    UserSummaryRecord,
    append_message,
    bind_external_account,
    create_user,
    drain_pending,
    get_user,
    init_db,
    list_messages,
    list_undelivered,
    list_user_summaries,
    session_scope,
    set_user_daily_limit,
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


class FeedbackRequest(BaseModel):
    label: str
    turn_id: str | None = None


class ProfileFieldUpdateRequest(BaseModel):
    value: str = Field(min_length=1)


class MemoryUpdateRequest(BaseModel):
    content: str | None = None
    metadata: dict[str, Any] | None = None


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


class AuthRequest(BaseModel):
    username: str
    password: str


class MoodCheckinRequest(BaseModel):
    mood_score: int = Field(ge=0, le=10)
    notes: str | None = None


class ImportedMessage(BaseModel):
    role: str
    content: str


class MessagesImportRequest(BaseModel):
    messages: list[ImportedMessage]


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
    auth: AuthManager | None = None
    transcriber: Transcriber | None = None
    last_turn_id: str | None = None
    last_triggered_skills: list[str] = field(default_factory=list)

    def startup(self) -> None:
        cfg = load_config(self.config_path)
        ensure_dirs(cfg)
        engine = init_db(cfg.paths.db_file)
        self.auth = AuthManager(engine)
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
                crisis_detector=CrisisDetector(provider, cfg.llm.small_model),
                input_moderator=InputModerator(provider, cfg.llm.small_model),
                output_moderator=OutputModerator(),
                cbt_guide=CbtGuide(),
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
        self.transcriber = make_transcriber(cfg)

    def shutdown(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown()

    # ----- 情绪追踪方法 -----

    def _mood_tracker(self) -> "MoodTracker":
        from mybuddy.mood import MoodTracker
        return MoodTracker(self.engine)

    def mood_payload(self, user_id: int, limit: int = 30) -> dict[str, Any]:
        """查询用户情绪记录。"""
        return {"records": self._mood_tracker().records(user_id, limit)}

    def mood_trends_payload(self, user_id: int, days: int = 30) -> dict[str, Any]:
        return {"daily_averages": self._mood_tracker().trends(user_id, days)}

    def mood_stats_payload(self, user_id: int) -> dict[str, Any]:
        return self._mood_tracker().stats(user_id)

    def mood_checkin_payload(self, user_id: int, mood_score: int, notes: str | None) -> dict[str, Any]:
        record_id = self._mood_tracker().checkin(user_id, mood_score, notes)
        return {"ok": True, "id": record_id}

    # ----- 语音转文字 -----

    async def transcribe_payload(self, audio_bytes: bytes) -> dict[str, Any]:
        if self.transcriber is None:
            raise RuntimeError("语音转文字未启用,请在 config.yaml 中设置 transcription.enabled: true")
        text = await self.transcriber.transcribe(audio_bytes)
        return {"text": text}

    # ----- 评估状态方法 -----

    def _assessment_tracker(self, user_id: int | None):
        """登录用户 → DB 追踪器;访客 → 进程内存追踪器。"""
        from mybuddy.assessment import ConversationalAssessmentTracker, get_guest_tracker
        if user_id is None:
            return get_guest_tracker()
        return ConversationalAssessmentTracker(_require(self.engine), user_id)

    async def _try_assessment_scoring(self, user_id: int | None, user_message: str) -> None:
        """无感化评估:检查是否有待评分维度,尝试自动评分(失败静默)。"""
        try:
            from mybuddy.assessment import AssessmentScorer

            tracker = self._assessment_tracker(user_id)
            asked = tracker.get_asked_dimensions()
            if not asked:
                return
            scorer = AssessmentScorer(self.provider, self.cfg.llm.small_model)
            pending_phq9 = [d["dimension_index"] for d in asked if d["assessment_type"] == "phq9"]
            pending_gad7 = [d["dimension_index"] for d in asked if d["assessment_type"] == "gad7"]
            result = await scorer.try_score(
                user_message,
                pending_phq9_indices=pending_phq9 or None,
                pending_gad7_indices=pending_gad7 or None,
            )
            if result:
                tracker.record_score(
                    result["assessment_type"],
                    result["dimension_index"],
                    result["score"],
                    user_message[:200],
                )
        except Exception:
            pass

    def assessment_status_payload(self, user_id: int) -> dict[str, Any]:
        from mybuddy.assessment import ConversationalAssessmentTracker
        engine = _require(self.engine)
        tracker = ConversationalAssessmentTracker(engine, user_id)
        return tracker.get_all_dimensions()

    def assessment_history_payload(self, user_id: int) -> dict[str, Any]:
        from mybuddy.assessment import ConversationalAssessmentTracker
        engine = _require(self.engine)
        tracker = ConversationalAssessmentTracker(engine, user_id)
        return {"cycles": tracker.get_history()}

    # ----- CBT 状态方法 -----

    def reset_chat_context(self) -> dict[str, Any]:
        """清空 agent 短期记忆,重置对话上下文(访客 + 登录用户通用)。"""
        if self.agent is not None:
            self.agent.reset_context()
        return {"ok": True}

    def clear_user_data_payload(self, user_id: int) -> dict[str, Any]:
        """清除用户所有数据(mood/assessment/cbt/messages)。

        同时清理未归属消息(None user_id),覆盖 cookie 修复前遗留的旧消息。
        """
        engine = _require(self.engine)
        from mybuddy.storage.models import (
            AssessmentCycle,
            AssessmentDimension,
            CbtEvent,
            ChatSession,
            Message,
            MoodRecord,
            SafetyEvent,
        )
        with session_scope(engine) as s:
            for model in [MoodRecord, SafetyEvent, AssessmentDimension, AssessmentCycle, CbtEvent, ChatSession]:
                s.query(model).filter(model.user_id == user_id).delete()
            s.query(Message).filter(Message.session_id.like(f"user-{user_id}%")).delete()
            s.query(Message).filter(Message.user_id == user_id).delete()
            s.query(Message).filter(Message.user_id.is_(None)).delete()
        return {"ok": True}

    def import_messages_payload(self, user_id: int, messages: list[dict[str, str]]) -> dict[str, Any]:
        """把访客对话导入登录账户(上限 50 条)。"""
        engine = _require(self.engine)
        imported = 0
        for item in messages[:50]:
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role not in ("user", "assistant") or not content:
                continue
            append_message(
                engine,
                session_id=f"user-{user_id}",
                role=role,
                content=content,
                meta={"source": "guest_import"},
                user_id=user_id,
            )
            imported += 1
        return {"ok": True, "imported": imported}

    def export_user_data_payload(self, user_id: int) -> dict[str, Any]:
        """导出该用户全部个人数据(JSON)。"""
        engine = _require(self.engine)
        from mybuddy.assessment import ConversationalAssessmentTracker
        from mybuddy.mood import MoodTracker
        from mybuddy.therapy import CbtTracker

        tracker = ConversationalAssessmentTracker(engine, user_id)
        return {
            "exported_at": utcnow().isoformat(),
            "user_id": user_id,
            "mood_records": MoodTracker(engine).records(user_id, limit=1000),
            "mood_stats": MoodTracker(engine).stats(user_id),
            "assessment_status": tracker.get_all_dimensions(),
            "assessment_history": tracker.get_history(limit=100),
            "cbt_events": CbtTracker(engine, user_id).get_events(limit=200),
            "messages": list_messages(engine, limit=500, user_id=user_id, user_scoped=True),
        }

    def delete_account_payload(self, user_id: int) -> dict[str, Any]:
        """删除账户:清空全部数据并移除用户行。"""
        self.clear_user_data_payload(user_id)
        engine = _require(self.engine)
        from mybuddy.storage.models import User
        with session_scope(engine) as s:
            s.query(User).filter(User.id == user_id).delete()
        return {"ok": True}

    def cbt_status_payload(self, user_id: int) -> dict[str, Any]:
        from mybuddy.therapy import CbtTracker
        tracker = CbtTracker(self.engine, user_id)
        events = tracker.get_events()
        return {"events": events}

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

    async def chat_payload(self, message: str, user_id: int | None = None) -> dict[str, Any]:
        if self.agent is None:
            raise RuntimeError("LLM api_key 未配置,无法对话")
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
                user_id=user_id,
            )
            # 无感化评估:选取待投放维度,注入system prompt。
            # 登录用户走 DB 追踪,访客走内存追踪(不持久化);危机状态下不投放。
            assessment_hint = ""
            try:
                # 首条消息时自动创建活跃会话(chat 类型)
                from mybuddy.session import SessionManager
                SessionManager(engine).get_or_create_active(user_id)
            except Exception:
                pass
            try:
                from mybuddy.safety import CrisisLevel, classify_crisis_level

                if classify_crisis_level(message) in (CrisisLevel.NONE, CrisisLevel.LOW):
                    tracker = self._assessment_tracker(user_id)
                    tracker.ensure_dimensions()
                    _assessment_hint_dim = tracker.pick_next_dimension()
                    if _assessment_hint_dim:
                        assessment_hint = (
                            f"你可以自然地关心一下用户最近的「{_assessment_hint_dim['dimension_name']}」。"
                            f"参考: {_assessment_hint_dim['hint']} 不要暴露这是在评估,像平常聊天一样自然地问。"
                        )
                        tracker.mark_asked(_assessment_hint_dim["assessment_type"], _assessment_hint_dim["dimension_index"])
            except Exception:
                pass

            # 安全门(输入审核/危机检测)、CBT 机会检测均已集中在 Agent.run 内部
            result = await self.agent.run(
                message.strip(), assessment_hint=assessment_hint, user_id=user_id
            )

            # 自动记录情绪(仅登录用户)
            if result.emotion is not None and user_id is not None:
                from mybuddy.mood import MoodTracker
                MoodTracker(engine).record_from_emotion(user_id, result.emotion)

            # 无感化评估:检查是否有待评分维度并尝试评分(访客同样参与,结果仅在内存)
            if self.provider is not None:
                await self._try_assessment_scoring(user_id, message.strip())

            result_text = result.text
            tool_calls = list(result.tool_calls)
            deterministic_tools = await _run_deterministic_demo_tools(message, tool_calls, self)
            if deterministic_tools:
                tool_calls.extend(deterministic_tools)
            result_text = _append_tool_summary(result_text, tool_calls)
            pending_after = _integrate_pending_messages(
                engine,
                session_id=self.agent.session_id,
                items=drain_pending(engine),
                add_to_short_term=self.agent._memory.add_message,
                user_id=user_id,
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
                "cbt_prompt": result.cbt_prompt,
                "crisis_alert": result.crisis_alert,
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

    def messages_payload(
        self,
        *,
        limit: int = 100,
        session_id: str | None = None,
        user_id: int | None = None,
        user_scoped: bool = False,
    ) -> dict[str, Any]:
        engine = _require(self.engine)
        return {
            "messages": list_messages(
                engine,
                limit=limit,
                session_id=session_id,
                user_id=user_id,
                user_scoped=user_scoped,
            )
        }

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
        from fastapi.responses import FileResponse
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

    @app.post("/api/chat/reset")
    async def chat_reset() -> dict[str, Any]:
        return state.reset_chat_context()

    @app.post("/api/chat")
    async def chat(req: ChatRequest, request: Request) -> dict[str, Any]:
        if state.agent is None:
            raise HTTPException(status_code=400, detail="LLM api_key 未配置,无法对话")
        from mybuddy.auth.manager import get_user_id_from_cookie
        user_id = get_user_id_from_cookie(request.headers.get("Cookie"))
        return await state.chat_payload(req.message, user_id=user_id)

    @app.get("/api/messages")
    async def messages(request: Request, limit: int = 100, session_id: str | None = None) -> dict[str, Any]:
        from mybuddy.auth.manager import get_user_id_from_cookie
        user_id = get_user_id_from_cookie(request.headers.get("Cookie"))
        if user_id is None:
            return {"messages": []}
        return state.messages_payload(
            limit=limit, session_id=session_id, user_id=user_id, user_scoped=True
        )

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

    # ----- 认证端点 -----

    @app.post("/api/auth/register")
    async def auth_register(req: AuthRequest):
        try:
            result = state.auth.register(req.username, req.password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        from fastapi.responses import JSONResponse
        resp = JSONResponse({"user_id": result["user_id"], "username": result["username"]})
        resp.set_cookie(
            key="mybuddy_session",
            value=result["cookie"],
            httponly=True,
            samesite="lax",
            max_age=30 * 24 * 3600,
        )
        return resp

    @app.post("/api/auth/login")
    async def auth_login(req: AuthRequest):
        try:
            result = state.auth.login(req.username, req.password)
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        from fastapi.responses import JSONResponse
        resp = JSONResponse({"user_id": result["user_id"], "username": result["username"]})
        resp.set_cookie(
            key="mybuddy_session",
            value=result["cookie"],
            httponly=True,
            samesite="lax",
            max_age=30 * 24 * 3600,
        )
        return resp

    @app.post("/api/auth/logout")
    async def auth_logout():
        from fastapi.responses import JSONResponse
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("mybuddy_session")
        return resp

    @app.get("/api/auth/me")
    async def auth_me(request):
        """返回当前用户信息。"""
        from mybuddy.auth.manager import get_user_id_from_cookie
        user_id = get_user_id_from_cookie(request.headers.get("Cookie"))
        if user_id is None:
            return {}
        user = state.auth.get_user(user_id)
        return user if user is not None else {}

    # ----- 安全资源端点 -----

    @app.get("/api/safety/resources")
    async def safety_resources():
        from mybuddy.safety.constants import HOTLINES
        return {"hotlines": HOTLINES}

    # ----- 情绪/评估/CBT 端点(基础占位) -----

    @app.get("/api/mood")
    async def mood(request: Request, limit: int = 30):
        user_id = _cookie_user_id(request)
        if user_id is None:
            return {"records": [], "daily_averages": []}
        return state.mood_payload(user_id, limit=limit)

    @app.get("/api/mood/trends")
    async def mood_trends(request: Request, days: int = 30):
        user_id = _cookie_user_id(request)
        if user_id is None:
            return {"daily_averages": []}
        return state.mood_trends_payload(user_id, days=days)

    @app.get("/api/mood/stats")
    async def mood_stats(request: Request):
        user_id = _cookie_user_id(request)
        if user_id is None:
            return {"total_records": 0, "streak": 0, "categories": {}}
        return state.mood_stats_payload(user_id)

    @app.post("/api/mood/checkin")
    async def mood_checkin(req: MoodCheckinRequest, request: Request):
        user_id = _cookie_user_id(request)
        if user_id is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return state.mood_checkin_payload(user_id, req.mood_score, req.notes)

    @app.get("/api/assessment/status")
    async def assessment_status(request: Request):
        user_id = _cookie_user_id(request)
        if user_id is None:
            return {"phq9": [], "gad7": []}
        return state.assessment_status_payload(user_id)

    @app.get("/api/assessment/history")
    async def assessment_history(request: Request):
        user_id = _cookie_user_id(request)
        if user_id is None:
            return {"cycles": []}
        return state.assessment_history_payload(user_id)

    @app.get("/api/cbt/status")
    async def cbt_status(request: Request):
        user_id = _cookie_user_id(request)
        if user_id is None:
            return {"events": []}
        return state.cbt_status_payload(user_id)

    @app.delete("/api/assessment/status")
    async def reset_assessment(request: Request):
        from mybuddy.auth.manager import get_user_id_from_cookie
        from mybuddy.assessment import ConversationalAssessmentTracker
        user_id = get_user_id_from_cookie(request.headers.get("Cookie"))
        if user_id is None:
            raise HTTPException(status_code=401, detail="请先登录")
        engine = state.engine
        tracker = ConversationalAssessmentTracker(engine, user_id)
        tracker.reset_cycle()
        return {"ok": True}

    @app.delete("/api/user/data")
    async def clear_user_data(request: Request):
        from mybuddy.auth.manager import get_user_id_from_cookie
        user_id = get_user_id_from_cookie(request.headers.get("Cookie"))
        if user_id is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return state.clear_user_data_payload(user_id)

    @app.post("/api/messages/import")
    async def import_messages(req: MessagesImportRequest, request: Request):
        user_id = _cookie_user_id(request)
        if user_id is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return state.import_messages_payload(
            user_id, [m.model_dump() for m in req.messages]
        )

    @app.get("/api/user/export")
    async def export_user_data(request: Request):
        user_id = _cookie_user_id(request)
        if user_id is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return state.export_user_data_payload(user_id)

    @app.delete("/api/auth/account")
    async def delete_account(request: Request):
        user_id = _cookie_user_id(request)
        if user_id is None:
            raise HTTPException(status_code=401, detail="请先登录")
        state.delete_account_payload(user_id)
        from fastapi.responses import JSONResponse
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("mybuddy_session")
        return resp

    return app


def _cookie_user_id(request) -> int | None:
    """从请求 Cookie 解析当前用户 id;访客返回 None。"""
    from mybuddy.auth.manager import get_user_id_from_cookie
    return get_user_id_from_cookie(request.headers.get("Cookie"))


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


def _restore_reminders(scheduler: MyBuddyScheduler, engine: Engine) -> None:
    from mybuddy._time import utcnow

    now = utcnow()
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
    user_id: int | None = None,
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
                user_id=user_id,
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
