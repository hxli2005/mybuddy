"""SkillCurator:Agent 跑完一轮"复杂任务"后,让 LLM 复盘是否值得抽象为 skill。

触发条件(由 Agent 决定,此类只负责"来了就复盘"):
  - tool_calls ≥ 3(设计方案约定的"复杂任务"门槛)
  - finish_reason == "stop"(真的收敛了,不是 max_steps 被截断)

复盘产出结构化 JSON:
  {
    "should_create": bool,
    "name": "情绪安抚流程",
    "triggers": ["用户情绪消极", "持续>2轮"],
    "steps": ["先共情不给方案", "询问具体发生了什么"],
    "reason": "为什么值得抽象"
  }

整个调用在 try/except 里隔离:curator 永远不应该让主对话流崩掉,
所以 Agent 端用 `asyncio.create_task` 挂到后台即可。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mybuddy.learning.skills import Skill, SkillRegistry
    from mybuddy.learning.trajectory import Trajectory
    from mybuddy.llm import BaseLLMProvider

logger = logging.getLogger(__name__)


CURATOR_SYSTEM = """你是一个 AI 助手,帮另一个 AI 小伙伴反思自己的对话经验。
给定一段对话轨迹(含用户输入、若干工具调用和最终回复),判断其中是否存在一个
**可复用的解题模板**值得抽象成 skill —— 比如"遇到 X 情况,该按 Y、Z 步骤做"。

严格输出 JSON,不要其他文本:
{
  "should_create": true/false,
  "name": "简短可读的 skill 名(8-15 字,中文)",
  "triggers": ["触发条件1", "触发条件2"],   // 2-4 条,每条 4-12 字
  "steps": ["步骤1", "步骤2", "步骤3"],       // 2-5 条,每条 10-30 字
  "reason": "为什么值得抽象(≤30 字)"
}

judgement 原则:
- 流程通用、下次遇到类似情况还能复用 → should_create=true
- 一次性的、高度特化的、或仅"查/答"单步任务 → should_create=false
- 没把握宁可 false。
"""


class SkillCurator:
    """把轨迹交给 LLM 复盘,决定是否新建 skill。"""

    def __init__(
        self,
        provider: BaseLLMProvider,
        registry: SkillRegistry,
        *,
        model: str | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._model = model

    async def maybe_curate(self, traj: Trajectory) -> Skill | None:
        """复盘轨迹 → 决定是否建 skill。永远不抛异常。"""
        try:
            return await self._curate(traj)
        except Exception:  # noqa: BLE001
            logger.exception("skill curator 失败,忽略")
            return None

    async def _curate(self, traj: Trajectory) -> Skill | None:
        summary = _summarize_trajectory(traj)
        if not summary:
            return None

        from mybuddy.llm import Message, Role

        resp = await self._provider.generate(
            messages=[Message(role=Role.USER, content=summary)],
            system=CURATOR_SYSTEM,
            temperature=0.3,
            model=self._model,
        )
        data = _parse_json_object(resp.text)
        if not isinstance(data, dict):
            return None
        if not data.get("should_create"):
            return None

        name = (data.get("name") or "").strip()
        triggers = [t for t in (data.get("triggers") or []) if isinstance(t, str) and t.strip()]
        steps = [s for s in (data.get("steps") or []) if isinstance(s, str) and s.strip()]
        if not name or not triggers or not steps:
            return None

        skill = self._registry.create(
            name=name,
            triggers=triggers,
            steps=steps,
            confidence=0.3,
        )
        logger.info("curator 新建 skill: %s (triggers=%s)", name, triggers)
        return skill


# ---------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------


def _summarize_trajectory(traj: Trajectory) -> str:
    """把轨迹序列化成紧凑的 prompt 文本。"""
    lines: list[str] = [
        f"用户输入: {traj.user_input}",
    ]
    for i, step in enumerate(traj.steps, 1):
        if step.tool_calls:
            tc_desc = ", ".join(
                f"{tc.get('name')}({_shorten_args(tc.get('arguments'))})"
                for tc in step.tool_calls
            )
            lines.append(f"步骤{i} 工具调用: {tc_desc}")
        for tr in step.tool_results:
            res = tr.get("result", "")
            if isinstance(res, str) and len(res) > 120:
                res = res[:120] + "…"
            lines.append(f"  工具结果[{tr.get('name')}]: {res}")
        if step.assistant_text:
            txt = step.assistant_text
            if len(txt) > 200:
                txt = txt[:200] + "…"
            lines.append(f"  AI 思考/回复: {txt}")
    if traj.final_response:
        final = traj.final_response
        if len(final) > 300:
            final = final[:300] + "…"
        lines.append(f"最终回复: {final}")
    return "\n".join(lines)


def _shorten_args(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    pairs = []
    for k, v in args.items():
        sv = str(v)
        if len(sv) > 40:
            sv = sv[:40] + "…"
        pairs.append(f"{k}={sv}")
    return ", ".join(pairs)


def _parse_json_object(text: str) -> Any:
    """容错解析 JSON 对象(支持 ```json 围栏),失败返回 None。"""
    clean = (text or "").strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean = "\n".join(lines)
    try:
        return json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        return None
