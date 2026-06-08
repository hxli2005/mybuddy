"""Dream Job:夜间记忆整理与主动关怀生成。

设计方案要求五件事,每件最小版:

  1. **去重合并**:对 long-term `memory` 条目两两算文本 token 相似度,高相似视为重复,
     保留更短的一条。
  2. **置信度重算**:对动态命题,结合"最近 N 天有无新证据"做线性衰减/加强。
  3. **冲突消解**:把所有现存命题打包交给 LLM 一次判断两两冲突,冲突对中置信度较低者 -0.2。
  4. **nudge 生成**:从 open_thread 里挑有具体由头的未完成话题,交给 LLM 写 1-2 条事件式短信。
  5. **角色动态**:基于角色生活状态和近期关系记忆,低频生成一条不强迫回复的动态。

每一步都在 try/except 里隔离,失败不影响其他步骤。触发入口是
`scheduler.jobs.run_dream_job`(凌晨 cron),也可以通过 `mybuddy dream run` 手动跑。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from mybuddy._time import utcnow
from mybuddy.storage import enqueue

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.config import Config
    from mybuddy.llm import BaseLLMProvider
    from mybuddy.memory import LongTermMemory, UserProfile

logger = logging.getLogger(__name__)


DEDUP_TEXT_THRESHOLD = 0.82
NUDGE_COUNT = 2
DYNAMIC_COUNT = 1
# 同一个未完成话题在这么多天内不重复 nudge,避免"你上次说的那个…"连推几天打扰用户。
NUDGE_COOLDOWN_DAYS = 3


@dataclass
class DreamReport:
    """Dream Job 执行结果,便于 CLI 打印和单元测试断言。"""

    merged_memories: int = 0
    insights_added: int = 0
    nudges_enqueued: int = 0
    dynamics_enqueued: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"记忆合并 {self.merged_memories} 条 · "
            f"nudge {self.nudges_enqueued} 条 · "
            f"dynamic {self.dynamics_enqueued} 条"
        )


NUDGE_PROMPT = """你是 {persona_name},一个克制但有牵挂的长期陪伴角色。
请基于以下 1-3 条未完成话题,写 {count} 条事件式短信(每条 18-50 字)。
要求:
- 每条必须有具体由头,像是你刚想起一件未收尾的小事。
- 不催促,不说教,不问泛泛的"现在感觉怎么样"。
- 不默认恋爱化,但可以有轻微偏爱和专属感。
严格输出 JSON 字符串数组,不要其他文本:
["...", "..."]

未完成话题:
{memories}
"""


DYNAMIC_PROMPT = """你是 {persona_name},一个有自己生活状态的长期陪伴角色。
请基于角色今天的状态和近期共同记忆,写 {count} 条轻量动态(每条 15-45 字)。
动态像是你自己的生活片段或顺手记下的小事,不要求用户回复。
不要像系统推送,不要总结用户画像,不要默认恋爱化。
严格输出 JSON 字符串数组,不要其他文本:
["..."]

角色今天的状态:
- {today_status}
- {current_mood}
- {recent_self_event}

