"""Dream Job:夜间记忆整理与主动关怀生成。

设计方案要求五件事,每件最小版:

  1. **去重合并**:对 long-term `memory` 条目两两算文本 token 相似度,高相似视为重复,
     保留更短的一条。
  2. **置信度重算**:对动态命题,结合"最近 N 天有无新证据"做线性衰减/加强。
  3. **冲突消解**:把所有现存命题打包交给 LLM 一次判断两两冲突,冲突对中置信度较低者 -0.2。
  4. **生成洞察**:把当天对话文本汇总交给 LLM,输出新命题候选(confidence 0.3-0.6),add_claim。
  5. **nudge 生成**:从 long-term memory 里挑"重要但久未提及"的条目,交给 LLM 写 1-2 条温暖问候,
     入 pending_messages 供 CLI 下次交互时播出。

每一步都在 try/except 里隔离,失败不影响其他步骤。触发入口是
`scheduler.jobs.run_dream_job`(凌晨 cron),也可以通过 `mybuddy dream run` 手动跑。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from mybuddy._time import utcnow
from mybuddy.storage import Message as DBMessage
from mybuddy.storage import enqueue, session_scope

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.config import Config
    from mybuddy.llm import BaseLLMProvider
    from mybuddy.memory import LongTermMemory, UserProfile

logger = logging.getLogger(__name__)


DEDUP_TEXT_THRESHOLD = 0.82
RECENT_EVIDENCE_DAYS = 7
CONFIDENCE_DECAY = 0.05     # 无新证据时的衰减量
CONFIDENCE_BOOST = 0.05     # 有新证据时的加强量
CONFLICT_PENALTY = 0.2
NUDGE_COUNT = 2


@dataclass
class DreamReport:
    """Dream Job 执行结果,便于 CLI 打印和单元测试断言。"""

    merged_memories: int = 0
    confidence_updates: int = 0
    conflicts_resolved: int = 0
    insights_added: int = 0
    nudges_enqueued: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"记忆合并 {self.merged_memories} 条 · "
            f"命题更新 {self.confidence_updates} 条 · "
            f"冲突消解 {self.conflicts_resolved} 对 · "
            f"新增洞察 {self.insights_added} 条 · "
            f"nudge {self.nudges_enqueued} 条"
        )


CONFLICT_PROMPT = """你是一个用户画像分析助手。下面是关于同一用户的命题集(每条带 id)。
找出语义冲突的命题对,输出 JSON 数组,每个元素形如 {"a": id_a, "b": id_b, "reason": "简述冲突"}。
没有冲突返回空数组 []。只输出 JSON,不要其他文本。

命题集:
"""


INSIGHT_PROMPT = """你是一个用户画像分析助手。从以下今天的对话中提炼 1-5 条关于用户的推测性命题(非事实)。
每条 10-30 字,附 confidence(0.3-0.6)。严格输出 JSON 数组,不要其他文本:
[{"claim": "...", "confidence": 0.4}]

对话:
"""


NUDGE_PROMPT = """你是用户的 AI 小伙伴,名字叫 {persona_name}。
请基于以下 1-3 条久未提及的过往记忆,写 {count} 条简短的主动问候(每条 15-40 字,温暖不套路)。
严格输出 JSON 字符串数组,不要其他文本:
["...", "..."]

