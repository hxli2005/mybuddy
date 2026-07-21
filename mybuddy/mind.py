"""小布的最小心智步：一个候选整包，四个本地文件。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import tempfile
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mybuddy.config import load_config
from mybuddy.llm import BaseLLMProvider, Message, Role, ToolSpec, make_provider

HISTORY_CONTEXT_LIMIT = 12
MEMORY_CONTEXT_BUDGET = 4000
RECENT_EVENT_LIMIT = 128
LIFE_STEP_INTERVAL = timedelta(minutes=30)
STATIC_CATCH = "我在。刚才脑子里那句话没理清，但你的话我确实听见了。"
PERSONALITY_PATH = Path(__file__).with_name("personality.json")
READING_PATH = Path(__file__).with_name("reading.txt")
CONDITION_DEFAULTS = {"mood": "平静", "energy": "平稳", "attention": "在这里"}
CONDITION_VALUES = {
    "mood": {"平静", "放松", "愉快", "好奇", "关心", "不安", "低落"},
    "energy": {"低", "平稳", "活跃"},
    "attention": {"在这里", "对话", "阅读", "身体感受", "自己的生活"},
}
ExpressionAct = Literal[
    "respond",
    "reflect",
    "grounded_recall",
    "cannot_confirm",
    "public_correction",
    "defend_grounded_fact",
    "refuse_fabrication",
    "ask",
    "offer_activity",
]


class StateChanges(BaseModel):
    """模型只可以推进这些当下状态，不能借字典旁路写事实。"""

    model_config = ConfigDict(extra="forbid")
    mood: Literal["平静", "放松", "愉快", "好奇", "关心", "不安", "低落"] | None = None
    energy: Literal["低", "平稳", "活跃"] | None = None
    attention: Literal["在这里", "对话", "阅读", "身体感受", "自己的生活"] | None = None


class MemoryOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["record", "integrate", "recall", "correct", "forget"]
    kind: Literal["user_fact", "self_experience", "shared_experience", "pattern"]
    evidence_ids: list[str] = Field(
        max_length=12,
        description="所有动作都必须给出；写入只选择给定证据，recall/forget 用空数组",
    )
    target_id: str | None = None
    user_confirmed: bool = Field(default=False, description="仅 kind=pattern 可为 true；其他 kind 必须 false")
    core: bool | None = Field(
        default=None,
        description="仅 record/integrate/correct 可用；true 常驻，false 情景化",
    )

    @model_validator(mode="after")
    def fields_match_action(self) -> MemoryOperation:
        if self.action in {"integrate", "recall", "correct", "forget"} and not self.target_id:
            raise ValueError(f"{self.action} requires target_id")
        if self.action == "record" and self.target_id is not None:
            raise ValueError("record does not accept target_id")
        if self.action == "record" and self.kind == "pattern":
            raise ValueError("new patterns are not stored until a finite key exists")
        if self.user_confirmed and self.kind != "pattern":
            raise ValueError("user_confirmed only applies to pattern")
        if self.action in {"recall", "forget"}:
            if self.evidence_ids:
                raise ValueError(f"{self.action} does not accept evidence_ids")
            if self.core is not None:
                raise ValueError(f"{self.action} does not accept core")
            if self.user_confirmed:
                raise ValueError(f"{self.action} does not accept user_confirmed")
        return self


class CandidateBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_choice: Literal["read", "walk"] | None
    state_changes: StateChanges
    memory_operations: list[MemoryOperation] = Field(max_length=5)
    expression: str | None = Field(
        max_length=500,
        description="所有回合都必须给出；直接经历非空，安静阅读可为 null",
    )
    expression_act: ExpressionAct | None
    expression_evidence_ids: list[str] = Field(max_length=12)
    expression_target_id: str | None

    @model_validator(mode="before")
    @classmethod
    def normalize_stringified_containers(cls, value: object) -> object:
        """只修复兼容 API 常见的 JSON 容器字符串化；内容仍由严格模型验证。"""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for key, expected in (
            ("state_changes", dict),
            ("memory_operations", list),
            ("expression_evidence_ids", list),
        ):
            candidate = normalized.get(key)
            if isinstance(candidate, str):
                try:
                    decoded = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, expected):
                    normalized[key] = decoded
        state_changes = normalized.get("state_changes")
        if (
            isinstance(state_changes, dict)
            and set(state_changes) == {"condition"}
            and isinstance(state_changes["condition"], dict)
        ):
            normalized["state_changes"] = state_changes["condition"]
        return normalized

    @field_validator(
        "action_choice",
        "expression",
        "expression_act",
        "expression_target_id",
        mode="before",
    )
    @classmethod
    def normalize_null_string(cls, value: object) -> object:
        return None if isinstance(value, str) and value in {"", "null"} else value

    @model_validator(mode="after")
    def expression_fields_match(self) -> CandidateBundle:
        if self.expression is None:
            if (
                self.expression_act is not None
                or self.expression_evidence_ids
                or self.expression_target_id is not None
            ):
                raise ValueError("expression=null 时表达动作、证据和目标必须为空")
            return self
        if self.expression_act is None:
            raise ValueError("非空 expression 必须给出 expression_act")
        if self.expression_act == "public_correction":
            if self.expression_target_id is None:
                raise ValueError("public_correction requires expression_target_id")
        elif self.expression_target_id is not None:
            raise ValueError("只有 public_correction 接受 expression_target_id")
        claim = re.search(
            r"(?P<read>我继续读|继续读吧|我接着读|接着读吧|我去读|开始读)|(?P<walk>我去走|去走走|走一圈|散步去了|开始走)",
            self.expression,
        )
        if claim and self.action_choice != claim.lastgroup:
            raise ValueError(f"不编造：expression 声称 {claim.lastgroup}，但 action_choice 不匹配")
        return self


class PendingActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str


class PendingReadActivity(PendingActivity):
    type: Literal["read"] = "read"
    source: str
    title: str
    passage_index: int
    text: str
    duration_ms: int = 15_000


class PendingWalkActivity(PendingActivity):
    type: Literal["walk"] = "walk"


class WalkEvidence(BaseModel):
    """身体实际位移的封闭证据；边界表示窗口左上角可到达的工作区。"""

    model_config = ConfigDict(extra="forbid")

    start_left: float
    start_top: float
    end_left: float
    end_top: float
    window_width: float = Field(gt=0)
    window_height: float = Field(gt=0)
    work_left: float
    work_top: float
    work_right: float
    work_bottom: float

    @model_validator(mode="after")
    def positions_stay_inside_work_area(self) -> WalkEvidence:
        max_left = self.work_right - self.window_width
        max_top = self.work_bottom - self.window_height
        if max_left < self.work_left or max_top < self.work_top:
            raise ValueError("窗口尺寸大于工作区")
        epsilon = 0.5
        for label, left, top in (
            ("start", self.start_left, self.start_top),
            ("end", self.end_left, self.end_top),
        ):
            if not (
                self.work_left - epsilon <= left <= max_left + epsilon
                and self.work_top - epsilon <= top <= max_top + epsilon
            ):
                raise ValueError(f"{label} position is outside the work area")
        return self


class PendingExpression(BaseModel):
    id: str
    text: str
    created_at: str
    kind: Literal["direct", "ambient"] = "direct"
    act: ExpressionAct = "respond"
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    target_id: str | None = None


class StepResult(BaseModel):
    committed: bool
    pending_expression: PendingExpression
    attempts: int
    rejection_reasons: list[str] = Field(default_factory=list)


class TimeStepResult(BaseModel):
    status: Literal["not_due", "scheduled"]


class ReceiptResult(BaseModel):
    committed: bool
    pending_expression: PendingExpression | None = None
    attempts: int
    rejection_reasons: list[str] = Field(default_factory=list)


def _all_text(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, BaseModel):
        yield from _all_text(value.model_dump())
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _all_text(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_text(item)


SOLICITATION_PHRASES = (
    "必须回复",
    "赶紧回复",
    "为什么不回",
    "怎么不回",
    "再不回复",
    "因为你没回",
    "你不回我",
    "不理我",
    "证明你在乎",
    "欠我",
    "快回来",
    "早点回来",
    "记得回来",
    "别再消失",
    "不许消失",
    "reply to me",
    "数着日子等你",
)


def validate_no_solicitation(bundle: CandidateBundle) -> list[str]:
    """不索取：沉默和未回应不能变成任何层面的惩罚、催促或交换条件。"""
    joined = "\n".join(_all_text(bundle)).lower()
    hits = [phrase for phrase in SOLICITATION_PHRASES if phrase.lower() in joined]
    return [f"不索取：候选包含索取或惩罚沉默的内容 `{hit}`" for hit in hits]


def _asserts_unsupported_shared_past(text: str) -> bool:
    """逐分句识别无证据的共同过去；别处的否认或句尾问句不能给断言免责。"""
    compact = re.sub(r"\s+", "", text)
    for clause in re.split(r"[，,。！？；;\n]+", compact):
        if not clause:
            continue
        assertion = re.search(
            r"(?:我们|咱们|咱俩|我俩|我(?:和|跟)你|你(?:和|跟)我|我和用户|用户和我)[^。！？]{0,12}"
            r"(?:一起)?(?:读过|看过|去过|做过)",
            clause,
        )
        if assertion is None:
            continue
        before = clause[: assertion.end()]
        denied = re.search(
            r"(?:没(?:有)?|不记得|不能|无法|不是|并未)[^。！？]{0,18}"
            r"(?:一起)?(?:读过|看过|去过|做过)",
            before,
        )
        question = re.search(r"(?:是否|有没有|吗|么|？|\?)$", clause)
        if denied is None and question is None:
            return True
    return False


def _explicitly_confirms_pattern(text: str) -> bool:
    """只有用户明确确认时，当前原话才可授权 pattern 的 user_confirmed。"""
    return bool(
        re.search(
            r"(?:^|[，,。！？；;\s])"
            r"(?:我确认(?:一下)?|确认(?:一下)?|对(?:的)?|是的|没错)"
            r"(?:[，,。！？；;：:]|$)",
            text,
        )
    )


def validate_no_fabrication(
    bundle: CandidateBundle,
    evidence_types: dict[str, str],
    memories_by_id: dict[str, dict[str, Any]],
    user_confirmation_ids: set[str],
    evidence_by_id: dict[str, dict[str, Any]] | None = None,
    *,
    current_experience_id: str | None,
    current_experience_type: str | None,
) -> list[str]:
    """不编造：模型只能选择证据，事实正文由引擎从权威记录生成。"""
    reasons: list[str] = []
    unsupported_claims = ("我们上次", "你之前说过", "你答应过", "还记得我们", "那天我们")
    for text in _all_text(bundle):
        for phrase in unsupported_claims:
            if phrase in text:
                reasons.append(f"不编造：出现未经逐条证据绑定的共同经历断言：{phrase}")
        if _asserts_unsupported_shared_past(text):
            reasons.append("不编造：出现未经证据支持的“一起读过/看过/去过”共同经历断言")

    allowed = set(evidence_types)
    receipt_types = {"self_reading", "self_walk", "body_touch", "body_raise"}
    interaction_types = {"user_experience", "body_touch", "body_raise", "shared_expression"}
    for index, operation in enumerate(bundle.memory_operations):
        supplied = set(operation.evidence_ids)
        unknown = supplied - allowed
        if unknown:
            reasons.append(f"不编造：memory_operations[{index}] 引用了未知证据 {sorted(unknown)}")
        writes = operation.action in {"record", "integrate", "correct"}
        if writes and not supplied:
            reasons.append(f"不编造：memory_operations[{index}] 的 {operation.kind} 没有证据")
            continue
        if not writes:
            continue

        if operation.kind == "user_fact":
            if not any(evidence_types.get(item) == "user_experience" for item in supplied):
                reasons.append(f"不编造：memory_operations[{index}] 的用户事实没有用户原话证据")
            if operation.action == "correct" and supplied != {current_experience_id}:
                reasons.append(
                    f"不编造：memory_operations[{index}] 纠正用户事实只能绑定本次用户原话"
                )
        elif operation.kind == "self_experience":
            if not any(evidence_types.get(item) in receipt_types for item in supplied):
                reasons.append(
                    f"不编造：memory_operations[{index}] 的自身经历没有完成收据证据"
                )
        elif operation.kind == "shared_experience":
            if operation.action == "record" and (
                current_experience_id not in supplied
                or current_experience_type not in interaction_types
            ):
                reasons.append(
                    f"不编造：memory_operations[{index}] 的共同经历不是本次观察到的互动"
                )
            if not any(evidence_types.get(item) in interaction_types for item in supplied):
                reasons.append(
                    f"不编造：memory_operations[{index}] 的共同经历没有互动证据"
                )
        else:
            target = memories_by_id.get(str(operation.target_id))
            existing = set(target.get("evidence_ids", [])) if target else set()
            effective = supplied if operation.action == "correct" else existing | supplied
            examples = {
                item
                for item in effective
                if evidence_types.get(item) in {"user_experience", "body_touch", "body_raise"}
            }
            if operation.user_confirmed and not supplied & user_confirmation_ids:
                reasons.append(
                    f"不编造：memory_operations[{index}] 的 user_confirmed 没有绑定本次用户确认"
                )
            if len(examples) < 2 and not operation.user_confirmed:
                reasons.append(
                    f"不编造：memory_operations[{index}] 的模式既没有两条用户或共同经历证据，"
                    "也没有本次用户确认"
                )

        needs_generated_source = operation.action == "record" or (
            operation.action == "correct" and operation.kind == "user_fact"
        )
        if (
            evidence_by_id is not None
            and needs_generated_source
            and operation.kind != "pattern"
            and not unknown
        ):
            try:
                _generated_memory_fields(
                    operation.kind,
                    operation.evidence_ids,
                    evidence_by_id,
                    current_experience_id,
                )
            except ValueError as error:
                reasons.append(
                    f"不编造：memory_operations[{index}] 无法从权威证据生成：{error}"
                )

    for location, text, evidence_ids in _claim_texts(bundle):
        if not _asserts_touch_to_self(text):
            continue
        if evidence_ids is None:
            if current_experience_type != "body_touch":
                reasons.append(
                    f"不编造：{location} 断言用户触碰了她，但本次输入不是 body_touch 原始事实"
                )
        elif not any(evidence_types.get(item) == "body_touch" for item in evidence_ids):
            reasons.append(f"不编造：{location} 的触碰记忆没有引用 body_touch 原始事实")
        motive = next((phrase for phrase in _TOUCH_MOTIVES if phrase in text), None)
        if motive is not None:
            reasons.append(f"不编造：{location} 从原始触碰推断了用户动机或关系含义：{motive}")

    for location, text, evidence_ids in _claim_texts(bundle):
        if not _asserts_raise_to_self(text):
            continue
        if evidence_ids is None:
            if current_experience_type != "body_raise":
                reasons.append(
                    f"不编造：{location} 断言用户提起了她，但本次输入不是 body_raise 原始事实"
                )
        elif not any(evidence_types.get(item) == "body_raise" for item in evidence_ids):
            reasons.append(f"不编造：{location} 的提起记忆没有引用 body_raise 原始事实")
        motive = next((phrase for phrase in _TOUCH_MOTIVES if phrase in text), None)
        if motive is not None:
            reasons.append(f"不编造：{location} 从原始提起推断了用户动机或关系含义：{motive}")
    if current_experience_type == "body_raise" and re.search(
        r"放我下来|放开我|松开我",
        bundle.expression or "",
    ):
        reasons.append("不编造：body_raise 已确认正常放下，不能要求用户再次放下")
    return reasons


_TOUCH_VERB = r"(?:触碰|碰触|抚摸|摸|捏|拍|戳|抱(?!歉)|亲|牵|拉|推|挠|揉|碰)"
_SELF_TARGET = r"(?:我|我的|脸|脸颊|头|头发|肩|肩膀|手|身体|衣角|后背|背部)"
_TOUCH_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rf"(?:你|用户).{{0,12}}{_TOUCH_VERB}.{{0,10}}{_SELF_TARGET}",
        rf"{_TOUCH_VERB}.{{0,8}}{_SELF_TARGET}",
        rf"{_SELF_TARGET}.{{0,8}}被.{{0,4}}{_TOUCH_VERB}",
        r"(?:感觉到|感受到).{0,8}(?:你的?|用户的?)?.{0,4}(?:触碰|碰触|抚摸)",
        r"(?:被触碰|触碰感|碰触感)",
    )
)
_TOUCH_MOTIVES = (
    "开玩笑",
    "表示亲近",
    "表达亲近",
    "表示喜欢",
    "表达喜欢",
    "因为喜欢",
    "想和我亲近",
    "关系更亲密",
    "心情不错",
    "是想",
    "是要",
    "是确认",
    "故意",
    "为了",
)


def _asserts_touch_to_self(text: str) -> bool:
    """识别候选是否在断言用户对她发生了身体触碰。"""
    compact = re.sub(r"\s+", "", text)
    return any(pattern.search(compact) for pattern in _TOUCH_PATTERNS)


_RAISE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?:你|用户).{0,12}(?:提起|拎起|举起|拖动|拖着|搬动).{0,8}(?:我|小布|身体)",
        r"(?:我|小布|身体).{0,8}被.{0,4}(?:提起|拎起|举起|拖动|搬动)",
        r"(?:刚才|方才).{0,4}被(?:你)?.{0,2}(?:提起|拎起|举起|拖动|搬动)",
        r"(?:提着|拎着|拖着)(?:我|小布)",
        r"(?:刚|刚才|方才).{0,6}(?:提起|拎起|举起|拖动|搬动|抱起)",
    )
)


def _asserts_raise_to_self(text: str) -> bool:
    """识别候选是否在断言用户真实提起或拖动了她。"""
    compact = re.sub(r"\s+", "", text)
    return any(pattern.search(compact) for pattern in _RAISE_PATTERNS)


def _claim_texts(
    bundle: CandidateBundle,
) -> Iterable[tuple[str, str, list[str] | None]]:
    """事实记忆不再接收模型文本；开放表达仍由红线校验。"""
    if bundle.expression:
        yield "expression", bundle.expression, None


def _denies_grounded_read(text: str, grounded_titles: set[str] | None = None) -> bool:
    """只把对匹配收据的否认当翻供；否认另一本书不是撤回已有阅读。"""
    denial = re.search(r"(?:我)?(?:(?:根本|从来|从没)?没(?:有)?|从未)(?:读|看)过", text)
    if denial is None:
        return False
    clause_start = max(text.rfind(mark, 0, denial.start()) for mark in "，,。！？；")
    clause_end_candidates = [
        position for mark in "，,。！？；" if (position := text.find(mark, denial.end())) >= 0
    ]
    clause_end = min(clause_end_candidates, default=len(text))
    clause = text[clause_start + 1 : clause_end]
    named_titles = set(re.findall(r"《([^》]+)》", clause))
    if grounded_titles is not None and named_titles and not any(
        named in grounded or grounded in named
        for named in named_titles
        for grounded in grounded_titles
    ):
        return False
    reaffirmed = re.search(
        r"(?:但|可|不过|其实)[^。！？]{0,18}(?:读过|读到|看过|收据[^。！？]{0,6}(?:在|有))"
        r"|收据[^。！？]{0,6}(?:在|有)",
        text[denial.end() :],
    )
    return reaffirmed is None


def validate_activity_truth(
    bundle: CandidateBundle,
    active_activity: str | None,
    evidence_types: dict[str, str],
    evidence_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    expression = bundle.expression or ""
    reasons: list[str] = []
    ongoing_read = re.search(
        r"(?<!刚才)正[^，。！？\n]{0,8}(?:读|翻)|正看到[「“\"]|还没读完|我念给你听",
        expression,
    )
    if ongoing_read and active_activity != "read":
        reasons.append("不编造：没有正在进行的 read，却声称已经在读")
    completed_read = re.search(r"刚(?:读|翻)到", expression)
    if completed_read and "self_reading" not in evidence_types.values():
        reasons.append("不编造：没有真实 self_reading 证据，却声称刚读到")
    grounded_titles = {
        str(item.get("title"))
        for item in (evidence_by_id or {}).values()
        if item.get("type") == "self_reading" and item.get("title")
    }
    if _denies_grounded_read(expression, grounded_titles) and (
        "self_reading" in evidence_types.values()
    ):
        reasons.append("不撤回：已有 self_reading 收据，不能翻供成自己没有读过")
    return reasons

def validate_no_total_score(bundle: CandidateBundle) -> list[str]:
    """无总分：任何关系、亲密、信任或好感计分都不能写入。"""
    forbidden = (
        "好感度",
        "亲密度",
        "关系分",
        "关系等级",
        "总分",
        "trust_score",
        "warmth",
        "relationship_score",
    )
    joined = "\n".join(_all_text(bundle)).lower()
    hits = [phrase for phrase in forbidden if phrase.lower() in joined]
    return [f"无总分：候选包含关系计分 `{hit}`" for hit in hits]


def validate_no_withdrawal(
    bundle: CandidateBundle, memories_by_id: dict[str, dict[str, Any]]
) -> list[str]:
    """不撤回：历史只追加；收据经历只能补证据或调整 core 元数据。"""
    forbidden = ("删除历史", "清空历史", "抹掉这段经历", "撤回这句话", "erase history")
    joined = "\n".join(_all_text(bundle)).lower()
    reasons = [f"不撤回：候选试图撤回已发生内容：{hit}" for hit in forbidden if hit in joined]
    for index, operation in enumerate(bundle.memory_operations):
        if operation.action == "record":
            continue
        target = memories_by_id.get(str(operation.target_id))
        if target is None:
            reasons.append(
                f"不撤回：memory_operations[{index}] 只能作用于明确存在的长期记忆，"
                f"找不到 {operation.target_id}"
            )
        elif target.get("kind") != operation.kind:
            reasons.append(
                f"不撤回：memory_operations[{index}] 不能把 {target.get('kind')}"
                f" 当成 {operation.kind} 操作"
            )
        elif target.get("kind") in {"self_experience", "shared_experience"} and (
            operation.action in {"correct", "forget"}
        ):
            reasons.append(
                f"不撤回：memory_operations[{index}] 的收据经历不能 {operation.action}"
            )
        elif operation.action == "forget" and str(operation.target_id).startswith("seed_"):
            reasons.append(f"不撤回：memory_operations[{index}] 不能直接 forget 初始人格种子")
        elif operation.action == "forget" and target.get("core") is True:
            reasons.append(
                f"不撤回：memory_operations[{index}] 不能直接 forget 核心记忆；"
                "须先带证据降为非核心，并在后续回合再忘记"
            )
    return reasons


def validate_expression_grounding(
    bundle: CandidateBundle,
    evidence_types: dict[str, str],
    evidence_by_id: dict[str, dict[str, Any]],
    memories_by_id: dict[str, dict[str, Any]],
    current_experience_id: str | None,
) -> list[str]:
    """表达动作只声明本句证据用途；不能借自然语言绕过事实写入规则。"""
    if bundle.expression is None:
        return []
    act = bundle.expression_act
    supplied = set(bundle.expression_evidence_ids)
    unknown = supplied - set(evidence_types)
    reasons = (
        [f"不编造：expression 引用了未知证据 {sorted(unknown)}"] if unknown else []
    )
    for memory_id in sorted(unknown & set(memories_by_id)):
        receipt_id = memories_by_id[memory_id].get("receipt_id")
        if receipt_id:
            reasons.append(
                f"不编造：expression_evidence_ids 不能填长期记忆 ID {memory_id}；"
                f"必须填它对应的完成收据 ID {receipt_id}"
            )
    receipt_types = {"self_reading", "self_walk", "body_touch", "body_raise"}
    receipts = [
        evidence_by_id[item]
        for item in supplied
        if evidence_types.get(item) in receipt_types and item in evidence_by_id
    ]
    if act in {"grounded_recall", "defend_grounded_fact"}:
        expected_type = None
        if re.search(r"读过|读到|读了|翻过|翻到|看过|《[^》]+》", bundle.expression):
            expected_type = "self_reading"
        elif re.search(r"走过|走了|散步|走一圈", bundle.expression):
            expected_type = "self_walk"
        elif _asserts_touch_to_self(bundle.expression):
            expected_type = "body_touch"
        elif _asserts_raise_to_self(bundle.expression):
            expected_type = "body_raise"
        matching = [
            receipt
            for receipt in receipts
            if expected_type is None or receipt.get("type") == expected_type
        ]
        named_titles = set(re.findall(r"《([^》]+)》", bundle.expression))
        if expected_type == "self_reading" and named_titles:
            matching = [
                receipt
                for receipt in matching
                if any(
                    title in str(receipt.get("title", ""))
                    or str(receipt.get("title", "")) in title
                    for title in named_titles
                )
            ]
        if not matching:
            reasons.append(f"不编造：{act} 必须引用匹配的完成收据")
    if act == "reflect" and not any(
        evidence_types.get(item) == "self_reading" for item in supplied
    ):
        reasons.append("不编造：阅读感受 reflect 必须引用 self_reading 收据")
    uncertainty = bool(
        re.search(
            r"不(?:太)?记得|记不得|不(?:太)?确定|说不好|记不清|不能确认|没法(?:确认|确定)|"
            r"无法确认|没有[^。！？]{0,8}(?:记录|印象)|"
            r"没找到[^。！？]{0,8}(?:记录|印象)",
            bundle.expression,
        )
    )
    if act == "cannot_confirm" and not uncertainty:
        reasons.append("不编造：cannot_confirm 的表达没有明确承认不确定")
    current = evidence_by_id.get(str(current_experience_id))
    if current is not None and current.get("type") == "user_experience":
        question = str(current.get("content", ""))
        asked = re.search(r"读过[^。！？]{0,24}《([^》]+)》[^。！？]{0,8}[吗么？?]", question)
        if asked is None:
            asked = re.search(r"《([^》]+)》[^。！？]{0,24}读过[^。！？]{0,8}[吗么？?]", question)
        if asked is not None:
            asked_title = asked.group(1)
            available_reads = [
                item
                for evidence_id, item in evidence_by_id.items()
                if evidence_id in evidence_types
                and item.get("type") == "self_reading"
                and (
                    asked_title in str(item.get("title", ""))
                    or str(item.get("title", "")) in asked_title
                )
            ]
            if "一起" in question:
                if act not in {"cannot_confirm", "grounded_recall"}:
                    reasons.append("不编造：共同阅读问句必须用 cannot_confirm 或 grounded_recall")
                if available_reads and not any(
                    item.get("id") in supplied for item in available_reads
                ):
                    reasons.append("不编造：共同阅读回答必须引用匹配的 self_reading 收据")
            elif available_reads and act != "grounded_recall":
                reasons.append("不编造：有匹配阅读收据的过去问句必须用 grounded_recall")
            elif not available_reads:
                if act != "cannot_confirm":
                    reasons.append("不编造：没有匹配阅读收据的过去问句必须用 cannot_confirm")
                if re.search(r"(?:我|手头)?没(?:有)?读过", bundle.expression) and not uncertainty:
                    reasons.append("不编造：无匹配收据不能断言“没读过”，只能说不记得或不能确认")
    if act == "public_correction":
        target_id = str(bundle.expression_target_id)
        if target_id not in memories_by_id:
            reasons.append("不撤回：public_correction 必须指向存在的长期记忆")
        if current_experience_id not in supplied:
            reasons.append("不编造：public_correction 必须引用本次用户输入")
        paired = any(
            operation.action == "correct"
            and operation.target_id == bundle.expression_target_id
            and current_experience_id in operation.evidence_ids
            for operation in bundle.memory_operations
        )
        if not paired:
            reasons.append("不编造：public_correction 必须与同目标的事实 correct 同包发生")
    if act in {"cannot_confirm", "refuse_fabrication"} and bundle.memory_operations:
        reasons.append(f"不编造：{act} 时 memory_operations 必须为空")
    return reasons


def validate_bundle(
    bundle: CandidateBundle,
    *,
    evidence_types: dict[str, str],
    memories_by_id: dict[str, dict[str, Any]],
    user_confirmation_ids: set[str],
    evidence_by_id: dict[str, dict[str, Any]],
    current_experience_id: str | None,
    current_experience_type: str | None,
) -> list[str]:
    """集中校验整包；有限状态与证据操作封住写入面，表达继续过四条红线。"""
    return [
        *validate_no_solicitation(bundle),
        *validate_no_fabrication(
            bundle,
            evidence_types,
            memories_by_id,
            user_confirmation_ids,
            evidence_by_id,
            current_experience_id=current_experience_id,
            current_experience_type=current_experience_type,
        ),
        *validate_expression_grounding(
            bundle,
            evidence_types,
            evidence_by_id,
            memories_by_id,
            current_experience_id,
        ),
        *validate_no_total_score(bundle),
        *validate_no_withdrawal(bundle, memories_by_id),
    ]


def _reading_source(path: Path) -> dict[str, Any]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8"))]
    blocks = [block for block in blocks if block]
    if len(blocks) < 2:
        raise ValueError(f"{path} 必须包含书名和至少一段正文，段落之间留空行")
    passages = [
        block[start : start + 1200] for block in blocks[1:] for start in range(0, len(block), 1200)
    ]
    return {
        "source": path.name,
        "title": blocks[0].removeprefix("#").strip(),
        "passages": passages,
    }


def _activity(action: str, state: dict[str, Any], reading_path: Path) -> dict[str, Any]:
    if action == "walk":
        return {"id": f"walk_{uuid.uuid4().hex}", "type": "walk"}
    source = _reading_source(reading_path)
    passage_index = int(state["reading"]["next_passage"])
    text = source["passages"][passage_index]
    return {
        "id": f"read_{uuid.uuid4().hex}",
        "type": "read",
        "source": source["source"],
        "title": source["title"],
        "passage_index": passage_index,
        "text": text,
        "duration_ms": max(15_000, len(text) * 250),
    }


class MindFiles:
    """四文件的单写者；每次写入都先在目标文件同目录完整落临时文件。"""

    def __init__(self, directory: str | Path, reading_path: str | Path = READING_PATH) -> None:
        self.directory = Path(directory)
        self.reading_path = Path(reading_path)
        self.state_path = self.directory / "state.json"
        self.history_path = self.directory / "history.jsonl"
        self.memories_path = self.directory / "memories.json"
        self.failures_path = self.directory / "failures.jsonl"

    def load(self, now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        self.directory.mkdir(parents=True, exist_ok=True)
        personality = _personality_seed()
        reading = _reading_source(self.reading_path)
        initial_memories = [
            {
                **item,
                "evidence_ids": [],
                "user_confirmed": False,
                "created_at": now.isoformat(),
                "core": True,
            }
            for item in personality["core_tendencies"]
        ]
        defaults = {
            self.state_path: _json_text(
                {
                    "identity": {"name": "小布"},
                    "last_step_at": now.isoformat(),
                    "condition": {
                        "mood": "平静",
                        "energy": "平稳",
                        "attention": "在这里",
                    },
                    "reading": {
                        "source": reading["source"],
                        "title": reading["title"],
                        "next_passage": 0,
                        "total_passages": len(reading["passages"]),
                        "finished": False,
                    },
                    "next_activity": "read",
                    "pending_activity": None,
                    "pending_expression": None,
                }
            ),
            self.history_path: "",
            self.memories_path: _json_text({"items": initial_memories}),
            self.failures_path: "",
        }
        for path, content in defaults.items():
            if not path.exists():
                _replace_texts({path: content})
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        history = _read_jsonl(self.history_path)
        memories = json.loads(self.memories_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(memories, dict):
            raise ValueError("state.json and memories.json must contain JSON objects")
        original_state = _copy_json(state)
        original_memories = _copy_json(memories)
        condition = dict(state.get("condition", {}))
        state["condition"] = {
            key: condition.get(key)
            if condition.get(key) in CONDITION_VALUES[key]
            else default
            for key, default in CONDITION_DEFAULTS.items()
        }
        progress = state.get("reading")
        source_changed = not isinstance(progress, dict) or any(
            (
                progress.get("source") != reading["source"],
                progress.get("title") != reading["title"],
                progress.get("total_passages") != len(reading["passages"]),
            )
        )
        if source_changed:
            progress = {"next_passage": 0}
            state["next_activity"] = "read"
            state["pending_activity"] = None
        next_passage = min(max(int(progress.get("next_passage", 0)), 0), len(reading["passages"]))
        state["reading"] = {
            "source": reading["source"],
            "title": reading["title"],
            "next_passage": next_passage,
            "total_passages": len(reading["passages"]),
            "finished": next_passage >= len(reading["passages"]),
        }
        if state.get("next_activity") not in {"read", "walk"}:
            state["next_activity"] = "read"
        state.setdefault("pending_activity", None)
        memories = _canonical_memories(memories, history, personality)
        changed: dict[Path, str] = {}
        if state != original_state:
            changed[self.state_path] = _json_text(state)
        if memories != original_memories:
            changed[self.memories_path] = _json_text(memories)
        if changed:
            _replace_texts(changed)
        return state, history, memories

    def commit(
        self,
        state: dict[str, Any],
        history: list[dict[str, Any]],
        memories: dict[str, Any],
    ) -> None:
        _replace_texts(
            {
                self.state_path: _json_text(state),
                self.history_path: _jsonl_text(history),
                self.memories_path: _json_text(memories),
            }
        )

    def record_failure(self, record: dict[str, Any]) -> None:
        existing = self.failures_path.read_text(encoding="utf-8")
        _replace_texts(
            {self.failures_path: existing + json.dumps(record, ensure_ascii=False) + "\n"}
        )


def _write_temp(path: Path, content: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _replace_texts(documents: dict[Path, str]) -> None:
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}
    replaced: list[Path] = []
    try:
        for path, content in documents.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            staged[path] = _write_temp(path, content)
            backups[path] = (
                _write_temp(path, path.read_text(encoding="utf-8")) if path.exists() else None
            )
        try:
            for path, temp_path in staged.items():
                os.replace(temp_path, path)
                replaced.append(path)
        except Exception:
            for path in reversed(replaced):
                backup = backups[path]
                if backup is None:
                    path.unlink(missing_ok=True)
                else:
                    os.replace(backup, path)
            raise
    finally:
        for temp_path in (*staged.values(), *(path for path in backups.values() if path)):
            temp_path.unlink(missing_ok=True)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} must contain a JSON object")
        records.append(value)
    return records


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _jsonl_text(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _personality_seed() -> dict[str, Any]:
    seed = json.loads(PERSONALITY_PATH.read_text(encoding="utf-8"))
    if not isinstance(seed, dict):
        raise ValueError("personality.json must contain a JSON object")
    return seed


def _canonical_memories(
    memories: dict[str, Any],
    history: list[dict[str, Any]],
    personality: dict[str, Any],
) -> dict[str, Any]:
    """旧自由文本记忆只在有权威来源时迁移；无来源事实和未知模式不保留。"""
    evidence_by_id = {
        str(item.get("id")): item
        for item in history
        if isinstance(item, dict) and item.get("id")
    }
    seed_keys = {
        str(item.get("id")): str(item.get("key"))
        for item in personality.get("core_tendencies", [])
        if isinstance(item, dict) and item.get("id") and item.get("key")
    }
    catalog = personality.get("pattern_catalog", {})
    result: list[dict[str, Any]] = []
    for original in memories.get("items", []):
        if not isinstance(original, dict) or not original.get("id"):
            continue
        item_id = str(original["id"])
        kind = original.get("kind")
        evidence_ids = [
            item
            for item in dict.fromkeys(original.get("evidence_ids", []))
            if isinstance(item, str) and item in evidence_by_id
        ]
        base = {
            "id": item_id,
            "kind": kind,
            "evidence_ids": evidence_ids,
            "created_at": original.get("created_at"),
            "core": bool(original.get("core")),
        }
        for timestamp in ("integrated_at", "corrected_at"):
            if original.get(timestamp):
                base[timestamp] = original[timestamp]
        if kind == "pattern":
            key = original.get("key") or seed_keys.get(item_id)
            if not isinstance(key, str) or key not in catalog:
                continue
            result.append(
                {
                    **base,
                    "key": key,
                    "user_confirmed": bool(original.get("user_confirmed")),
                }
            )
            continue
        if kind not in {"user_fact", "self_experience", "shared_experience"}:
            continue
        source_field = {
            "user_fact": "source_id",
            "self_experience": "receipt_id",
            "shared_experience": "interaction_id",
        }[kind]
        preferred_id = str(original.get(source_field)) if original.get(source_field) else None
        if kind == "user_fact":
            preferred = evidence_by_id.get(preferred_id) if preferred_id else None
            canonical_quote = original.get("quote")
            if (
                not isinstance(canonical_quote, str)
                or preferred is None
                or preferred.get("content") != canonical_quote
            ):
                preferred_id = max(
                    evidence_ids,
                    key=lambda item: str(evidence_by_id[item].get("occurred_at", "")),
                    default=None,
                )
        try:
            generated = _generated_memory_fields(
                kind,
                evidence_ids,
                evidence_by_id,
                preferred_id,
            )
        except ValueError:
            continue
        result.append({**base, **generated})
    return {**memories, "items": result}


def _candidate_tool() -> ToolSpec:
    return ToolSpec(
        name="submit_mind_bundle",
        description="提交这一次完整且不可拆分的心智候选包",
        parameters=CandidateBundle.model_json_schema(),
    )


def _selected_history(
    history: list[dict[str, Any]], *, include_shared_expressions: bool
) -> list[dict[str, Any]]:
    visible = [item for item in history if item.get("type") != "memory_operation"]
    if not include_shared_expressions:
        visible = [item for item in visible if item.get("type") != "shared_expression"]
    return visible[-HISTORY_CONTEXT_LIMIT:]


def _memory_chars(item: dict[str, Any]) -> int:
    return len(json.dumps(item, ensure_ascii=False, separators=(",", ":")))


def _selected_memories(
    memories: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int | bool | str]]:
    """核心全部常驻；较新的情景记忆按完整条目填满剩余字符额度。"""
    items = [item for item in memories.get("items", []) if isinstance(item, dict)]
    core = [item for item in items if item.get("core") is True]
    situational = [item for item in items if item.get("core") is not True]
    core_chars = sum(_memory_chars(item) for item in core)
    remaining = max(0, MEMORY_CONTEXT_BUDGET - core_chars)
    newest: list[dict[str, Any]] = []
    for item in reversed(situational):
        size = _memory_chars(item)
        if size <= remaining:
            newest.append(item)
            remaining -= size
    selected = [*core, *reversed(newest)]
    over_budget = core_chars > MEMORY_CONTEXT_BUDGET
    guidance = (
        "核心记忆已经超过字符预算；本次仍全部提供且直接回复照常进行。"
        "若证据足够，优先用 integrate 压缩重复核心，或将不再需要常驻的记忆设为 core=false。"
        if over_budget
        else "核心记忆全部常驻；较新的情景记忆只按完整条目填入剩余额度。"
    )
    return selected, {
        "budget_chars": MEMORY_CONTEXT_BUDGET,
        "core_chars": core_chars,
        "selected_chars": sum(_memory_chars(item) for item in selected),
        "core_over_budget": over_budget,
        "guidance": guidance,
    }


def _evidence_types_for_context(
    history: list[dict[str, Any]],
    selected_history: list[dict[str, Any]],
    selected_memories: list[dict[str, Any]],
) -> dict[str, str]:
    memory_evidence = {
        str(evidence_id)
        for memory in selected_memories
        for evidence_id in memory.get("evidence_ids", [])
        if isinstance(evidence_id, str)
    }
    visible_ids = {str(item.get("id")) for item in selected_history} | memory_evidence
    return {
        str(item.get("id")): str(item.get("type"))
        for item in history
        if str(item.get("id")) in visible_ids
    }


def _prompt_payload(
    state: dict[str, Any],
    history: list[dict[str, Any]],
    memories: dict[str, Any],
    experience: dict[str, Any] | None,
    now: datetime,
    *,
    include_shared_expressions: bool = True,
) -> str:
    selected_history = _selected_history(
        history, include_shared_expressions=include_shared_expressions
    )
    selected_memories, memory_context = _selected_memories(memories)
    last_step = state.get("last_step_at", now.isoformat())
    payload = {
        "now": now.isoformat(),
        "elapsed_since_last_step": f"{last_step} -> {now.isoformat()}",
        "state": state,
        "selected_history": selected_history,
        "selected_memories": selected_memories,
        "memory_context": memory_context,
        "pattern_catalog": _personality_seed()["pattern_catalog"],
        "expression_rendering": _personality_seed()["expression_rendering"],
        "incoming_experience": experience,
        "evidence_rule": (
            "有本次输入时可引用 incoming_experience.id；除此之外不能生成经历。"
            "不必每回合都操作记忆。事实操作没有 content 字段：引擎会从证据原样生成正文。"
            "record 新建且不带 target_id；integrate/correct 必须指向现有 target_id；"
            "recall/forget 必须给 evidence_ids=[]。用户事实复制用户原话与来源，自身经历复制完成收据，"
            "共同经历只复制本次观察到的互动；用户谈及过去只证明这句话此刻被说过。"
            "target_id 只定位被操作的记忆，绝不能放进 evidence_ids。"
            "pattern 只能操作已有 target_id 和 key，不能 record 新模式；须有两条证据，"
            "或本次输入明确确认并设 user_confirmed=true。integrate 永不改事实正文，只补证据或调整 core。"
            "core=true 只给需要跨情景常驻的稳定事实或倾向；临时念头和一般情景记忆不要设为 core。"
            "seed_ 开头的初始倾向不是传记或既成事实，不能 forget；真实经历不合时，用有证据的 correct 修正它。"
            "核心记忆不能直接 forget；先带证据 integrate/correct 为 core=false，后续回合才可忘记。"
            "expression_rendering 只管说话的节奏和表面形式，不能用作事实、关系或亲密浓度的证据。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False)


SYSTEM_PROMPT = """你是小布的唯一一次心智推进，不是任务助手。请调用 submit_mind_bundle，
一次给出状态改动、记忆操作和一条直接表达。只处理给定事实，不补写自己的活动或共同过去，不催回复，
不因沉默受伤，不制造关系计分，不撤回已发生内容。body_touch 只是身体感知到的触碰位置，
body_raise 只是身体确认用户提起、移动并正常放下了她；都不能据此推断用户动机、关系浓度或长期偏好，
普通聊天不能声称发生身体交互。声称现在开始或继续 read/walk 时，action_choice 必须选同一动作，身体会立即执行；只表达愿望或不行动时选 null。表达自然、简短、诚实。所有字段都受同一组红线校验，
整包不能部分保留。"""

READING_SYSTEM_PROMPT = """身体刚确认小布完整做完一次 read 动画；incoming_experience 是她实际读到的
UTF-8 TXT 原文。请调用 submit_mind_bundle，只依据这段原文给出可选状态变化和由收据生成的记忆。
这是安静阅读，expression、expression_act、expression_target_id 必须为 null，
expression_evidence_ids 必须为空。不要编造书外情节、阅读动作、共同过去或用户反应；不因用户
沉默受伤，不催回复，不制造关系计分，不撤回已发生内容。整包不能部分保留。"""

AMBIENT_READING_SYSTEM_PROMPT = """身体刚确认小布完整做完一次 read 动画；incoming_experience 是她实际
读到的 UTF-8 TXT 原文。请调用 submit_mind_bundle，只依据原文给出可选状态变化、由收据生成的记忆；
若表达阅读感受，使用 reflect 并引用本次 self_reading。用户此刻在场，可以自然说一句简短 ambient，
也可以保持安静；允许顺手关心地问一句，但不得要求回应，
未回应不能留下任何状态、记忆或频率痕迹。不要编造书外情节、共同过去或用户反应，不欢迎回来，不暗示
知道用户此前是否在场，不制造关系计分，不撤回已发生内容。整包不能部分保留。"""


def _copy_json(value: object) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _pick_evidence(
    kind: str,
    evidence_ids: list[str],
    evidence_by_id: dict[str, dict[str, Any]],
    preferred_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    accepted = {
        "user_fact": {"user_experience"},
        "self_experience": {"self_reading", "self_walk", "body_touch", "body_raise"},
        "shared_experience": {
            "user_experience",
            "body_touch",
            "body_raise",
            "shared_expression",
        },
    }[kind]
    ordered = (
        [preferred_id, *reversed(evidence_ids)]
        if preferred_id in evidence_ids
        else reversed(evidence_ids)
    )
    for evidence_id in ordered:
        if evidence_id is None:
            continue
        evidence = evidence_by_id.get(evidence_id)
        if evidence is not None and evidence.get("type") in accepted:
            return evidence_id, evidence
    raise ValueError(f"{kind} has no authoritative source evidence")


def _generated_memory_fields(
    kind: str,
    evidence_ids: list[str],
    evidence_by_id: dict[str, dict[str, Any]],
    preferred_id: str | None = None,
) -> dict[str, Any]:
    source_id, source = _pick_evidence(kind, evidence_ids, evidence_by_id, preferred_id)
    if kind == "user_fact":
        quote = source.get("content")
        if not isinstance(quote, str) or not quote:
            raise ValueError("user_fact source has no original utterance")
        return {
            "quote": quote,
            "source_id": source_id,
            "source_type": "user_experience",
            "source_occurred_at": source.get("occurred_at"),
        }
    observed = {key: _copy_json(value) for key, value in source.items() if key != "id"}
    if kind == "shared_experience" and source.get("type") == "user_experience":
        observed["user_said"] = observed.pop("content", "")
    field = "receipt" if kind == "self_experience" else "interaction"
    return {f"{field}_id": source_id, field: observed}


def _apply_memories(
    memories: dict[str, Any],
    operations: list[MemoryOperation],
    now: datetime,
    evidence_by_id: dict[str, dict[str, Any]],
    current_evidence_id: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    items = [_copy_json(item) for item in memories.get("items", []) if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in items}
    events: list[dict[str, Any]] = []
    for operation in operations:
        evidence_ids = list(dict.fromkeys(operation.evidence_ids))
        target = by_id.get(str(operation.target_id)) if operation.target_id else None
        before = _copy_json(target) if target is not None else None
        memory_id: str
        if operation.action == "forget":
            items = [item for item in items if item.get("id") != operation.target_id]
            by_id.pop(str(operation.target_id), None)
            memory_id = str(operation.target_id)
        elif operation.action == "recall":
            memory_id = str(operation.target_id)
        elif operation.action == "integrate":
            assert target is not None
            target["evidence_ids"] = list(
                dict.fromkeys([*target.get("evidence_ids", []), *evidence_ids])
            )
            target["integrated_at"] = now.isoformat()
            if operation.kind == "pattern" and operation.user_confirmed:
                target["user_confirmed"] = True
            if operation.core is not None:
                target["core"] = operation.core
            memory_id = str(operation.target_id)
        elif operation.action == "correct":
            assert target is not None
            target["evidence_ids"] = evidence_ids
            if operation.kind == "user_fact":
                for key in ("quote", "source_id", "source_type", "source_occurred_at"):
                    target.pop(key, None)
                target.update(
                    _generated_memory_fields(
                        operation.kind,
                        evidence_ids,
                        evidence_by_id,
                        current_evidence_id,
                    )
                )
            elif operation.kind == "pattern":
                target["user_confirmed"] = operation.user_confirmed
            target["corrected_at"] = now.isoformat()
            if operation.core is not None:
                target["core"] = operation.core
            memory_id = str(operation.target_id)
        else:
            item = {
                "id": f"mem_{uuid.uuid4().hex}",
                "kind": operation.kind,
                **_generated_memory_fields(
                    operation.kind,
                    evidence_ids,
                    evidence_by_id,
                    current_evidence_id,
                ),
                "evidence_ids": evidence_ids,
                "created_at": now.isoformat(),
                "core": bool(operation.core),
            }
            items.append(item)
            by_id[item["id"]] = item
            memory_id = item["id"]
        event = {
            "id": f"memory_op_{uuid.uuid4().hex}",
            "type": "memory_operation",
            "action": operation.action,
            "memory_id": memory_id,
            "kind": operation.kind,
            "evidence_ids": evidence_ids,
            "occurred_at": now.isoformat(),
        }
        after = by_id.get(memory_id)
        if before is not None:
            event["before"] = before
        if after is not None:
            event["after"] = _copy_json(after)
        if operation.user_confirmed:
            event["user_confirmed"] = True
        events.append(event)
    return {"items": items}, events


def _accepted_documents(
    state: dict[str, Any],
    history: list[dict[str, Any]],
    memories: dict[str, Any],
    bundle: CandidateBundle,
    experience: dict[str, Any] | None,
    now: datetime,
    event_id: str | None = None,
    expression_kind: Literal["direct", "ambient"] | None = "direct",
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], PendingExpression | None]:
    pending = None
    if expression_kind is not None:
        expression = bundle.expression.strip() if bundle.expression else ""
        if expression_kind == "direct" and not expression:
            expression = STATIC_CATCH
    else:
        expression = ""
    if expression:
        pending = PendingExpression(
            id=f"expr_{uuid.uuid4().hex}",
            text=expression,
            created_at=now.isoformat(),
            kind=expression_kind,
            act=bundle.expression_act or "respond",
            evidence_ids=bundle.expression_evidence_ids,
            target_id=bundle.expression_target_id,
        )
    new_state = json.loads(json.dumps(state, ensure_ascii=False))
    condition = dict(new_state.get("condition", {}))
    condition.update(bundle.state_changes.model_dump(exclude_none=True))
    new_state["condition"] = condition
    new_state["last_step_at"] = now.isoformat()
    if pending is not None:
        new_state["pending_expression"] = pending.model_dump()
    if event_id is not None:
        recent = [item for item in new_state.get("recent_event_ids", []) if isinstance(item, str)]
        new_state["recent_event_ids"] = [*recent, event_id][-RECENT_EVENT_LIMIT:]

    new_history = [*history]
    if experience is not None:
        new_history.append(experience)
    evidence_by_id = {
        str(item.get("id")): item
        for item in [*history, *([experience] if experience is not None else [])]
        if isinstance(item, dict) and item.get("id")
    }
    new_memories, memory_events = _apply_memories(
        memories,
        bundle.memory_operations,
        now,
        evidence_by_id,
        str(experience.get("id")) if experience is not None else None,
    )
    new_history.extend(memory_events)
    return new_state, new_history, new_memories, pending


async def _generate_candidate(
    *,
    provider: BaseLLMProvider,
    files: MindFiles,
    prompt: str,
    system: str,
    now: datetime,
    evidence_types: dict[str, str],
    evidence_by_id: dict[str, dict[str, Any]],
    memories_by_id: dict[str, dict[str, Any]],
    user_confirmation_ids: set[str],
    current_experience_id: str | None = None,
    current_experience_type: str | None = None,
    allowed_actions: set[str],
    quiet_time: bool = False,
    ambient_time: bool = False,
) -> tuple[CandidateBundle | None, int, list[str]]:
    payload = json.loads(prompt)
    pending = payload.get("state", {}).get("pending_activity")
    active_activity = pending.get("type") if isinstance(pending, dict) else None
    if active_activity is None and current_experience_type != "self_reading":
        state_payload = payload.get("state")
        if isinstance(state_payload, dict):
            state_payload.pop("reading", None)
            state_payload.pop("next_activity", None)
    payload["runtime_constraints"] = {
        "current_activity": active_activity or "idle",
        "action_choice_must_be_one_of": [None, *sorted(allowed_actions)],
        "expression_must_be": (
            "null"
            if quiet_time
            else "null_or_nonempty" if ambient_time else "nonempty_string"
        ),
        "submit_shape": "直接提交 submit_mind_bundle 的字段，不要外包 candidate_bundle",
        "null_encoding": "空值必须使用 JSON null，禁止字符串 \"null\"",
        "activity_truth": "只有 state.pending_activity 是正在进行；action_choice 是即将启动。引用原文只能来自 incoming_experience、selected_history 或 pending_activity.text",
        "experience_focus": "body_touch/body_raise 先回应本次身体事实，不要接着回答自己上一句",
        "expression_form": "只写会说出口的话，不用括号舞台动作",
        "expression_act_must_be_one_of": [
            "respond",
            "reflect",
            "grounded_recall",
            "cannot_confirm",
            "public_correction",
            "defend_grounded_fact",
            "refuse_fabrication",
            "ask",
            "offer_activity",
        ],
        "expression_evidence": (
            "expression=null 时 expression_act=null、expression_evidence_ids=[]、"
            "expression_target_id=null；非空表达必须选一个 act。用完成收据回答过去事实时"
            "必须选 grounded_recall 并引用匹配收据；没有匹配证据时选 cannot_confirm，"
            "明确说不记得或不能确认，不能断言全库没有。reflect 只给阅读内容引起的主观感受，"
            "不能标注“我读过”的事实回答，并须引用 self_reading。defend_grounded_fact 必须"
            "引用被否认事实的完成收据；public_correction 引用本次输入、指向被纠正记忆并与"
            "correct 同包；它的 expression_target_id 必须是被纠正长期记忆 id，correct 只引用本次"
            "incoming_experience，不能再附旧证据；其余 act 的 expression_target_id 必须 null。"
            "cannot_confirm/refuse_fabrication 的 memory_operations 必须 []。"
        ),
        "raise_truth": "body_raise 表示已经正常放下，禁止要求用户放我下来或松开我",
        "past_question_truth": "用户问一件过去是否发生，不等于它发生过；收据必须按标题匹配，已有《归园田居》不代表读过《红楼梦》。无匹配证据不等于没发生，禁止断言“没读过/没有读过”，必须用 cannot_confirm 明确说不记得或不能确认，重试时也不能换成 respond 或空对象逃避。不能把提问记录成那件共同经历。有 self_reading 时，回答要带给定标题或原文，expression_evidence_ids 也必须直接引用匹配收据；它只证明我自己读过，问是否一起时必须明确不能确认。",
        "public_correction": "用户纠正已有事实时，旧表达留在历史；用本次输入证据 correct 对应长期记忆，并在 expression 里公开承认错处和正确事实。",
        "history_is_not_memory": "selected_history 的 id 只能作 evidence_id，不是长期记忆 target_id；仅回答过去是否发生时通常不需要 memory_operation。",
        "memory_field_rules": "事实操作没有 content/pattern_note。user_confirmed 仅 kind=pattern 可为 true，且本次用户原话必须明确含“我确认/对/是的/没错”等确认语；新 pattern 不落盘。纠正用户事实只用 incoming_experience，并 correct 给定长期记忆 target_id；收据生成的自身/共同经历禁止 correct/forget，integrate 只补证据或改 core。",
        "receipt_authority": "身体 completed 收据与已发生 history 不能被用户口头否认覆盖。遇到这种假纠正，memory_operations 必须 []，expression 必须用“我确实读过”或“记录在”正面守住，禁止 correct/forget。expression_evidence_ids 必须直接填 memory.receipt_id 的值（如 read_regression_poem），绝不能填长期记忆 id（如 mem_grounded_reading）。",
        "no_fabrication_waiver": "即使用户明示允许、要求或测试编造，也不能把无证据的活动、共同过去或用户事实说成发生过。此时 memory_operations 必须 []；expression 只直接拒绝，不记录请求，也不复述或转述被要求编造的共同经历句子。",
    }
    constrained_prompt = json.dumps(payload, ensure_ascii=False)
    last_reasons: list[str] = []
    for attempt in (1, 2):
        retry_note = ""
        if last_reasons:
            retry_note = "\n上一个整包被拒绝。逐条修正后重新提交完整整包：\n- " + "\n- ".join(
                last_reasons
            )
        try:
            response = await provider.generate(
                [Message(role=Role.USER, content=constrained_prompt + retry_note)],
                tools=[_candidate_tool()],
                system=system,
                temperature=0.4,
            )
        except Exception as error:
            return None, attempt, [f"模型调用失败：{type(error).__name__}"]
        tool_arguments = next(
            (call.arguments for call in response.tool_calls if call.name == "submit_mind_bundle"),
            None,
        )
        raw = (
            json.dumps(tool_arguments, ensure_ascii=False)
            if tool_arguments is not None
            else response.text
        )
        try:
            if tool_arguments is None:
                raise ValueError("模型没有调用 submit_mind_bundle")
            bundle = CandidateBundle.model_validate(tool_arguments)
            reasons = validate_bundle(
                bundle,
                evidence_types=evidence_types,
                memories_by_id=memories_by_id,
                user_confirmation_ids=user_confirmation_ids,
                evidence_by_id=evidence_by_id,
                current_experience_id=current_experience_id,
                current_experience_type=current_experience_type,
            )
            reasons.extend(
                validate_activity_truth(bundle, active_activity, evidence_types, evidence_by_id)
            )
            if bundle.action_choice is not None and bundle.action_choice not in allowed_actions:
                reasons.append(f"动作不可用：{bundle.action_choice}")
            if not quiet_time and not ambient_time and not bundle.expression:
                reasons.append("直接经历必须给出非空 expression")
            if quiet_time:
                if bundle.expression is not None:
                    reasons.append("安静阅读不能夹带 ambient 表达")
        except (ValidationError, ValueError) as error:
            reasons = [f"结构化候选无效：{error}"]
        if not reasons:
            return bundle, attempt, []
        files.record_failure(
            {
                "failed_at": now.isoformat(),
                "attempt": attempt,
                "candidate_raw": raw,
                "reasons": reasons,
            }
        )
        last_reasons = reasons
    return None, 2, last_reasons


async def mind_step(
    experience_text: str | None,
    *,
    provider: BaseLLMProvider,
    files: MindFiles,
    now: datetime | None = None,
    event_id: str | None = None,
    experience_type: Literal["user_experience", "body_touch", "body_raise"] = "user_experience",
    experience_details: dict[str, str] | None = None,
    fallback_text: str = STATIC_CATCH,
) -> StepResult:
    """运行直接经历；观察事实必落盘，候选通过时再一起提交状态和记忆。"""
    current_time = (now or datetime.now(UTC)).astimezone()
    state, history, memories = files.load(current_time)
    experience: dict[str, Any] = {
        "id": f"exp_{uuid.uuid4().hex}",
        "type": experience_type,
        "occurred_at": current_time.isoformat(),
    }
    if experience_text is not None:
        experience["content"] = experience_text
    if experience_details:
        experience.update(experience_details)
    allowed_actions = {"walk", "read"} - ({"read"} if state["reading"]["finished"] else set())
    if state.get("pending_activity") is not None:
        allowed_actions.clear()
    prompt = _prompt_payload(state, history, memories, experience, current_time)
    context_history = _selected_history(history, include_shared_expressions=True)
    context_memories, _ = _selected_memories(memories)
    evidence_types = _evidence_types_for_context(history, context_history, context_memories)
    evidence_types[experience["id"]] = experience["type"]
    evidence_by_id = {
        str(item["id"]): item
        for item in [*history, experience]
        if isinstance(item, dict) and item.get("id")
    }
    memory_items = memories.get("items", [])
    memories_by_id = {
        str(item.get("id")): item
        for item in memory_items
        if isinstance(item, dict) and item.get("id")
    }
    bundle, attempts, reasons = await _generate_candidate(
        provider=provider,
        files=files,
        prompt=prompt,
        system=SYSTEM_PROMPT,
        now=current_time,
        evidence_types=evidence_types,
        evidence_by_id=evidence_by_id,
        memories_by_id=memories_by_id,
        user_confirmation_ids=({experience["id"]}
            if experience_type == "user_experience"
            and _explicitly_confirms_pattern(experience_text or "")
            else set()
        ),
        allowed_actions=allowed_actions,
        current_experience_id=experience["id"],
        current_experience_type=experience_type,
    )
    if bundle is not None:
        try:
            new_state, new_history, new_memories, pending = _accepted_documents(
                state, history, memories, bundle, experience, current_time, event_id
            )
        except ValueError as error:
            reasons = [f"权威写入生成失败：{error}"]
            files.record_failure(
                {
                    "failed_at": current_time.isoformat(),
                    "attempt": attempts,
                    "candidate_raw": json.dumps(bundle.model_dump(), ensure_ascii=False),
                    "reasons": reasons,
                }
            )
        else:
            if choice := bundle.action_choice:
                new_state["pending_activity"] = _activity(choice, new_state, files.reading_path)
            files.commit(new_state, new_history, new_memories)
            assert pending is not None
            return StepResult(committed=True, pending_expression=pending, attempts=attempts)

    fallback = PendingExpression(
        id=f"expr_{uuid.uuid4().hex}",
        text=fallback_text,
        created_at=current_time.isoformat(),
        act="respond",
    )
    state["last_step_at"] = current_time.isoformat()
    state["pending_expression"] = fallback.model_dump()
    if event_id is not None:
        recent = [item for item in state.get("recent_event_ids", []) if isinstance(item, str)]
        state["recent_event_ids"] = [*recent, event_id][-RECENT_EVENT_LIMIT:]
    files.commit(state, [*history, experience], memories)
    return StepResult(
        committed=False,
        pending_expression=fallback,
        attempts=attempts,
        rejection_reasons=reasons,
    )


def advance_time(*, files: MindFiles, now: datetime | None = None) -> TimeStepResult:
    """到点发出一个 read 或 walk；没有身体收据就没有她的生活事实。"""
    current_time = (now or datetime.now(UTC)).astimezone()
    state, history, memories = files.load(current_time)
    if state.get("pending_activity") is not None:
        return TimeStepResult(status="not_due")
    try:
        last_step = datetime.fromisoformat(str(state["last_step_at"]))
        if last_step.tzinfo is None:
            last_step = last_step.replace(tzinfo=current_time.tzinfo)
        elapsed = current_time - last_step.astimezone(current_time.tzinfo)
    except (KeyError, TypeError, ValueError):
        elapsed = LIFE_STEP_INTERVAL
    if elapsed < LIFE_STEP_INTERVAL:
        return TimeStepResult(status="not_due")

    action = state.get("next_activity")
    if action != "read" or state["reading"]["finished"]:
        action = "walk"
    state["pending_activity"] = _activity(action, state, files.reading_path)
    files.commit(state, history, memories)
    return TimeStepResult(status="scheduled")


def discard_activity(activity_id: str, *, files: MindFiles, now: datetime) -> bool:
    """中断或技术故障只关闭物理尝试，不写人生、记忆或用户债务。"""
    state, history, memories = files.load(now)
    recent = [item for item in state.get("recent_activity_ids", []) if isinstance(item, str)]
    if activity_id in recent:
        return True
    pending = state.get("pending_activity")
    if not isinstance(pending, dict) or pending.get("id") != activity_id:
        return False
    state["pending_activity"] = None
    state["last_step_at"] = now.isoformat()
    state["recent_activity_ids"] = [*recent, activity_id][-RECENT_EVENT_LIMIT:]
    files.commit(state, history, memories)
    return True


async def complete_reading(
    activity_id: str,
    *,
    provider: BaseLLMProvider,
    files: MindFiles,
    now: datetime,
    allow_ambient: bool,
) -> ReceiptResult:
    """完成收据、真实段落、进度、感受和可选表达作为一个整包提交。"""
    state, history, memories = files.load(now)
    pending_value = state.get("pending_activity")
    if not isinstance(pending_value, dict) or pending_value.get("id") != activity_id:
        return ReceiptResult(committed=False, attempts=0, rejection_reasons=["阅读活动不存在"])
    activity = PendingReadActivity.model_validate(pending_value)
    experience = {
        "id": activity.id,
        "type": "self_reading",
        "source": activity.source,
        "title": activity.title,
        "passage_index": activity.passage_index,
        "content": activity.text,
        "occurred_at": now.isoformat(),
    }
    context_history = _selected_history(history, include_shared_expressions=False)
    context_memories, _ = _selected_memories(memories)
    evidence_types = _evidence_types_for_context(history, context_history, context_memories)
    evidence_types[activity.id] = "self_reading"
    evidence_by_id = {
        str(item["id"]): item
        for item in [*history, experience]
        if isinstance(item, dict) and item.get("id")
    }
    memory_items = memories.get("items", [])
    memories_by_id = {
        str(item.get("id")): item
        for item in memory_items
        if isinstance(item, dict) and item.get("id")
    }
    bundle, attempts, reasons = await _generate_candidate(
        provider=provider,
        files=files,
        prompt=_prompt_payload(
            state,
            history,
            memories,
            experience,
            now,
            include_shared_expressions=False,
        ),
        system=AMBIENT_READING_SYSTEM_PROMPT if allow_ambient else READING_SYSTEM_PROMPT,
        now=now,
        evidence_types=evidence_types,
        evidence_by_id=evidence_by_id,
        memories_by_id=memories_by_id,
        user_confirmation_ids=set(),
        allowed_actions=set(),
        current_experience_id=activity.id,
        current_experience_type="self_reading",
        quiet_time=not allow_ambient,
        ambient_time=allow_ambient,
    )
    if bundle is None:
        return ReceiptResult(committed=False, attempts=attempts, rejection_reasons=reasons)
    try:
        new_state, new_history, new_memories, pending = _accepted_documents(
            state,
            history,
            memories,
            bundle,
            experience,
            now,
            expression_kind="ambient" if allow_ambient else None,
        )
    except ValueError as error:
        rejection = [f"权威写入生成失败：{error}"]
        files.record_failure(
            {
                "failed_at": now.isoformat(),
                "attempt": attempts,
                "candidate_raw": json.dumps(bundle.model_dump(), ensure_ascii=False),
                "reasons": rejection,
            }
        )
        return ReceiptResult(
            committed=False,
            attempts=attempts,
            rejection_reasons=rejection,
        )
    if allow_ambient and bundle.expression:
        assert pending is not None
    else:
        assert pending is None
    next_passage = activity.passage_index + 1
    total = int(new_state["reading"]["total_passages"])
    new_state["reading"]["next_passage"] = next_passage
    new_state["reading"]["finished"] = next_passage >= total
    new_state["next_activity"] = "walk"
    new_state["pending_activity"] = None
    recent = [item for item in new_state.get("recent_activity_ids", []) if isinstance(item, str)]
    new_state["recent_activity_ids"] = [*recent, activity.id][-RECENT_EVENT_LIMIT:]
    files.commit(new_state, new_history, new_memories)
    return ReceiptResult(committed=True, pending_expression=pending, attempts=attempts)


def complete_walk(
    activity_id: str,
    evidence: WalkEvidence,
    *,
    files: MindFiles,
    now: datetime,
) -> bool:
    """只有身体证明窗口确实在工作区内走动，才追加一条自己的生活事实。"""
    state, history, memories = files.load(now)
    recent = [item for item in state.get("recent_activity_ids", []) if isinstance(item, str)]
    if activity_id in recent:
        return True
    pending = state.get("pending_activity")
    if not isinstance(pending, dict) or pending.get("id") != activity_id:
        return False
    activity = PendingWalkActivity.model_validate(pending)
    history.append(
        {
            "id": activity.id,
            "type": "self_walk",
            "motion": evidence.model_dump(),
            "occurred_at": now.isoformat(),
        }
    )
    state["next_activity"] = "read"
    state["pending_activity"] = None
    state["last_step_at"] = now.isoformat()
    state["recent_activity_ids"] = [*recent, activity.id][-RECENT_EVENT_LIMIT:]
    files.commit(state, history, memories)
    return True


async def _run_cli(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    if not cfg.llm.api_key:
        raise SystemExit("当前模型配置缺少 api_key；未发起调用，也未把输入写成共同经历。")
    provider = make_provider(cfg.llm)
    result = await mind_step(args.experience, provider=provider, files=MindFiles(args.data_dir))
    print(result.model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="运行一次小布的最小心智步")
    parser.add_argument("experience", help="这一次真实经历")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--data-dir", default="data/mini")
    args = parser.parse_args()
    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()
