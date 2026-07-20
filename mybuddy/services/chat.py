"""多入口共享的聊天服务。

该层把核心 Agent 变成按 user_id 调用的服务。QQ/Web/App 只需要提供用户上下文
和文本消息,不直接触碰 Agent、Memory、Profile 或工具运行上下文。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
from mybuddy.llm import Role, make_provider
from mybuddy.memory import LongTermMemory, MemoryManager, UserProfile
from mybuddy.safety import CrisisDetector, InputModerator, OutputModerator
from mybuddy.scheduler import MyBuddyScheduler
from mybuddy.storage import (
    Reminder,
    UserRecord,
    append_message,
    drain_pending,
    ensure_local_user,
    get_user,
    increment_usage,
    init_db,
    session_scope,
    usage_count_today,
)
from mybuddy.therapy import CbtGuide
from mybuddy.tools import ToolRegistry, setup_memory_tool, setup_skill_tool, use_context

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.llm import BaseLLMProvider


logger = logging.getLogger(__name__)

ProviderFactory = Callable[[Config], "BaseLLMProvider"]


@dataclass(frozen=True)
class RequestContext:
    user_id: int
    source: str = "web"
    external_id: str | None = None


@dataclass
class ChatResponse:
    text: str
    turn_id: str | None = None
    steps: int = 0
    finish_reason: str = "stop"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    emotion: dict[str, Any] | None = None
    emotional_support: dict[str, Any] | None = None
    triggered_skills: list[str] = field(default_factory=list)
    search_sources: list[dict[str, str]] = field(default_factory=list)
    pending_messages: list[dict[str, Any]] = field(default_factory=list)
    cbt_prompt: dict[str, str] | None = None
    crisis_alert: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "turn_id": self.turn_id,
            "steps": self.steps,
            "finish_reason": self.finish_reason,
            "tool_calls": self.tool_calls,
            "emotion": self.emotion,
            "emotional_support": self.emotional_support,
            "triggered_skills": self.triggered_skills,
            "search_sources": self.search_sources,
            "pending_messages": self.pending_messages,
            "cbt_prompt": self.cbt_prompt,
            "crisis_alert": self.crisis_alert,
        }


@dataclass
class UserRuntime:
    user_id: int
    cfg: Config
    engine: Engine
    provider: BaseLLMProvider
    ltm: LongTermMemory
    profile: UserProfile
    skill_registry: SkillRegistry
    feedback_bus: FeedbackBus
    agent: Agent
    scheduler: MyBuddyScheduler | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_turn_id: str | None = None
    last_triggered_skills: list[str] = field(default_factory=list)


class ChatService:
    """按用户隔离的聊天服务。

    当前实现采用"主库保存用户/渠道元数据 + 每用户独立运行库和记忆目录"的方式。
    这比一次性改造所有业务表为 user_id 更稳,也让 QQ 先作为独立挂件接入。
    """

    def __init__(
        self,
        *,
        config_path: str = "config.yaml",
        max_steps: int = 6,
        provider: BaseLLMProvider | None = None,
        provider_factory: ProviderFactory | None = None,
        enable_emotion: bool = True,
    ) -> None:
        self.config_path = config_path
        self.max_steps = max_steps
        self._provider_override = provider
        self._provider_factory = provider_factory
        self._enable_emotion = enable_emotion
        self.cfg: Config | None = None
        self.engine: Engine | None = None
        self._runtimes: dict[int, UserRuntime] = {}

    def startup(self) -> None:
        cfg = load_config(self.config_path)
        ensure_dirs(cfg)
        self.cfg = cfg
        self.engine = init_db(cfg.paths.db_file)

    def shutdown(self) -> None:
        for runtime in self._runtimes.values():
            _shutdown_scheduler(runtime.scheduler)
        self._runtimes.clear()

    @property
    def started(self) -> bool:
        return self.cfg is not None and self.engine is not None

    def ensure_started(self) -> None:
        if not self.started:
            self.startup()

    def local_user(self) -> UserRecord:
        self.ensure_started()
        return ensure_local_user(_require_engine(self.engine))

    async def chat(self, ctx: RequestContext, message: str) -> ChatResponse:
        clean = message.strip()
        if not clean:
            raise RuntimeError("message is required")
        self.ensure_started()
        user = get_user(_require_engine(self.engine), ctx.user_id)
        if user is None:
            raise RuntimeError(f"用户不存在:user_id={ctx.user_id}")
        if not user.is_active:
            raise RuntimeError("用户已被禁用")
        guard = self._quota_guard(user, ctx)
        if guard is not None:
            return guard

        runtime = self._runtime_for(user, refresh_if_stale=True)
        async with runtime.lock:
            # 锁内权威复检。配额检查若只在锁外、而累加又发生在 agent.run 之后,同一用户的
            # 并发请求会一起读到旧计数并同时放行,越过 daily_message_limit。runtime.lock 按
            # 用户串行,锁内"复检 + 累加"保证这次读改写是原子的。
            guard = self._quota_guard(user, ctx)
            if guard is not None:
                return guard
            # 调度器必须在运行中的事件循环内启动(APScheduler AsyncIOScheduler 约束),
            # 因此放到这里惰性启动,而不是 startup()/构造期。不启动它,set_reminder 只会
            # 静默写库不注册 job(QQ/多用户提醒永不触发)。
            self._ensure_scheduler_started(runtime)
            with use_context(
                engine=runtime.engine,
                config=runtime.cfg,
                scheduler=runtime.scheduler,
                provider=runtime.provider,
                long_term=runtime.ltm,
                skill_registry=runtime.skill_registry,
            ):
                pending_before = _integrate_pending_messages(
                    runtime.engine,
                    session_id=runtime.agent.session_id,
                    items=drain_pending(runtime.engine),
                    add_to_short_term=runtime.agent._memory.add_message,
                    user_id=user.id,
                )
                result = await runtime.agent.run(clean)
                # 情绪持久化 + 无感化评估评分(与 api.py 单用户路径对齐)
                if result.emotion is not None:
                    from mybuddy.mood import MoodTracker

                    MoodTracker(runtime.engine).record_from_emotion(user.id, result.emotion)
                await _try_assessment_scoring(runtime, user.id, clean)
                pending_after = _integrate_pending_messages(
                    runtime.engine,
                    session_id=runtime.agent.session_id,
                    items=drain_pending(runtime.engine),
                    add_to_short_term=runtime.agent._memory.add_message,
                    user_id=user.id,
                )
            increment_usage(_require_engine(self.engine), user_id=user.id, source=ctx.source)
            runtime.last_turn_id = result.trajectory.turn_id
            runtime.last_triggered_skills = list(result.triggered_skills)
            return ChatResponse(
                text=result.text,
                turn_id=result.trajectory.turn_id,
                steps=result.steps,
                finish_reason=result.finish_reason,
                tool_calls=list(result.tool_calls),
                emotion=result.emotion.to_dict() if result.emotion else None,
                emotional_support=result.emotional_support,
                triggered_skills=list(result.triggered_skills),
                search_sources=list(result.search_sources),
                pending_messages=pending_before + pending_after,
                cbt_prompt=result.cbt_prompt,
                crisis_alert=result.crisis_alert,
            )

    def get_user_record(self, user_id: int) -> UserRecord | None:
        self.ensure_started()
        return get_user(_require_engine(self.engine), user_id)

    def _quota_guard(self, user: UserRecord, ctx: RequestContext) -> ChatResponse | None:
        """返回额度已用尽的提示响应;未超额或不限额时返回 None。"""
        if user.daily_message_limit <= 0:
            return None
        used = usage_count_today(_require_engine(self.engine), user_id=user.id, source=ctx.source)
        if used < user.daily_message_limit:
            return None
        return ChatResponse(
            text=f"今天的测试额度已经用完了({used}/{user.daily_message_limit})。明天再继续。",
            finish_reason="quota_exceeded",
        )

    def feedback(self, ctx: RequestContext, label: str, turn_id: str | None = None) -> dict[str, Any]:
        self.ensure_started()
        user = get_user(_require_engine(self.engine), ctx.user_id)
        if user is None:
            raise RuntimeError(f"用户不存在:user_id={ctx.user_id}")
        existing = self._runtimes.get(user.id)
        tid = turn_id or (existing.last_turn_id if existing is not None else None)
        if not tid:
            # 没有可反馈轮次时直接返回。不要为了读一个 None 而构造完整 runtime——构造会初始化
            # provider/记忆/Agent,在 api_key 未配置时还会抛出无关的 api_key 错误,被 /good
            # 当成回复抛给用户。
            raise RuntimeError("没有可反馈的对话轮次")
        runtime = existing if existing is not None else self._runtime_for(user, refresh_if_stale=False)
        clean_label = label.strip()
        runtime.feedback_bus.publish(
            FeedbackEvent(
                turn_id=tid,
                label=clean_label,
                meta={"triggered_skills": list(runtime.last_triggered_skills)},
            )
        )
        return {"ok": True, "turn_id": tid, "label": clean_label}

    def quota_payload(self, ctx: RequestContext) -> dict[str, Any]:
        self.ensure_started()
        user = get_user(_require_engine(self.engine), ctx.user_id)
        if user is None:
            raise RuntimeError(f"用户不存在:user_id={ctx.user_id}")
        used = usage_count_today(_require_engine(self.engine), user_id=user.id, source=ctx.source)
        return {
            "user_id": user.id,
            "source": ctx.source,
            "used": used,
            "limit": user.daily_message_limit,
            "remaining": max(0, user.daily_message_limit - used)
            if user.daily_message_limit > 0
            else None,
        }

    def reset_runtime(self, user_id: int) -> bool:
        runtime = self._runtimes.pop(user_id, None)
        if runtime is None:
            return False
        _shutdown_scheduler(runtime.scheduler)
        return True

    def _ensure_scheduler_started(self, runtime: UserRuntime) -> None:
        """首次对话时在运行中的事件循环内启动该用户的调度器并回灌待触发提醒。

        APScheduler 的 AsyncIOScheduler.start() 需要 running loop,故惰性启动放在
        async chat() 内而非 startup()/构造期。已运行则直接返回(幂等)。
        """
        scheduler = runtime.scheduler
        if scheduler is None or scheduler.running:
            return
        scheduler.start()
        _restore_reminders(scheduler, runtime.engine)

    def _runtime_for(self, user: UserRecord, *, refresh_if_stale: bool = True) -> UserRuntime:
        base_cfg = _require_cfg(self.cfg)
        existing = self._runtimes.get(user.id)
        if existing is not None:
            return existing

        user_cfg = _user_config(base_cfg, user.id)
        ensure_dirs(user_cfg)
        engine = init_db(user_cfg.paths.db_file)
        provider = self._make_provider(user_cfg)
        ltm = LongTermMemory(
            persist_dir=user_cfg.paths.chroma_dir,
            embedding_model=user_cfg.memory.embedding_model,
        )
        ltm.normalize_metadata()
        logger = TrajectoryLogger(user_cfg.paths.trajectories_dir)
        profile = UserProfile(engine, ltm)
        skill_registry = SkillRegistry.load_all(user_cfg.paths.skills_dir)
        memory = MemoryManager(
            engine=engine,
            config=user_cfg,
            ltm=ltm,
            provider=provider,
            session_id=f"user-{user.id}",
        )
        # 重启后从持久化的 messages 表回灌最近对话,避免即时上下文断档。
        memory.rehydrate_short_term()
        feedback_bus = FeedbackBus()
        feedback_bus.subscribe(make_trajectory_subscriber(logger))
        feedback_bus.subscribe(make_skill_subscriber(skill_registry))
        emotion_detector = (
            EmotionDetector(provider, user_cfg.llm.small_model) if self._enable_emotion else None
        )
        emotion_tracker = EmotionTracker(window=5) if self._enable_emotion else None
        setup_memory_tool(ltm)
        setup_skill_tool(skill_registry)
        # 每用户独立调度器:jobstore 与提醒投递都落在该用户自己的库,和 api.py/cli.py 的
        # 单用户装配对称。这里只构造,真正 start() 推迟到 chat() 的事件循环内。
        scheduler = MyBuddyScheduler(user_cfg) if user_cfg.scheduler.enabled else None
        agent = Agent(
            provider=provider,
            config=user_cfg,
            registry=ToolRegistry.default(),
            memory=memory,
            trajectory_logger=logger,
            session_id=f"user-{user.id}",
            max_steps=self.max_steps,
            emotion_detector=emotion_detector,
            emotion_tracker=emotion_tracker,
            engine=engine,
            scheduler=scheduler,
            skill_registry=skill_registry,
            skill_curator=SkillCurator(provider, skill_registry, model=user_cfg.llm.small_model),
            user_id=user.id,
            crisis_detector=CrisisDetector(provider, user_cfg.llm.small_model),
            input_moderator=InputModerator(provider, user_cfg.llm.small_model),
            output_moderator=OutputModerator(),
            cbt_guide=CbtGuide(),
        )
        runtime = UserRuntime(
            user_id=user.id,
            cfg=user_cfg,
            engine=engine,
            provider=provider,
            ltm=ltm,
            profile=profile,
            skill_registry=skill_registry,
            feedback_bus=feedback_bus,
            scheduler=scheduler,
            agent=agent,
        )
        self._runtimes[user.id] = runtime
        return runtime

    def _make_provider(self, cfg: Config) -> BaseLLMProvider:
        if self._provider_override is not None:
            return self._provider_override
        if self._provider_factory is not None:
            return self._provider_factory(cfg)
        if not cfg.llm.api_key:
            raise RuntimeError("LLM api_key 未配置,无法对话")
        return make_provider(cfg.llm)


CONVERSATIONAL_PENDING_SOURCES = {"nudge", "dynamic", "greeting"}


async def _try_assessment_scoring(runtime: UserRuntime, user_id: int, user_message: str) -> None:
    """无感化评估:检查是否有待评分维度,尝试自动评分(失败静默)。"""
    try:
        from mybuddy.assessment import AssessmentScorer, ConversationalAssessmentTracker

        tracker = ConversationalAssessmentTracker(runtime.engine, user_id)
        tracker.ensure_dimensions()
        asked = tracker.get_asked_dimensions()
        if not asked:
            return
        scorer = AssessmentScorer(runtime.provider, runtime.cfg.llm.small_model)
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
        logger.exception("assessment scoring failed")


def _integrate_pending_messages(
    engine: Engine,
    *,
    session_id: str,
    items: list[dict[str, Any]],
    add_to_short_term: Callable[[LLMMessage], None] | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    integrated: list[dict[str, Any]] = []
    for item in items:
        enriched = dict(item)
        source = str(enriched.get("source") or "")
        content = str(enriched.get("content") or "")
        if source in CONVERSATIONAL_PENDING_SOURCES and content:
            message_id = append_message(
                engine,
                session_id=session_id,
                role=Role.ASSISTANT.value,
                content=content,
                meta={
                    "source": "pending_message",
                    "pending_source": source,
                    "pending_message_id": enriched.get("id"),
                    "scheduled_at": enriched.get("scheduled_at"),
                    "pending_meta": enriched.get("meta") or {},
                },
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


def _user_config(cfg: Config, user_id: int) -> Config:
    user_cfg = cfg.model_copy(deep=True)
    base = Path(cfg.paths.data_dir) / "users" / str(user_id)
    user_cfg.paths.data_dir = str(base)
    user_cfg.paths.db_file = str(base / "mybuddy.db")
    user_cfg.paths.chroma_dir = str(base / "memory")
    user_cfg.paths.skills_dir = str(base / "skills")
    user_cfg.paths.trajectories_dir = str(base / "trajectories")
    user_cfg.logging.file = str(base / "mybuddy.log")
    return user_cfg


def _restore_reminders(scheduler: MyBuddyScheduler, engine: Engine) -> None:
    """把该用户库里仍 pending 且未过期的提醒重新注册到调度器(幂等)。

    与 api.py:_restore_reminders / cli.py:_restore_reminders 同义,只是作用在每用户库上:
    重启后用户首次对话时兜底重建 job(即便 jobstore 丢过 job 也能从 reminders 表恢复)。
    """
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


def _shutdown_scheduler(scheduler: MyBuddyScheduler | None) -> None:
    """关停一个用户调度器。QQ 进程退出时事件循环可能已关闭,故吞掉关停异常只记日志。"""
    if scheduler is None:
        return
    try:
        scheduler.shutdown()
    except Exception:  # noqa: BLE001
        logger.exception("user scheduler shutdown failed")


def _require_cfg(value: Config | None) -> Config:
    if value is None:
        raise RuntimeError("chat service is not initialized")
    return value


def _require_engine(value: Engine | None) -> Engine:
    if value is None:
        raise RuntimeError("chat service is not initialized")
    return value