近期关系记忆:
{memories}
"""


class DreamJob:
    """执行五件事,输出 DreamReport。"""

    def __init__(
        self,
        *,
        engine: Engine,
        config: Config,
        provider: BaseLLMProvider,
        ltm: LongTermMemory,
        profile: UserProfile,
    ) -> None:
        self._engine = engine
        self._config = config
        self._provider = provider
        self._ltm = ltm
        self._profile = profile

    async def run(self) -> DreamReport:
        report = DreamReport()

        for step_name, coro in (
            ("dedup_memories", self._dedup_memories),
            ("generate_nudges", self._generate_nudges),
            ("generate_dynamics", self._generate_dynamics),
        ):
            try:
                await coro(report)
            except Exception as e:  # noqa: BLE001
                logger.exception("dream step %s failed", step_name)
                report.errors.append(f"{step_name}: {type(e).__name__}: {e}")

        logger.info("dream report: %s", report.summary())
        return report

    # -----------------------------------------------------------------
    # 1. 去重合并
    # -----------------------------------------------------------------
    async def _dedup_memories(self, report: DreamReport) -> None:
        items = self._ltm.list_all(mem_type="memory")
        if len(items) < 2:
            return

        # 两两比较文本相似度,>= DEDUP_TEXT_THRESHOLD 视为重复
        to_delete: set[str] = set()
        n = len(items)
        for i in range(n):
            if items[i]["id"] in to_delete:
                continue
            for j in range(i + 1, n):
                if items[j]["id"] in to_delete:
                    continue
                sim = _text_similarity(items[i]["content"], items[j]["content"])
                if sim >= DEDUP_TEXT_THRESHOLD:
                    # 保留较短的(假定更精炼),删除较长的;等长保留 i
                    keep_i = len(items[i]["content"]) <= len(items[j]["content"])
                    drop = items[j]["id"] if keep_i else items[i]["id"]
                    to_delete.add(drop)
                    if not keep_i:
                        break

        for uid in to_delete:
            self._ltm.delete(uid)
        report.merged_memories = len(to_delete)

    # -----------------------------------------------------------------
    # 5. nudge 生成
    # -----------------------------------------------------------------
    async def _generate_nudges(self, report: DreamReport) -> None:
        now = utcnow()
        # 只 nudge 仍 active 的未完成话题(已 stale/resolved 的不该再翻出来),
        # 且跳过冷却期内刚 nudge 过的,避免反复打扰同一件事。
        items = [
            m
            for m in self._ltm.list_all(mem_type="open_thread")
            if (m.get("metadata") or {}).get("status", "active") == "active"
            and not _recently_nudged(m, now, NUDGE_COOLDOWN_DAYS)
        ]
        if not items:
            return

        items.sort(key=_updated_key, reverse=True)
        picks = items[:3]
        if not picks:
            return

        memories_block = "\n".join(_memory_line(m) for m in picks)
        prompt = NUDGE_PROMPT.format(
            persona_name=self._config.persona.name,
            count=NUDGE_COUNT,
            memories=memories_block,
        )
        from mybuddy.llm import Message, Role

        resp = await self._provider.generate(
            messages=[Message(role=Role.USER, content="请生成。")],
            system=prompt,
            temperature=0.7,
            model=self._config.llm.small_model or None,
        )
        nudges = _parse_json_array(resp.text)
        if not isinstance(nudges, list):
            return

        enqueued = 0
        for n in nudges[:NUDGE_COUNT]:
            if not isinstance(n, str) or not n.strip():
                continue
            enqueue(
                self._engine,
                source="nudge",
                content=n.strip(),
                meta={
                    "origin": "dream_job_open_thread",
                    "contact_reason": _first_contact_reason(picks),
                },
            )
            enqueued += 1
        # 标记本批被选中的话题已 nudge,进入冷却期,下次跳过。
        if enqueued:
            stamp = now.isoformat(timespec="seconds")
            for m in picks:
                self._ltm.update_metadata(m["id"], {"last_nudged_at": stamp})
        report.nudges_enqueued = enqueued

    # -----------------------------------------------------------------
    # 6. 角色动态
    # -----------------------------------------------------------------
    async def _generate_dynamics(self, report: DreamReport) -> None:
        items: list[dict[str, Any]] = []
        for mem_type in ("shared_moment", "preference"):
            items.extend(
                m
                for m in self._ltm.list_all(mem_type=mem_type)
                if (m.get("metadata") or {}).get("status", "active") == "active"
            )
        if not items:
            return
        items.sort(key=_updated_key, reverse=True)
        memories_block = "\n".join(_memory_line(m) for m in items[:3])
        life = self._config.persona.character_life
        prompt = DYNAMIC_PROMPT.format(
            persona_name=self._config.persona.name,
            count=DYNAMIC_COUNT,
            today_status=life.today_status,
            current_mood=life.current_mood,
            recent_self_event=life.recent_self_event,
            memories=memories_block,
        )
        from mybuddy.llm import Message, Role

        resp = await self._provider.generate(
            messages=[Message(role=Role.USER, content="请生成。")],
            system=prompt,
            temperature=0.7,
            model=self._config.llm.small_model or None,
        )
        dynamics = _parse_json_array(resp.text)
        if not isinstance(dynamics, list):
            return
        enqueued = 0
        for text in dynamics[:DYNAMIC_COUNT]:
            if not isinstance(text, str) or not text.strip():
                continue
            enqueue(
                self._engine,
                source="dynamic",
                content=text.strip(),
                meta={"origin": "dream_job_character_dynamic"},
            )
            enqueued += 1
        report.dynamics_enqueued = enqueued

# ---------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------

def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _recently_nudged(item: dict[str, Any], now: datetime, days: int) -> bool:
    """该未完成话题是否在冷却期内已 nudge 过(用同源 utcnow 写读,时区一致)。"""
    last = _parse_iso((item.get("metadata") or {}).get("last_nudged_at"))
    if last is None:
        return False
    return (now - last).days < days


def _text_similarity(a: str, b: str) -> float:
    ta = set(_tokenize_text(a))
    tb = set(_tokenize_text(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _tokenize_text(text: str) -> list[str]:
    import re

    tokens: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        for length in (1, 2):
            for i in range(len(chunk) - length + 1):
                tokens.append(chunk[i : i + length])
    tokens.extend(w.lower() for w in re.findall(r"[a-zA-Z0-9_]+", text))
    return tokens


def _parse_json_array(text: str) -> Any:
    """容错解析 JSON 数组/对象,支持 ```json 围栏。"""
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean = "\n".join(lines)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return None


def _updated_key(item: dict[str, Any]) -> str:
    meta = item.get("metadata") or {}
    return str(meta.get("updated_at") or meta.get("created_at") or "")


def _memory_line(item: dict[str, Any]) -> str:
    meta = item.get("metadata") or {}
    parts = []
    if meta.get("title"):
        parts.append(str(meta["title"]))
    parts.append(str(item.get("content", "")))
    if meta.get("contact_reason"):
        parts.append(f"由头:{meta['contact_reason']}")
    if meta.get("callback_style"):
        parts.append(f"回响方式:{meta['callback_style']}")
    return "- " + " / ".join(part for part in parts if part)


def _first_contact_reason(items: list[dict[str, Any]]) -> str:
    for item in items:
        meta = item.get("metadata") or {}
        reason = meta.get("contact_reason") or meta.get("title")
        if reason:
            return str(reason)
    return "open_thread"
