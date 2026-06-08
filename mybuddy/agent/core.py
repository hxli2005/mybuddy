"""Agent 核心 ReAct 循环。

一轮 `run(user_input)` 的过程:

  1. (M5)情绪检测 + 更新窗口,连续 negative 触发离线 nudge
  2. (M6)按用户输入 + 情绪状态匹配 skill 候选,拼进 system prompt
  3. 追加 user message 到短期记忆
  4. 检索长期记忆 + 画像上下文(同时拿到本轮相关 claim_ids),合并情绪提示,注入 system prompt
  5. while step < max_steps:
       a. build_messages + build_system_prompt
       b. provider.generate(messages, tools, system=...)
       c. 把 assistant 产出(文本 + 工具调用)写入短期记忆 + 轨迹
       d. 有工具调用 → 逐个 execute,产出的 tool 消息回写记忆 → 继续下一步
       e. 没有工具调用 → finish
  6. record_turn + maybe_extract(每 N 轮触发 LLM 事实抽取)
  7. commit 轨迹(含 emotion / skill meta)
  8. (M6)若 tool_calls ≥3 且 finish=="stop",异步让 SkillCurator 复盘是否抽象新 skill

短期记忆由 MemoryManager 管理;长期记忆和画像上下文通过 system prompt 注入。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from mybuddy._time import utcnow
from mybuddy.config import Config
from mybuddy.emotion import build_emotional_support, support_system_hint
from mybuddy.learning import Trajectory, TrajectoryLogger, TrajectoryStep
from mybuddy.llm import BaseLLMProvider, Message, Role
from mybuddy.memory import MemoryManager
from mybuddy.storage import append_message, enqueue
from mybuddy.tools import ToolRegistry

from .context import build_messages, build_system_prompt
from .search import (
    build_search_context,
    build_unavailable_search_context,
    classify_search_need,
    extract_search_sources,
    may_use_interest_topics,
    search_result_count,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.emotion import EmotionDetector, EmotionResult, EmotionTracker
    from mybuddy.learning import SkillCurator, SkillRegistry
    from mybuddy.scheduler import MyBuddyScheduler

logger = logging.getLogger(__name__)


# 触发 curator 复盘的门槛:本轮 tool_calls 累计次数
CURATOR_TOOL_CALL_THRESHOLD = 3


class AgentResult:
    text: str
    steps: int
    finish_reason: str
    trajectory: Trajectory
    tool_calls: list[dict[str, Any]]
    emotion: EmotionResult | None
    emotional_support: dict[str, Any] | None
    related_claim_ids: list[int]
    triggered_skills: list[str]
    search_sources: list[dict[str, str]]

    def __init__(
        self,
        text: str,
        steps: int,
        finish_reason: str,
        trajectory: Trajectory,
        tool_calls: list[dict[str, Any]] | None = None,
        emotion: EmotionResult | None = None,
        emotional_support: dict[str, Any] | None = None,
        related_claim_ids: list[int] | None = None,
        triggered_skills: list[str] | None = None,
        search_sources: list[dict[str, str]] | None = None,
    ) -> None:
        self.text = text
        self.steps = steps
        self.finish_reason = finish_reason
        self.trajectory = trajectory
        self.tool_calls = tool_calls or []
        self.emotion = emotion
        self.emotional_support = emotional_support
        self.related_claim_ids = related_claim_ids or []
        self.triggered_skills = triggered_skills or []
        self.search_sources = search_sources or []


class Agent:
    def __init__(
        self,
        *,
        provider: BaseLLMProvider,
        config: Config,
        registry: ToolRegistry,
        memory: MemoryManager,
        trajectory_logger: TrajectoryLogger,
        session_id: str | None = None,
        max_steps: int = 6,
        emotion_detector: EmotionDetector | None = None,
        emotion_tracker: EmotionTracker | None = None,
        engine: Engine | None = None,
        scheduler: MyBuddyScheduler | None = None,
        skill_registry: SkillRegistry | None = None,
        skill_curator: SkillCurator | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._registry = registry
        self._memory = memory
        self._logger = trajectory_logger
        self._session_id = session_id or uuid.uuid4().hex[:8]
        self._max_steps = max_steps
        # 情绪系统可选 —— None 时整个情绪链路跳过(测试友好)
        self._emotion_detector = emotion_detector
        self._emotion_tracker = emotion_tracker
        # engine 用于触发 nudge 入队;None 时跳过 nudge
        self._engine = engine
        self._scheduler = scheduler
        # M6:skill 匹配 + 自动抽象(均可选)
        self._skill_registry = skill_registry
        self._skill_curator = skill_curator
        self._warned_search_registry_fallback = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def history(self) -> list[Message]:
        return self._memory.get_recent_messages()

    async def run(self, user_input: str) -> AgentResult:
        # 1. 情绪检测(可选)
        emotion = await self._detect_emotion(user_input)
        emotional_support = build_emotional_support(user_input, emotion)
        consecutive_negative = self._has_consecutive_negative()
        scene_hint = support_system_hint(
            emotional_support,
            consecutive_negative=consecutive_negative,
        )
        all_tool_calls: list[dict[str, Any]] = []

        # 2. 检索记忆上下文(text + 本轮相关 claim_ids)
        memory_context, related_claim_ids = self._memory.build_context_section(user_input)

        # 3. Skill 匹配(可选)
        skill_hint, triggered_skills = self._match_skills(user_input, emotion)

        # 4. 时效事实预检索:不要把新闻/最新信息完全交给模型自由决定是否调用工具
        search_context, search_call, search_sources = await self._prefetch_web_search(user_input)
        if search_call is not None:
            all_tool_calls.append(search_call)

        # 5. 合并 system prompt:人设 + 记忆 + 情绪 + skill + 外部资料
        extras = "\n\n".join(
            x
            for x in (
                memory_context,
                search_context,
                scene_hint,
                skill_hint,
            )
            if x
        )
        system = build_system_prompt(self._config.persona, extras)
        traj = self._logger.start(
            session_id=self._session_id,
            system=system,
            user_input=user_input,
        )
        if emotion is not None:
            traj.meta["emotion"] = emotion.to_dict()
        traj.meta["emotional_support"] = emotional_support.to_dict()
        if triggered_skills:
            traj.meta["triggered_skills"] = list(triggered_skills)
        if related_claim_ids:
            traj.meta["related_claim_ids"] = list(related_claim_ids)
        if search_call is not None:
            traj.meta["search"] = {
                "level": search_call.get("decision_level"),
                "reason": search_call.get("decision_reason"),
                "topic": search_call.get("decision_topic"),
                "result_count": search_call.get("result_count", 0),
            }

        # 6. 用户消息入记
        user_message_id = self._persist_chat_message(
            Role.USER,
            user_input,
            meta={
                "turn_id": traj.turn_id,
                "source": "chat",
            },
        )
        self._memory.add_message(Message(role=Role.USER, content=user_input))
        self._schedule_silence_followup(user_input, user_message_id)

        final_text = ""
        finish_reason = "stop"

        for _step in range(self._max_steps):
            messages = build_messages(self._memory.get_recent_messages())
            resp = await self._provider.generate(
                messages,
                tools=self._registry.specs() or None,
                system=system,
            )

            tool_call_records = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in resp.tool_calls
            ]
            all_tool_calls.extend(tool_call_records)

            step_record = TrajectoryStep(
                assistant_text=resp.text,
                tool_calls=tool_call_records,
            )

            if resp.text or resp.tool_calls:
                self._persist_chat_message(
                    Role.ASSISTANT,
                    resp.text or "",
                    meta={
                        "turn_id": traj.turn_id,
                        "step_index": _step,
                        "finish_reason": resp.finish_reason,
                        "tool_calls": [tc.model_dump() for tc in resp.tool_calls],
                        "usage": resp.usage,
                        **(
                            {"search_sources": search_sources}
                            if search_sources and not resp.tool_calls
                            else {}
                        ),
                    },
                )
                self._memory.add_message(
                    Message(
                        role=Role.ASSISTANT,
                        content=resp.text or "",
                        tool_calls=list(resp.tool_calls),
                    )
                )

            if not resp.tool_calls:
                final_text = resp.text
                finish_reason = resp.finish_reason or "stop"
                traj.steps.append(step_record)
                break

            for tc in resp.tool_calls:
                result_text = await self._registry.execute(tc.name, tc.arguments)
                self._persist_chat_message(
                    Role.TOOL,
                    result_text,
                    meta={
                        "turn_id": traj.turn_id,
                        "step_index": _step,
                        "tool_call_id": tc.id,
                        "tool_name": tc.name,
                        "arguments": tc.arguments,
                    },
                )
                for record in tool_call_records:
                    if record["id"] == tc.id:
                        record["result"] = result_text
                        break
                step_record.tool_results.append(
                    {"tool_call_id": tc.id, "name": tc.name, "result": result_text}
                )
                self._memory.add_message(
                    Message(
                        role=Role.TOOL,
                        content=result_text,
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                )

            traj.steps.append(step_record)
        else:
            finish_reason = "max_steps"
            final_text = final_text or "(已达到最大推理步数)"
            self._persist_chat_message(
                Role.ASSISTANT,
                final_text,
                meta={
                    "turn_id": traj.turn_id,
                    "source": "agent_guard",
                    "finish_reason": finish_reason,
                },
            )

        traj.final_response = final_text
        traj.finish_reason = finish_reason
        self._logger.commit(traj)

        # 5. 记录本轮对话;达到阈值则后台抽取——快照同步取,LLM 调用与写入走后台 task,
        #    不阻塞用户可见回复(每 N 轮原本要在回复后多等一次 small-model 往返)。
        self._memory.record_turn(user_input, final_text, turn_id=traj.turn_id)
        batch = self._memory.take_extract_batch()
        if batch is not None:
            self._spawn_extract(*batch)

        # 6. M6:满足"复杂任务"条件时,异步让 curator 复盘是否抽象新 skill
        self._maybe_trigger_curator(traj, all_tool_calls, finish_reason)

        return AgentResult(
            text=final_text,
            steps=len(traj.steps),
            finish_reason=finish_reason,
            trajectory=traj,
            tool_calls=all_tool_calls,
            emotion=emotion,
            emotional_support=emotional_support.to_dict(),
            related_claim_ids=related_claim_ids,
            triggered_skills=triggered_skills,
            search_sources=search_sources,
        )

    # -----------------------------------------------------------------
    # 情绪辅助
    # -----------------------------------------------------------------

    def _persist_chat_message(
        self,
        role: Role,
        content: str,
        *,
        meta: dict[str, Any] | None = None,
    ) -> int | None:
        """写入 SQLite 原始聊天主日志。"""
        if self._engine is None:
            return None
        try:
            return append_message(
                self._engine,
                session_id=self._session_id,
                role=role.value,
                content=content,
                meta=meta,
            )
        except Exception:
            logger.exception("persist chat message failed")
            return None

    async def _detect_emotion(self, user_input: str) -> EmotionResult | None:
        """跑情绪分类,写入 tracker,必要时触发离线 nudge。"""
        if self._emotion_detector is None:
            return None
        result = await self._emotion_detector.classify(
            user_input,
            context=self._memory.get_recent_messages(),
        )
        if self._emotion_tracker is not None:
            self._emotion_tracker.add(result)
            if (
                self._emotion_tracker.is_consecutive_negative(n=2)
                and self._engine is not None
            ):
                self._enqueue_empathy_nudge(user_input)
        return result

    def _has_consecutive_negative(self) -> bool:
        return (
            self._emotion_tracker is not None
            and self._emotion_tracker.is_consecutive_negative(n=2)
        )

    def _schedule_silence_followup(
        self,
        user_input: str,
        user_message_id: int | None,
    ) -> None:
        scheduler = self._scheduler
        if scheduler is None or not scheduler.running:
            return
        if user_message_id is None:
            return
        settings = self._config.scheduler
        if not settings.enabled or not settings.silence_followup_enabled:
            return
        delay = max(5, int(settings.silence_followup_delay_minutes))
        try:
            scheduler.schedule_silence_followup(
                session_id=self._session_id,
                user_message_id=user_message_id,
                user_text=user_input,
                run_at=datetime.now() + timedelta(minutes=delay),
            )
        except Exception:
            logger.exception("schedule silence followup failed")

    async def _prefetch_web_search(
        self,
        user_input: str,
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, str]]]:
        # 兴趣话题收集会读取全部长期记忆卡片,代价较高。只有当消息可能用到它时才收集,
        # 避免每个普通寒暄轮次都做一次全量归档扫描(判定结果完全等价)。
        interest_topics = (
            self._interest_topics() if may_use_interest_topics(user_input) else []
        )
        decision = classify_search_need(user_input, interest_topics=interest_topics)
        if decision.level == "none":
            return "", None, []

        registry = self._registry_for_web_search()
        if registry is None:
            if decision.level == "must":
                return build_unavailable_search_context(decision, query=user_input), None, []
            return "", None, []

        args = {
            "query": user_input,
            "max_results": self._config.tools.web_search_max_results,
        }
        result_text = await registry.execute("web_search", args)
        context = build_search_context(
            decision,
            query=user_input,
            result_text=result_text,
            max_items=self._config.tools.web_search_max_results,
        )
        sources = extract_search_sources(
            result_text,
            max_items=self._config.tools.web_search_max_results,
        )
        return context, {
            "id": "prefetch_web_search",
            "name": "web_search",
            "arguments": args,
            "result": result_text,
            "source": "backend_search_prefetch",
            "decision_level": decision.level,
            "decision_reason": decision.reason,
            "decision_topic": decision.topic,
            "result_count": search_result_count(result_text),
        }, sources

    def _registry_for_web_search(self) -> ToolRegistry | None:
        if self._registry.get("web_search") is not None:
            return self._registry

        default_registry = ToolRegistry.default()
        if default_registry is not self._registry and default_registry.get("web_search") is not None:
            if not self._warned_search_registry_fallback:
                logger.warning(
                    "web_search missing from agent registry; falling back to default registry"
                )
                self._warned_search_registry_fallback = True
            return default_registry
        return None

    def _interest_topics(self) -> list[str]:
        getter = getattr(self._memory, "interest_topics", None)
        if getter is None:
            return []
        try:
            return list(getter())
        except Exception:
            logger.exception("collect interest topics failed")
            return []

    # -----------------------------------------------------------------
    # M6 Skill 匹配 & curator 触发
    # -----------------------------------------------------------------

    def _match_skills(
        self, user_input: str, emotion: EmotionResult | None
    ) -> tuple[str, list[str]]:
        """返回 (注入 system prompt 的文本段落, 命中的 skill name 列表)。"""
        if self._skill_registry is None:
            return "", []
        emotion_label = emotion.label if emotion is not None else None
        consecutive = (
            self._emotion_tracker is not None
            and self._emotion_tracker.is_consecutive_negative(n=2)
        )
        hits = self._skill_registry.match(
            user_input,
            emotion_label=emotion_label,
            consecutive_negative=consecutive,
        )
        if not hits:
            return "", []

        lines = ["## 可能有用的做法建议(仅参考,不要明示在用模板)"]
        for s in hits:
            steps_text = ";".join(s.steps) if s.steps else "(无具体步骤)"
            if len(steps_text) > 400:
                steps_text = steps_text[:400] + "…"
            lines.append(f"- 【{s.name}】{steps_text}")
        return "\n".join(lines), [s.name for s in hits]

    def _maybe_trigger_curator(
        self,
        traj: Trajectory,
        all_tool_calls: list[dict[str, Any]],
        finish_reason: str,
    ) -> None:
        """满足门槛时挂一个异步 task 让 curator 复盘。永不抛异常。"""
        if self._skill_curator is None:
            return
        if finish_reason != "stop":
            return
        eligible_tool_calls = [
            call for call in all_tool_calls
            if call.get("source") != "backend_search_prefetch"
        ]
        if len(eligible_tool_calls) < CURATOR_TOOL_CALL_THRESHOLD:
            return
        try:
            asyncio.create_task(self._skill_curator.maybe_curate(traj))
        except RuntimeError:
            # 没有 running loop(例如在同步测试里 await agent.run 之外调用过),忽略
            logger.debug("no running loop for curator task, skipping")

    def _spawn_extract(self, turns: list[str], turn_ids: list[str]) -> None:
        """后台执行事实抽取,不阻塞回复;无 running loop 时静默跳过。"""
        try:
            asyncio.create_task(self._memory.run_extract(turns, turn_ids))
        except RuntimeError:
            logger.debug("no running loop for extract task, skipping")

    def _enqueue_empathy_nudge(self, user_input: str) -> None:
        """连续 2 轮 negative → 延迟 30min 的主动问候,写 pending_messages。"""
        if self._engine is None:
            return
        persona = self._config.persona.name
        reason = _brief_reason(user_input)
        content = (
            f"{persona}刚才还记着你说的「{reason}」。"
            "不是来催你,只是想确认一下:那股压力现在还压着吗?不想回也没事。"
        )
        scheduled_at = utcnow() + timedelta(minutes=30)
        enqueue(
            self._engine,
            source="nudge",
            content=content,
            meta={
                "origin": "emotion_consecutive_negative",
                "contact_reason": f"连续两轮负面情绪,最近一句:{reason}",
            },
            scheduled_at=scheduled_at,
        )


def _brief_reason(text: str, limit: int = 28) -> str:
    clean = " ".join((text or "").strip().split())
    if not clean:
        return "刚才那件事"
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "..."
