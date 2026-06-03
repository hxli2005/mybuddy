"""记忆系统协调器。

统一管理三层记忆(短期/长期/画像)并暴露给 Agent:

  - add_message: 往短期记忆追加一条消息
  - build_context_section: 生成注入 system prompt 的"记忆上下文"文本块
  - maybe_extract: 每 N 轮后异步抽取事实,写入长期记忆和画像
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mybuddy.memory.extractor import RELATIONSHIP_MEMORY_TYPES, FactExtractor
from mybuddy.memory.governance import MemoryGovernance
from mybuddy.memory.long_term import LongTermMemory
from mybuddy.memory.profile import UserProfile
from mybuddy.memory.short_term import ShortTermMemory

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.config import Config
    from mybuddy.llm import BaseLLMProvider, Message

logger = logging.getLogger(__name__)


class MemoryManager:
    """统一记忆入口。

    Agent 的每个 turn:
      1. mm.add_message(user_msg)           # 记录用户消息
      2. ctx_section = mm.build_context_section(user_input)  # 检索相关记忆
      3. llm.generate(system + ctx_section + messages)
      4. mm.add_message(assistant_msg)      # 记录 AI 回复
      5. await mm.maybe_extract()           # 每 N 轮尝试抽取事实
    """

    def __init__(
        self,
        engine: Engine,
        config: Config,
        ltm: LongTermMemory,
        provider: BaseLLMProvider,
        *,
        session_id: str = "",
    ) -> None:
        self._engine = engine
        self._config = config
        self._ltm = ltm
        self._provider = provider
        self._session_id = session_id

        self._short_term = ShortTermMemory(capacity=config.memory.short_term_size)
        self._profile = UserProfile(engine, ltm)
        self._extractor = FactExtractor(provider, config.llm.small_model)
        self._ltm.normalize_metadata()
        self._governance = MemoryGovernance(ltm)

        # 用于记录最近 user+assistant 文本对,供 extractor 使用
        self._recent_turns: list[str] = []
        self._recent_turn_ids: list[str] = []
        self._turns_since_extract = 0

    # ---- 短期记忆 ----

    def add_message(self, msg: Message) -> None:
        self._short_term.add(msg)

    def get_recent_messages(self) -> list[Message]:
        return self._short_term.get_all()

    # ---- 上下文构建 ----

    def build_context_section(self, user_input: str) -> tuple[str, list[int]]:
        """构建注入 system prompt 的记忆上下文文本块。

        返回 (text, related_claim_ids):
          text:包含长期记忆 / 用户画像 / 命题三段,空段自动省略
          related_claim_ids:本轮命中的命题 sql_id 列表,供 FeedbackBus 反馈回写使用

        关系陪伴优先级:
          1. 未完成话题 / 共同经历 / 私人暗号 / 避免事项
          2. 关系线索 / 角色线索
          3. 普通长期记忆 / 用户画像 / 动态命题
        """
        self._ensure_governance_state()
        parts: list[str] = []
        related_claim_ids: list[int] = []

        scene = _infer_scene(user_input)
        if scene:
            parts.append(
                "## 当前场景线索\n"
                f"- {scene}\n"
                "- 使用记忆时要像自然想起旧事,不要把记忆条目逐条汇报给用户。"
            )

        relation_sections = [
            ("open_thread", "## 未完成话题(有具体由头才提)", 2),
            ("shared_moment", "## 共同经历(可轻轻回响)", 2),
            ("private_code", "## 私人暗号", 2),
            ("anti_preference", "## 避免事项", 3),
            ("relationship_note", "## 关系线索", 2),
            ("character_note", "## 角色侧线索", 1),
        ]
        if self._ltm is not None:
            self._governance.refresh_open_thread_lifecycle()
        for mem_type, title, limit in relation_sections:
            hits = self._relationship_hits(user_input, mem_type, top_k=limit)
            if hits:
                lines = [title]
                lines.extend(f"- {_format_memory_hit(h)}" for h in hits)
                parts.append("\n".join(lines))

        # 1. 长期记忆检索
        mem_hits = self._ltm.search(
            user_input,
            top_k=self._config.memory.long_term_top_k,
            mem_type="memory",
        ) if self._ltm is not None else []
        if mem_hits:
            mem_lines = ["## 相关历史记忆"]
            for h in mem_hits:
                if h["score"] < 0.3:
                    continue
                mem_lines.append(f"- {h['content']}")
            if len(mem_lines) > 1:
                parts.append("\n".join(mem_lines))

        # 2. 用户画像核心字段
        fields = self._profile.get_all_fields()
        if fields:
            field_lines = ["## 用户画像"]
            for k, v in fields.items():
                field_lines.append(f"- {k}: {v}")
            parts.append("\n".join(field_lines))

        # 3. 与当前话题相关的命题(语义搜索, confidence >= 0.5)
        claim_hits = self._profile.search_claims(user_input, top_k=5, min_confidence=0.5)
        if claim_hits:
            claim_lines = ["## 关于用户的认知(仅供参考)"]
            for c in claim_hits:
                claim_lines.append(f"- {c['claim']} (置信度 {c['confidence']:.0%})")
                sid = c.get("sql_id")
                if isinstance(sid, int):
                    related_claim_ids.append(sid)
            parts.append("\n".join(claim_lines))

        text = "\n\n".join(parts) if parts else ""
        return text, related_claim_ids

    # ---- 事实抽取 ----

    def record_turn(
        self,
        user_text: str,
        assistant_text: str,
        *,
        turn_id: str | None = None,
    ) -> None:
        """记录一轮对话文本,供抽取器使用。"""
        self._ensure_governance_state()
        self._recent_turns.append(f"USER: {user_text}")
        self._recent_turns.append(f"AI: {assistant_text}")
        if turn_id and turn_id not in self._recent_turn_ids:
            self._recent_turn_ids.append(turn_id)
        self._turns_since_extract += 1
        if hasattr(self._ltm, "record_conversation_turn"):
            self._ltm.record_conversation_turn(
                session_id=self._session_id,
                turn_id=turn_id,
                user_text=user_text,
                assistant_text=assistant_text,
            )

    async def maybe_extract(self) -> bool:
        """如果积累的对话轮数达到阈值,触发抽取。返回是否执行了抽取。"""
        self._ensure_governance_state()
        threshold = self._config.memory.extract_after_turns
        if self._turns_since_extract < threshold:
            return False

        try:
            result = await self._extractor.extract(self._recent_turns)
        except Exception:
            logger.exception("事实抽取失败")
            self._recent_turns.clear()
            self._recent_turn_ids.clear()
            self._turns_since_extract = 0
            return False

        if result.is_empty():
            self._recent_turns.clear()
            self._recent_turn_ids.clear()
            self._turns_since_extract = 0
            return False

        # 写入长期记忆
        if self._ltm is not None:
            for fact in result.facts:
                self._governance.add_or_merge(
                    fact,
                    mem_type="memory",
                    session_id=self._session_id,
                    source="fact_extraction",
                    extra_meta={"source_turn_ids": list(self._recent_turn_ids)},
                )

        # 写入画像字段
        for key, value in result.profile_fields.items():
            self._profile.set_field(key, value)

        # 写入命题候选(M3 中新的命题从低置信度开始)
        for claim_data in result.claims:
            if isinstance(claim_data, dict) and "claim" in claim_data:
                conf = float(claim_data.get("confidence", 0.5))
                self._profile.add_claim(
                    claim_data["claim"],
                    confidence=conf,
                    evidence_ids=list(self._recent_turn_ids),
                )

        relationship_count = 0
        if self._ltm is not None:
            for mem_type in RELATIONSHIP_MEMORY_TYPES:
                for item in result.relationship_memories.get(mem_type, []):
                    content, meta = _relation_item_to_card(item)
                    if not content:
                        continue
                    meta.setdefault("source_turn_ids", list(self._recent_turn_ids))
                    self._governance.add_or_merge(
                        content,
                        mem_type=mem_type,
                        session_id=self._session_id,
                        source="relationship_extraction",
                        extra_meta=meta,
                    )
                    relationship_count += 1

        logger.info(
            "事实抽取完成: %d facts, %d fields, %d claims, %d relationship memories",
            len(result.facts),
            len(result.profile_fields),
            len(result.claims),
            relationship_count,
        )

        self._recent_turns.clear()
        self._recent_turn_ids.clear()
        self._turns_since_extract = 0
        return True

    # ---- 属性访问 ----

    @property
    def profile(self) -> UserProfile:
        return self._profile

    @property
    def long_term(self) -> LongTermMemory:
        return self._ltm

    def _ensure_governance_state(self) -> None:
        """补齐记忆治理状态,兼容绕过 __init__ 的测试替身。"""
        if not hasattr(self, "_recent_turn_ids"):
            self._recent_turn_ids = []
        if not hasattr(self, "_governance") and self._ltm is not None:
            self._governance = MemoryGovernance(self._ltm)

    def _relationship_hits(
        self,
        user_input: str,
        mem_type: str,
        *,
        top_k: int,
    ) -> list[dict]:
        if self._ltm is None:
            return []
        hits = [
            h for h in self._ltm.search(user_input, top_k=top_k, mem_type=mem_type)
            if h.get("score", 0) >= 0.25
        ]
        if hits:
            return hits[:top_k]
        if mem_type not in {"open_thread", "shared_moment", "anti_preference", "private_code"}:
            return []
        fallback = [
            item for item in self._ltm.list_all(mem_type=mem_type)
            if (item.get("metadata") or {}).get("status", "active") == "active"
        ]
        fallback.sort(
            key=lambda item: (item.get("metadata") or {}).get("updated_at", ""),
            reverse=True,
        )
        return fallback[:top_k]

    def _looks_duplicate(self, mem_type: str, content: str) -> bool:
        if self._ltm is None:
            return False
        hits = self._ltm.search(content, top_k=1, mem_type=mem_type)
        return bool(hits and hits[0].get("score", 0) >= 0.88)


def _infer_scene(user_input: str) -> str:
    text = user_input or ""
    if any(k in text for k in ("不想", "拖延", "写不动", "动不了", "好累", "累")):
        return "用户可能处在低压陪伴/启动困难场景;先给角色内微反应,再给一个很小的下一步。"
    if any(k in text for k in ("开心", "好了", "搞定", "完成", "通过")):
        return "用户可能在分享进展;可以具体接住这件事,不要夸张庆祝。"
    if any(k in text for k in ("提醒", "天气", "查", "帮我")):
        return "用户有现实任务;完成任务时保持角色口吻,不要变成工具播报。"
    return ""


def _format_memory_hit(hit: dict) -> str:
    meta = hit.get("metadata") or {}
    title = meta.get("title")
    content = hit.get("content", "")
    bits = []
    if title:
        bits.append(str(title))
    bits.append(str(content))
    for key, label in (
        ("contact_reason", "由头"),
        ("callback_style", "回响方式"),
        ("emotional_color", "情绪色"),
        ("event_time", "事件时间"),
        ("expires_at", "过期时间"),
        ("status", "状态"),
    ):
        value = meta.get(key)
        if value:
            bits.append(f"{label}:{value}")
    return " / ".join(bit for bit in bits if bit)


def _relation_item_to_card(item: dict) -> tuple[str, dict]:
    content = str(
        item.get("content")
        or item.get("summary")
        or item.get("text")
        or item.get("title")
        or ""
    ).strip()
    if not content:
        return "", {}
    meta: dict = {
        "confidence": _clamp_float(item.get("confidence", 0.7), 0.3, 1.0),
        "importance": _clamp_float(item.get("importance", 0.65), 0.1, 1.0),
    }
    for key in (
        "title",
        "triggers",
        "emotional_color",
        "callback_style",
        "contact_reason",
        "event_time",
        "observed_at",
        "expires_at",
        "status",
        "source_turn_ids",
    ):
        value = item.get(key)
        if value:
            meta[key] = value
    keywords: list[str] = []
    for value in (meta.get("title"), meta.get("triggers"), meta.get("contact_reason")):
        if isinstance(value, str):
            keywords.extend(part for part in value.replace(",", " ").split() if part)
        elif isinstance(value, list):
            keywords.extend(str(part) for part in value if str(part).strip())
    if keywords:
        meta["keywords"] = keywords[:12]
        meta["tags"] = keywords[:6]
    return content, meta


def _clamp_float(value: object, low: float, high: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, f))
