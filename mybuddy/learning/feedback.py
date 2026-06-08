"""FeedbackEvent 总线。

解耦订阅者:
  - trajectory logger:写 labels 文件
  - (M6)skill success/fail 计数

CLI 的 /good /bad /fix 和隐式反馈统一 publish 事件,各订阅者独立处理。
同步分发(MVP 够用),订阅者自己保证不阻塞。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# 反馈类别
LABEL_GOOD = "good"
LABEL_BAD = "bad"
LABEL_FIX_PREFIX = "fix"  # 实际 label 形如 "fix:<修正文本>"
LABEL_IMPLICIT_NEGATIVE = "implicit:negative"


@dataclass
class FeedbackEvent:
    """一次反馈事件。"""

    turn_id: str
    label: str
    # 任意附加信息(skill_name、emotion 等)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_positive(self) -> bool:
        return self.label == LABEL_GOOD

    @property
    def is_negative(self) -> bool:
        return self.label in (LABEL_BAD, LABEL_IMPLICIT_NEGATIVE) or self.label.startswith(
            f"{LABEL_FIX_PREFIX}:"
        ) or self.label == LABEL_FIX_PREFIX


Subscriber = Callable[[FeedbackEvent], None]


class FeedbackBus:
    """同步 pub/sub,订阅者失败不影响其他订阅者。"""

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []

    def subscribe(self, sub: Subscriber) -> None:
        self._subs.append(sub)

    def publish(self, event: FeedbackEvent) -> None:
        logger.info("feedback: turn=%s label=%s", event.turn_id, event.label)
        for sub in self._subs:
            try:
                sub(event)
            except Exception:
                logger.exception("feedback subscriber failed")


# ---------------------------------------------------------------------
# 内置订阅者
# ---------------------------------------------------------------------

def make_trajectory_subscriber(logger_obj) -> Subscriber:
    """把 label 写入 trajectory 的 .labels.jsonl。"""

    def _sub(event: FeedbackEvent) -> None:
        logger_obj.attach_label(event.turn_id, event.label)

    return _sub


def make_skill_subscriber(registry) -> Subscriber:
    """把反馈信号回写到本轮触发的 skill 的 success/fail 计数。

    事件 meta 里约定字段 `triggered_skills: list[str]`(skill name 列表),由
    CLI 在 publish 时填。无此字段或列表为空则 no-op。
    """

    def _sub(event: FeedbackEvent) -> None:
        names = event.meta.get("triggered_skills") or []
        if not names:
            return
        if event.is_positive:
            for name in names:
                registry.record_success(name)
        elif event.is_negative:
            for name in names:
                registry.record_failure(name)

    return _sub


_NEGATIVE_KEYWORDS_RE = re.compile(
    r"(不对|不是这样|不是这个意思|我的意思是|错了|再试|重来|"
    r"理解错了?|没听懂|搞错|不准确|别这样|不是)"
)


def detect_implicit_negative(user_input: str) -> bool:
    """用户当前消息是否含"纠错/否定上一轮"的信号。

    保守起见:只要命中关键词就判定,宁可漏过(用户真没意见)也不误伤。
    """
    if not user_input:
        return False
    return bool(_NEGATIVE_KEYWORDS_RE.search(user_input))
