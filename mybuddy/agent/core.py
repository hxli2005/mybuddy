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
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from mybuddy._time import utcnow
from mybuddy.config import Config
from mybuddy.emotion import build_emotional_support, support_system_hint
from mybuddy.learning import Trajectory, TrajectoryLogger, TrajectoryStep
from mybuddy.llm import BaseLLMProvider, Message, Role
from mybuddy.memory import MemoryManager
from mybuddy.storage import enqueue
from mybuddy.tools import ToolRegistry

from .context import build_messages, build_system_prompt

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.emotion import EmotionDetector, EmotionResult, EmotionTracker
    from mybuddy.learning import SkillCurator, SkillRegistry

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
        # M6:skill 匹配 + 自动抽象(均可选)
        self._skill_registry = skill_registry
        self._skill_curator = skill_curator

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def history(self) -> list[Message]:
        return self._memory.get_recent_messages()

    async def run(self, user_input: str) -> AgentResult:
        # 1. 情绪检测(可选)
        emotion = await self._detect_emotion(user_input)
        emotion_hint = self._emotion_system_hint(emotion)
        emotional_support = build_emotional_support(user_input, emotion)
        support_hint = support_system_hint(emotional_support)

        # 2. 检索记忆上下文(text + 本轮相关 claim_ids)
        memory_context, related_claim_ids = self._memory.build_context_section(user_input)

        # 3. Skill 匹配(可选)
        skill_hint, triggered_skills = self._match_skills(user_input, emotion)

        # 4. 合并 system prompt:人设 + 记忆 + 情绪 + skill
        extras = "\n\n".join(
            x for x in (memory_context, emotion_hint, support_hint, skill_hint) if x
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

        # 4. 用户消息入记
        self._memory.add_message(Message(role=Role.USER, content=user_input))

        final_text = ""
        finish_reason = "stop"
        all_tool_calls: list[dict[str, Any]] = []

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

        traj.final_response = final_text
        traj.finish_reason = finish_reason
        self._logger.commit(traj)

        # 5. 记录本轮对话,触发事实抽取(如果达到阈值)
        self._memory.record_turn(user_input, final_text, turn_id=traj.turn_id)
        await self._memory.maybe_extract()

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
        )

    # -----------------------------------------------------------------
    # 情绪辅助
    # -----------------------------------------------------------------

    async def _detect_emotion(self, user_input: str) -> EmotionResult | None:
        """跑情绪分类,写入 tracker,必要时触发离线 nudge。"""
        if self._emotion_detector is None:
            return None
        result = await self._emotion_detector.classify(user_input)
        if self._emotion_tracker is not None:
            self._emotion_tracker.add(result)
            if (
                self._emotion_tracker.is_consecutive_negative(n=2)
                and self._engine is not None
            ):
                self._enqueue_empathy_nudge()
        return result

    def _emotion_system_hint(self, emotion: EmotionResult | None) -> str:
        """若情绪负面,往 system prompt 追加"先共情再给方案"的指引。"""
        if emotion is None or not emotion.is_negative:
            return ""
        consecutive = (
            self._emotion_tracker is not None
            and self._emotion_tracker.is_consecutive_negative(n=2)
        )
        extra = (
            "注意最近两轮用户情绪都偏低,不要急着给方案或做事,"
            "先用 1-2 句话共情,让 TA 感到被看见。"
            if consecutive
            else "用户这句话情绪偏低,回复时先共情再给内容,语气放软。"
        )
        return f"## 情绪提示\n{extra}"

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
        if len(all_tool_calls) < CURATOR_TOOL_CALL_THRESHOLD:
            return
        try:
            asyncio.create_task(self._skill_curator.maybe_curate(traj))
        except RuntimeError:
            # 没有 running loop(例如在同步测试里 await agent.run 之外调用过),忽略
            logger.debug("no running loop for curator task, skipping")

    def _enqueue_empathy_nudge(self) -> None:
        """连续 2 轮 negative → 延迟 30min 的主动问候,写 pending_messages。"""
        if self._engine is None:
            return
        persona = self._config.persona.name
        content = (
            f"{persona}有点惦记你~刚才聊到的事,现在感觉怎么样了?"
            "不想说也没事,我一直都在。"
        )
        scheduled_at = utcnow() + timedelta(minutes=30)
        enqueue(
            self._engine,
            source="nudge",
            content=content,
            meta={"origin": "emotion_consecutive_negative"},
            scheduled_at=scheduled_at,
        )
