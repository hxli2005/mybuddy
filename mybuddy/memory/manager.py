"""记忆系统协调器。

统一管理三层记忆(短期/长期/画像)并暴露给 Agent:

  - add_message: 往短期记忆追加一条消息
  - build_context_section: 生成注入 system prompt 的"记忆上下文"文本块
  - maybe_extract: 每 N 轮后异步抽取事实,写入长期记忆和画像
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.config import Config
    from mybuddy.llm import BaseLLMProvider, Message

from mybuddy.memory.extractor import FactExtractor
from mybuddy.memory.long_term import LongTermMemory
from mybuddy.memory.profile import UserProfile
from mybuddy.memory.short_term import ShortTermMemory

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

        # 用于记录最近 user+assistant 文本对,供 extractor 使用
        self._recent_turns: list[str] = []
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

        段落:
          1. 相关长期记忆(top-k=3)
          2. 用户画像核心字段(全部)
          3. 与当前话题相关的动态命题(top-5, confidence >= 0.5)
        """
        parts: list[str] = []
        related_claim_ids: list[int] = []

        # 1. 长期记忆检索
        mem_hits = self._ltm.search(user_input, top_k=self._config.memory.long_term_top_k, mem_type="memory")
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
        self._recent_turns.append(f"USER: {user_text}")
        self._recent_turns.append(f"AI: {assistant_text}")
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
        threshold = self._config.memory.extract_after_turns
        if self._turns_since_extract < threshold:
            return False

        try:
            result = await self._extractor.extract(self._recent_turns)
        except Exception:
            logger.exception("事实抽取失败")
            self._recent_turns.clear()
            self._turns_since_extract = 0
            return False

        if result.is_empty():
            self._recent_turns.clear()
            self._turns_since_extract = 0
            return False

        # 写入长期记忆
        for fact in result.facts:
            self._ltm.add(fact, session_id=self._session_id)

        # 写入画像字段
        for key, value in result.profile_fields.items():
            self._profile.set_field(key, value)

        # 写入命题候选(M3 中新的命题从低置信度开始)
        for claim_data in result.claims:
            if isinstance(claim_data, dict) and "claim" in claim_data:
                conf = float(claim_data.get("confidence", 0.5))
                self._profile.add_claim(claim_data["claim"], confidence=conf)

        logger.info(
            "事实抽取完成: %d facts, %d fields, %d claims",
            len(result.facts),
            len(result.profile_fields),
            len(result.claims),
        )

        self._recent_turns.clear()
        self._turns_since_extract = 0
        return True

    # ---- 属性访问 ----

    @property
    def profile(self) -> UserProfile:
        return self._profile

    @property
    def long_term(self) -> LongTermMemory:
        return self._ltm