相关记忆:
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
            ("recompute_confidence", self._recompute_confidence),
            ("resolve_conflicts", self._resolve_conflicts),
            ("generate_insights", self._generate_insights),
            ("generate_nudges", self._generate_nudges),
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
    # 2. 置信度重算
    # -----------------------------------------------------------------
    async def _recompute_confidence(self, report: DreamReport) -> None:
        claims = self._profile.get_all_claims()
        cutoff = utcnow() - timedelta(days=RECENT_EVIDENCE_DAYS)
        updated = 0

        for c in claims:
            # updated_at 近期 = 最近有新证据写入 → +BOOST
            # 否则 → -DECAY
            updated_iso = c.get("updated_at")
            try:
                last_update = datetime.fromisoformat(updated_iso) if updated_iso else None
            except (TypeError, ValueError):
                last_update = None

            has_recent = last_update is not None and last_update >= cutoff
            delta = CONFIDENCE_BOOST if has_recent else -CONFIDENCE_DECAY

            if self._profile.update_confidence(c["sql_id"], delta):
                updated += 1

        # 低置信度命题归档
        pruned = self._profile.prune_low_confidence(threshold=0.3)
        report.confidence_updates = updated
        if pruned:
            logger.info("dream pruned %d low-confidence claims", pruned)

    # -----------------------------------------------------------------
    # 3. 冲突消解
    # -----------------------------------------------------------------
    async def _resolve_conflicts(self, report: DreamReport) -> None:
        claims = self._profile.get_all_claims(min_confidence=0.3)
        if len(claims) < 2:
            return

        listing = "\n".join(
            f"{c['sql_id']}. {c['claim']} (置信度 {c['confidence']:.2f})"
            for c in claims
        )
        from mybuddy.llm import Message, Role

        resp = await self._provider.generate(
            messages=[Message(role=Role.USER, content=listing)],
            system=CONFLICT_PROMPT,
            temperature=0.2,
            model=self._config.llm.small_model or None,
        )
        pairs = _parse_json_array(resp.text)
        if not isinstance(pairs, list):
            return

        conf_by_id = {c["sql_id"]: c["confidence"] for c in claims}
        resolved = 0
        for p in pairs:
            if not isinstance(p, dict):
                continue
            a, b = p.get("a"), p.get("b")
            if a not in conf_by_id or b not in conf_by_id:
                continue
            # 置信度较低一方 -PENALTY
            loser = a if conf_by_id[a] < conf_by_id[b] else b
            self._profile.update_confidence(loser, -CONFLICT_PENALTY)
            resolved += 1

        report.conflicts_resolved = resolved

    # -----------------------------------------------------------------
    # 4. 生成洞察(从当日对话提炼新命题)
    # -----------------------------------------------------------------
    async def _generate_insights(self, report: DreamReport) -> None:
        texts = self._collect_today_messages()
        if not texts:
            return

        conversation = "\n".join(texts[-80:])  # 最多取最近 80 行,控制 prompt 长度
        from mybuddy.llm import Message, Role

        resp = await self._provider.generate(
            messages=[Message(role=Role.USER, content=conversation)],
            system=INSIGHT_PROMPT,
            temperature=0.4,
            model=self._config.llm.small_model or None,
        )
        items = _parse_json_array(resp.text)
        if not isinstance(items, list):
            return

        added = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            claim = it.get("claim")
            conf = float(it.get("confidence", 0.4))
            if not claim:
                continue
            self._profile.add_claim(claim, confidence=max(0.3, min(0.6, conf)))
            added += 1
        report.insights_added = added

    # -----------------------------------------------------------------
    # 5. nudge 生成
    # -----------------------------------------------------------------
    async def _generate_nudges(self, report: DreamReport) -> None:
        items = self._ltm.list_all(mem_type="memory")
        if not items:
            return

        # 挑 created_at 最久远的 top-3(若 metadata 没时间戳则随机取前 3)
        def _created_key(m: dict[str, Any]) -> str:
            return (m.get("metadata") or {}).get("created_at", "")

        items.sort(key=_created_key)
        picks = items[:3]
        if not picks:
            return

        memories_block = "\n".join(f"- {m['content']}" for m in picks)
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
                meta={"origin": "dream_job"},
            )
            enqueued += 1
        report.nudges_enqueued = enqueued

    # -----------------------------------------------------------------
    # 工具方法
    # -----------------------------------------------------------------
    def _collect_today_messages(self) -> list[str]:
        """读 messages 表当天的 user/assistant 消息文本。

        messages 表目前还没被 agent 写入(短期记忆在内存里),因此返回为空也正常;
        未来 agent 持久化到 DB 后这里自动有数据。
        """
        today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        with session_scope(self._engine) as s:
            rows = (
                s.query(DBMessage)
                .filter(DBMessage.created_at >= today_start)
                .order_by(DBMessage.created_at.asc())
                .all()
            )
            return [f"{m.role.upper()}: {m.content}" for m in rows]


# ---------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------

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
