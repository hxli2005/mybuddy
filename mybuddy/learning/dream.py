"""Dream Job:夜间记忆整理与主动关怀生成。

设计方案要求五件事,每件最小版:

  1. **去重合并**:对 long-term `memory` 条目两两算文本 token 相似度,高相似视为重复,
     保留更短的一条。
  2. **置信度重算**:对动态命题,结合"最近 N 天有无新证据"做线性衰减/加强。
  3. **冲突消解**:把所有现存命题打包交给 LLM 一次判断两两冲突,冲突对中置信度较低者 -0.2。
  4. **生成洞察**:把当天对话文本汇总交给 LLM,输出新命题候选(confidence 0.3-0.6),add_claim。
  5. **nudge 生成**:从 open_thread 里挑有具体由头的未完成话题,交给 LLM 写 1-2 条事件式短信。
  6. **角色动态**:基于角色生活状态和近期关系记忆,低频生成一条不强迫回复的动态。

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
from mybuddy.memory.governance import MemoryGovernance
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
DYNAMIC_COUNT = 1
INSIGHT_COUNT = 3
CLAIM_PROMOTION_LIMIT = 3
PROMOTION_CONFIDENCE = 0.75
PROMOTION_EVIDENCE_COUNT = 3
PROMOTION_EVIDENCE_DAYS = 2


@dataclass
class DreamReport:
    """Dream Job 执行结果,便于 CLI 打印和单元测试断言。"""

    merged_memories: int = 0
    confidence_updates: int = 0
    conflicts_resolved: int = 0
    insights_added: int = 0
    promoted_claims: int = 0
    nudges_enqueued: int = 0
    dynamics_enqueued: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"记忆合并 {self.merged_memories} 条 · "
            f"命题更新 {self.confidence_updates} 条 · "
            f"冲突消解 {self.conflicts_resolved} 对 · "
            f"晋升命题 {self.promoted_claims} 条 · "
            f"新增洞察 {self.insights_added} 条 · "
            f"nudge {self.nudges_enqueued} 条 · "
            f"dynamic {self.dynamics_enqueued} 条"
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
            ("recompute_confidence", self._recompute_confidence),
            ("resolve_conflicts", self._resolve_conflicts),
            ("promote_stable_claims", self._promote_stable_claims),
            ("generate_insights", self._generate_insights),
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
    # 2. 置信度重算
    # -----------------------------------------------------------------
    async def _recompute_confidence(self, report: DreamReport) -> None:
        claims = self._profile.get_all_claims(include_hidden=False)
        cutoff = utcnow() - timedelta(days=RECENT_EVIDENCE_DAYS)
        updated = 0

        for c in claims:
            # last_seen_at / evidence_days 近期 = 最近有新证据写入 → +BOOST
            # 否则 → -DECAY
            # 注意: updated_at 会被置信度重算本身刷新,不能作为证据时间。
            has_recent = _has_recent_claim_evidence(c, cutoff)
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
        claims = self._profile.get_all_claims(min_confidence=0.3, include_hidden=False)
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
            try:
                a, b = int(p.get("a")), int(p.get("b"))
            except (TypeError, ValueError):
                continue
            if a not in conf_by_id or b not in conf_by_id:
                continue
            self._profile.mark_claim_conflicts([a, b], [a, b])
            # 置信度较低一方 -PENALTY
            loser = a if conf_by_id[a] < conf_by_id[b] else b
            self._profile.update_confidence(loser, -CONFLICT_PENALTY)
            resolved += 1

        report.conflicts_resolved = resolved

    # -----------------------------------------------------------------
    # 4. 稳定命题晋升
    # -----------------------------------------------------------------
    async def _promote_stable_claims(self, report: DreamReport) -> None:
        claims = self._profile.get_all_claims(
            min_confidence=PROMOTION_CONFIDENCE,
            include_hidden=False,
        )
        if not claims:
            return

        governance = MemoryGovernance(self._ltm)
        promoted = 0
        for claim in claims:
            if promoted >= CLAIM_PROMOTION_LIMIT:
                break
            claim_id = claim.get("sql_id")
            if not isinstance(claim_id, int):
                continue
            self._profile.mark_claim_promotion_checked(claim_id)
            target_type = _promotion_target_type(claim)
            if target_type is None:
                continue
            if not _claim_passes_promotion_checks(claim):
                continue
            content = _promoted_claim_content(claim)
            result = governance.add_or_merge(
                content,
                mem_type=target_type,
                source="claim_promotion",
                extra_meta={
                    "title": _promotion_title(claim),
                    "confidence": claim.get("confidence", 0.75),
                    "importance": 0.75,
                    "category": claim.get("category", "general"),
                    "promoted_from_claim_id": claim_id,
                    "evidence_count": claim.get("evidence_count", 0),
                    "evidence_days": claim.get("evidence_days", []),
                    "callback_style": "作为稳定理解轻轻使用,不要向用户展示推理过程",
                },
            )
            if self._profile.mark_claim_promoted(claim_id, result.memory_id):
                promoted += 1

        report.promoted_claims = promoted

    # -----------------------------------------------------------------
    # 5. 生成洞察(从当日对话提炼新命题)
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
        for it in items[:INSIGHT_COUNT]:
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
    # 6. nudge 生成
    # -----------------------------------------------------------------
    async def _generate_nudges(self, report: DreamReport) -> None:
        items = self._ltm.list_all(mem_type="open_thread")
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
        report.nudges_enqueued = enqueued

    # -----------------------------------------------------------------
    # 7. 角色动态
    # -----------------------------------------------------------------
    async def _generate_dynamics(self, report: DreamReport) -> None:
        items: list[dict[str, Any]] = []
        for mem_type in ("shared_moment", "relationship_note", "character_note"):
            items.extend(self._ltm.list_all(mem_type=mem_type))
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


def _has_recent_claim_evidence(claim: dict[str, Any], cutoff: datetime) -> bool:
    evidence_time = _latest_claim_evidence_time(claim)
    return evidence_time is not None and evidence_time >= cutoff


def _latest_claim_evidence_time(claim: dict[str, Any]) -> datetime | None:
    times: list[datetime] = []
    evidence_days = claim.get("evidence_days")
    if isinstance(evidence_days, list):
        for value in evidence_days:
            parsed = _parse_claim_time(value)
            if parsed is not None:
                times.append(parsed)

    last_seen = _parse_claim_time(claim.get("last_seen_at"))
    if last_seen is not None:
        times.append(last_seen)

    return max(times) if times else None


def _parse_claim_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 10:
        text = f"{text}T23:59:59"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _claim_passes_promotion_checks(claim: dict[str, Any]) -> bool:
    if claim.get("status") not in {"active", "stable"}:
        return False
    if float(claim.get("confidence") or 0.0) < PROMOTION_CONFIDENCE:
        return False
    if int(claim.get("evidence_count") or 0) < PROMOTION_EVIDENCE_COUNT:
        return False
    evidence_days = claim.get("evidence_days") or []
    if not isinstance(evidence_days, list) or len(set(evidence_days)) < PROMOTION_EVIDENCE_DAYS:
        return False
    if claim.get("conflict_ids"):
        return False
    return True


def _promotion_target_type(claim: dict[str, Any]) -> str | None:
    category = str(claim.get("category") or "general")
    if category == "task":
        return None
    if category == "fact":
        return "memory"
    if category == "boundary":
        return "anti_preference"
    return "relationship_note"


def _promotion_title(claim: dict[str, Any]) -> str:
    category = str(claim.get("category") or "general")
    labels = {
        "fact": "稳定事实",
        "preference": "稳定偏好",
        "relationship": "关系默契",
        "emotion_pattern": "陪伴方式线索",
        "boundary": "回应避雷",
        "general": "稳定观察",
    }
    return labels.get(category, "稳定观察")


def _promoted_claim_content(claim: dict[str, Any]) -> str:
    text = str(claim.get("claim") or "").strip()
    category = str(claim.get("category") or "general")
    if category == "fact":
        return text
    if category == "boundary":
        return f"稳定避雷: {text}"
    return f"稳定观察: {text}"
