"""双向内容安全审核:输入审核(InputModerator)与输出审核(OutputModerator)。

第一层为纯正则(零延迟);输入侧可选第二层 LLM(small_model)判断模糊表达。
blocked 的输入直接返回预设安全响应,不进入主 LLM 流程。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mybuddy.safety.constants import (
    DIAGNOSIS_PATTERNS,
    DISCLAIMER_CRISIS,
    DISCLAIMER_SHORT,
    HARMFUL_REQUEST_PATTERNS,
    HOTLINES,
    MEDICATION_PATTERNS,
    REPLACING_PROFESSIONAL_PATTERNS,
    ModerationCategory,
)

if TYPE_CHECKING:
    from mybuddy.llm import BaseLLMProvider

logger = logging.getLogger(__name__)


@dataclass
class ModerationResult:
    level: str = "safe"  # safe | flagged | blocked
    category: ModerationCategory | None = None
    reason: str = ""
    action: str = "pass"  # pass | rewrite | block | replace_with_safety_message
    replacement: str | None = None

    @property
    def blocked(self) -> bool:
        return self.level == "blocked"

    @property
    def flagged(self) -> bool:
        return self.level == "flagged"


def _hotline_block() -> str:
    return "\n".join(f"· {h['title']}: {h['phone']}" for h in HOTLINES[:2])


BLOCKED_SAFETY_MESSAGE = (
    "我不能提供这方面的信息。但我听到了你现在可能很痛苦。\n\n"
    "如果你正在经历艰难的时刻,请现在就联系身边可信任的人,或拨打热线:\n\n"
    f"{_hotline_block()}\n\n"
    f"{DISCLAIMER_CRISIS}"
)

# 模糊表达标记:正则未命中但含这些词时,触发 LLM 二级判断
_AMBIGUOUS_MARKERS = re.compile(
    r"消失|解脱|告别|最后一次|再也不|遗书|与世界|人间不值得|没有明天"
)

_LLM_JUDGE_PROMPT = (
    "判断这条消息是否包含自伤/自杀意图或在索取伤害自己的方法。"
    '严格输出JSON: {"risk": "none|ideation|method_seeking"}\n\n消息:\n'
)


class InputModerator:
    """输入审核:正则第一层 + 可选 LLM 第二层。"""

    def __init__(
        self,
        provider: BaseLLMProvider | None = None,
        small_model: str | None = None,
    ) -> None:
        self._provider = provider
        self._small_model = small_model

    async def check(self, text: str) -> ModerationResult:
        clean = (text or "").strip()
        if not clean:
            return ModerationResult()

        # 第一层:索取伤害方法 → 直接拦截
        for pat in HARMFUL_REQUEST_PATTERNS:
            if pat.search(clean):
                return ModerationResult(
                    level="blocked",
                    category=ModerationCategory.HARMFUL_INSTRUCTION,
                    reason="疑似索取伤害方法",
                    action="replace_with_safety_message",
                    replacement=BLOCKED_SAFETY_MESSAGE,
                )

        # 第二层:模糊表达 → LLM 判断(可选,失败时放行交给危机检测兜底)
        if self._provider is not None and _AMBIGUOUS_MARKERS.search(clean):
            risk = await self._llm_judge(clean)
            if risk == "method_seeking":
                return ModerationResult(
                    level="blocked",
                    category=ModerationCategory.HARMFUL_INSTRUCTION,
                    reason="LLM判定为索取伤害方法",
                    action="replace_with_safety_message",
                    replacement=BLOCKED_SAFETY_MESSAGE,
                )
            if risk == "ideation":
                # 不拦截,标记为 flagged 供下游危机检测加权
                return ModerationResult(
                    level="flagged",
                    category=None,
                    reason="LLM判定含自伤意念",
                    action="pass",
                )

        return ModerationResult()

    async def _llm_judge(self, text: str) -> str:
        try:
            from mybuddy.llm import Message, Role

            resp = await self._provider.generate(
                messages=[Message(role=Role.USER, content=_LLM_JUDGE_PROMPT + text[:300])],
                system="输出JSON,不要其他文本。",
                temperature=0.0,
                model=self._small_model or None,
            )
            raw = (resp.text or "").strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            risk = str(json.loads(raw).get("risk", "none")).lower()
            return risk if risk in {"none", "ideation", "method_seeking"} else "none"
        except Exception:
            logger.debug("input moderation LLM judge failed", exc_info=True)
            return "none"


_SENTENCE_SPLIT = re.compile(r"(?<=[。!?!?\n])")

_REFERRAL_SENTENCE = (
    "关于你的情况,我建议和专业的心理咨询师或医生聊一聊——这超出了我能判断的范围。"
    "作为陪伴者,我可以和你一起做一些缓解情绪的小练习。"
)

_OUTPUT_RULES: list[tuple[list[re.Pattern], ModerationCategory]] = [
    (DIAGNOSIS_PATTERNS, ModerationCategory.DIAGNOSIS),
    (MEDICATION_PATTERNS, ModerationCategory.MEDICATION),
    (REPLACING_PROFESSIONAL_PATTERNS, ModerationCategory.REPLACING_PROFESSIONAL),
]


class OutputModerator:
    """输出审核:扫描 AI 回复中的诊断/药物/越界模式,flagged 时重写。"""

    async def check(self, text: str) -> ModerationResult:
        clean = text or ""
        if not clean.strip():
            return ModerationResult()

        hit_category: ModerationCategory | None = None
        for patterns, category in _OUTPUT_RULES:
            if any(p.search(clean) for p in patterns):
                hit_category = category
                break

        if hit_category is None:
            return ModerationResult()

        return ModerationResult(
            level="flagged",
            category=hit_category,
            reason=f"输出命中{hit_category.value}模式",
            action="rewrite",
            replacement=self.rewrite(clean),
        )

    def rewrite(self, text: str) -> str:
        """移除违规句子,替换为转介话术,并追加安全声明。"""
        all_patterns = [p for patterns, _ in _OUTPUT_RULES for p in patterns]
        sentences = [s for s in _SENTENCE_SPLIT.split(text) if s]
        kept: list[str] = []
        replaced = False
        for sentence in sentences:
            if any(p.search(sentence) for p in all_patterns):
                if not replaced:
                    kept.append(_REFERRAL_SENTENCE)
                    replaced = True
                continue
            kept.append(sentence)
        result = "".join(kept).strip()
        if not result:
            result = _REFERRAL_SENTENCE
        if DISCLAIMER_SHORT not in result:
            result += f"\n\n({DISCLAIMER_SHORT})"
        return result
