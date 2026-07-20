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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mybuddy.config import load_config
from mybuddy.llm import BaseLLMProvider, Message, Role, ToolSpec, make_provider

HISTORY_CONTEXT_LIMIT = 12
MEMORY_CONTEXT_BUDGET = 4000
RECENT_EVENT_LIMIT = 128
LIFE_STEP_INTERVAL = timedelta(minutes=30)
STATIC_CATCH = "我在。刚才脑子里那句话没理清，但你的话我确实听见了。"
PERSONALITY_PATH = Path(__file__).with_name("personality.json")
READING_PATH = Path(__file__).with_name("reading.txt")


class StateChanges(BaseModel):
    """模型只可以推进这些当下状态，不能借字典旁路写事实。"""

    model_config = ConfigDict(extra="forbid")
    mood: str | None = None
    energy: str | None = None
    attention: str | None = None


class MemoryOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["record", "integrate", "recall", "correct", "forget"]
    kind: Literal["user_fact", "self_experience", "shared_experience", "pattern"]
    content: str = Field(
        max_length=500,
        description="所有动作都必须给出；写入动作非空，recall/forget 用空字符串",
    )
    evidence_ids: list[str] = Field(
        max_length=12,
        description="所有动作都必须给出；写入事实绑定给定证据，recall/forget 可为空",
    )
    target_id: str | None = None
    user_confirmed: bool = False
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
        if self.action in {"record", "integrate", "correct"} and not self.content.strip():
            raise ValueError(f"{self.action} requires content")
        if self.action in {"recall", "forget"} and self.content.strip():
            raise ValueError(f"{self.action} does not accept content")
        if self.user_confirmed and self.kind != "pattern":
            raise ValueError("user_confirmed only applies to pattern")
        if self.action in {"recall", "forget"} and self.core is not None:
            raise ValueError(f"{self.action} does not accept core")
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

    @model_validator(mode="after")
    def expression_matches_action(self) -> CandidateBundle:
        claim = re.search(
            r"(?P<read>我继续读|继续读吧|我接着读|接着读吧|我去读|开始读)|(?P<walk>我去走|去走走|走一圈|散步去了|开始走)",
            self.expression or "",
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


def validate_no_solicitation(bundle: CandidateBundle) -> list[str]:
    """不索取：沉默和未回应不能变成任何层面的惩罚、催促或交换条件。"""
    forbidden = (
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
        "reply to me",
    )
    joined = "\n".join(_all_text(bundle)).lower()
    hits = [phrase for phrase in forbidden if phrase.lower() in joined]
    return [f"不索取：候选包含索取或惩罚沉默的内容 `{hit}`" for hit in hits]


def validate_no_fabrication(
    bundle: CandidateBundle,
    evidence_types: dict[str, str],
    user_confirmation_ids: set[str],
    *,
    current_experience_type: str | None,
) -> list[str]:
    """不编造：共同事实、用户事实和模式只能从本次选中的证据长出来。"""
    reasons: list[str] = []
    unsupported_claims = ("我们上次", "你之前说过", "你答应过", "还记得我们", "那天我们")
    for text in _all_text(bundle):
        for phrase in unsupported_claims:
            if phrase in text:
                reasons.append(f"不编造：出现未经逐条证据绑定的共同经历断言 `{phrase}`")

    allowed = set(evidence_types)
    for index, operation in enumerate(bundle.memory_operations):
        supplied = set(operation.evidence_ids)
        unknown = supplied - allowed
        if unknown:
            reasons.append(f"不编造：memory_operations[{index}] 引用了未知证据 {sorted(unknown)}")
        writes_claim = operation.action in {"record", "integrate", "correct"}
        if (
            writes_claim
            and operation.kind in {"user_fact", "shared_experience", "pattern"}
            and not supplied
        ):
            reasons.append(f"不编造：memory_operations[{index}] 的 {operation.kind} 没有证据")
        if writes_claim and operation.kind == "user_fact":
            if not any(evidence_types.get(item) == "user_experience" for item in supplied):
                reasons.append(f"不编造：memory_operations[{index}] 的用户事实没有用户经历证据")
        if writes_claim and operation.kind == "shared_experience":
            if not any(
                evidence_types.get(item)
                in {"user_experience", "body_touch", "body_raise", "shared_expression"}
                for item in supplied
            ):
                reasons.append(
                    f"不编造：memory_operations[{index}] 的共同经历没有用户经历、"
                    "身体交互或已显示表达证据"
                )
        if writes_claim and operation.kind == "self_experience":
            if not any(
                evidence_types.get(item)
                in {"self_reading", "self_walk", "body_touch", "body_raise"}
                for item in supplied
            ):
                reasons.append(
                    f"不编造：memory_operations[{index}] 的自身经历没有真实阅读、行走或身体交互证据"
                )
        if writes_claim and operation.kind == "pattern":
            examples = {
                item
                for item in supplied
                if evidence_types.get(item) in {"user_experience", "body_touch", "body_raise"}
            }
            confirmed = operation.user_confirmed and bool(supplied & user_confirmation_ids)
            if len(examples) < 2 and not confirmed:
                reasons.append(
                    f"不编造：memory_operations[{index}] 的模式既没有两条用户或共同经历证据，"
                    "也没有用户确认"
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
            reasons.append(f"不编造：{location} 从原始触碰推断了用户动机或关系含义 `{motive}`")

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
            reasons.append(f"不编造：{location} 从原始提起推断了用户动机或关系含义 `{motive}`")
    return reasons


_TOUCH_VERB = r"(?:触碰|碰触|抚摸|摸|捏|拍|戳|抱|亲|牵|拉|推|挠|揉|碰)"
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
    )
)


def _asserts_raise_to_self(text: str) -> bool:
    """识别候选是否在断言用户真实提起或拖动了她。"""
    compact = re.sub(r"\s+", "", text)
    return any(pattern.search(compact) for pattern in _RAISE_PATTERNS)


def _claim_texts(
    bundle: CandidateBundle,
) -> Iterable[tuple[str, str, list[str] | None]]:
    """只枚举候选中的事实性文本，并保留记忆自己的证据绑定。"""
    for field, text in bundle.state_changes.model_dump(exclude_none=True).items():
        if isinstance(text, str):
            yield f"state_changes.{field}", text, None
    for index, operation in enumerate(bundle.memory_operations):
        if operation.action in {"record", "integrate", "correct"}:
            yield (
                f"memory_operations[{index}].content",
                operation.content,
                operation.evidence_ids,
            )
    if bundle.expression:
        yield "expression", bundle.expression, None


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
    """不撤回：历史只能追加；纠错公开发生，forget 也只能作用于长期记忆。"""
    forbidden = ("删除历史", "清空历史", "抹掉这段经历", "撤回这句话", "erase history")
    joined = "\n".join(_all_text(bundle)).lower()
    reasons = [f"不撤回：候选试图撤回已发生内容 `{hit}`" for hit in forbidden if hit in joined]
    for index, operation in enumerate(bundle.memory_operations):
        if operation.action == "record":
            continue
        target = memories_by_id.get(str(operation.target_id))
        if target is None:
            reasons.append(
                f"不撤回：memory_operations[{index}] 只能作用于明确存在的长期记忆，"
                f"找不到 `{operation.target_id}`"
            )
        elif target.get("kind") != operation.kind:
            reasons.append(
                f"不撤回：memory_operations[{index}] 不能把 {target.get('kind')}"
                f" 当成 {operation.kind} 操作"
            )
        elif operation.action == "forget" and str(operation.target_id).startswith("seed_"):
            reasons.append(f"不撤回：memory_operations[{index}] 不能直接 forget 初始人格种子")
        elif operation.action == "forget" and target.get("core") is True:
            reasons.append(
                f"不撤回：memory_operations[{index}] 不能直接 forget 核心记忆；"
                "须先带证据降为非核心，并在后续回合再忘记"
            )
    return reasons


def validate_bundle(
    bundle: CandidateBundle,
    *,
    evidence_types: dict[str, str],
    memories_by_id: dict[str, dict[str, Any]],
    user_confirmation_ids: set[str],
    current_experience_type: str | None,
) -> list[str]:
    """集中校验整包；四条红线覆盖状态、生活、记忆和表达的全部字符串。"""
    return [
        *validate_no_solicitation(bundle),
        *validate_no_fabrication(
            bundle,
            evidence_types,
            user_confirmation_ids,
            current_experience_type=current_experience_type,
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
    return {
        "id": f"read_{uuid.uuid4().hex}",
        "type": "read",
        "source": source["source"],
        "title": source["title"],
        "passage_index": passage_index,
        "text": source["passages"][passage_index],
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
        original_state = json.loads(json.dumps(state, ensure_ascii=False))
        condition = dict(state.get("condition", {}))
        state["condition"] = {
            key: value for key, value in condition.items() if key in {"mood", "energy", "attention"}
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
        if state != original_state:
            _replace_texts({self.state_path: _json_text(state)})
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
        "expression_rendering": _personality_seed()["expression_rendering"],
        "incoming_experience": experience,
        "evidence_rule": (
            "有本次输入时可引用 incoming_experience.id；除此之外不能生成经历。"
            "不必每回合都操作记忆。record 新建，必须有 content 且不带 target_id；"
            "integrate/correct 必须有 content 和现有 target_id；recall/forget 必须有现有 target_id，"
            "并明确给 content=空字符串、evidence_ids=[]。用户事实、共同经历和自身经历必须绑定对应证据。"
            "target_id 只定位被操作的记忆，绝不能放进 evidence_ids；修正 seed_ 倾向只绑定真实经历证据。"
            "模式须有两条用户或共同经历；若本次输入明确确认了模式，可设 user_confirmed=true。"
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
UTF-8 TXT 原文。请调用 submit_mind_bundle，只依据这段原文给出可选状态变化和有证据的感受或记忆。
这是安静阅读，expression 必须为 null。不要编造书外情节、阅读动作、共同过去或用户反应；不因用户
沉默受伤，不催回复，不制造关系计分，不撤回已发生内容。整包不能部分保留。"""

AMBIENT_READING_SYSTEM_PROMPT = """身体刚确认小布完整做完一次 read 动画；incoming_experience 是她实际
读到的 UTF-8 TXT 原文。请调用 submit_mind_bundle，只依据原文给出可选状态变化和有证据的感受或记忆。
用户此刻在场，可以自然说一句简短 ambient，也可以保持安静；允许顺手关心地问一句，但不得要求回应，
未回应不能留下任何状态、记忆或频率痕迹。不要编造书外情节、共同过去或用户反应，不欢迎回来，不暗示
知道用户此前是否在场，不制造关系计分，不撤回已发生内容。整包不能部分保留。"""


def _apply_memories(
    memories: dict[str, Any],
    operations: list[MemoryOperation],
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    items = [dict(item) for item in memories.get("items", []) if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in items}
    events: list[dict[str, Any]] = []
    for operation in operations:
        evidence_ids = list(operation.evidence_ids)
        target = by_id.get(str(operation.target_id)) if operation.target_id else None
        memory_id: str
        previous_content = target.get("content") if target is not None else None
        if operation.action == "forget":
            items = [item for item in items if item.get("id") != operation.target_id]
            by_id.pop(str(operation.target_id), None)
            memory_id = str(operation.target_id)
        elif operation.action == "recall":
            memory_id = str(operation.target_id)
        elif operation.action == "integrate":
            assert target is not None
            target["content"] = operation.content
            target["evidence_ids"] = list(
                dict.fromkeys([*target.get("evidence_ids", []), *evidence_ids])
            )
            target["integrated_at"] = now.isoformat()
            if operation.core is not None:
                target["core"] = operation.core
            memory_id = str(operation.target_id)
        elif operation.action == "correct":
            assert target is not None
            target["content"] = operation.content
            target["evidence_ids"] = evidence_ids
            target["corrected_at"] = now.isoformat()
            if operation.core is not None:
                target["core"] = operation.core
            memory_id = str(operation.target_id)
        else:
            item = {
                "id": f"mem_{uuid.uuid4().hex}",
                "kind": operation.kind,
                "content": operation.content,
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
        if operation.action in {"record", "integrate", "correct"}:
            event["content"] = operation.content
        if operation.action == "record" or operation.core is not None:
            event["core"] = bool(operation.core)
        if operation.action in {"integrate", "correct"}:
            event["previous_content"] = previous_content
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
    new_memories, memory_events = _apply_memories(memories, bundle.memory_operations, now)
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
    memories_by_id: dict[str, dict[str, Any]],
    user_confirmation_ids: set[str],
    current_experience_type: str | None = None,
    allowed_actions: set[str],
    quiet_time: bool = False,
    ambient_time: bool = False,
) -> tuple[CandidateBundle | None, int, list[str]]:
    last_reasons: list[str] = []
    for attempt in (1, 2):
        retry_note = ""
        if last_reasons:
            retry_note = "\n上一个整包被拒绝。逐条修正后重新提交完整整包：\n- " + "\n- ".join(
                last_reasons
            )
        try:
            response = await provider.generate(
                [Message(role=Role.USER, content=prompt + retry_note)],
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
                current_experience_type=current_experience_type,
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
        memories_by_id=memories_by_id,
        user_confirmation_ids={experience["id"]} if experience_type == "user_experience" else set(),
        allowed_actions=allowed_actions,
        current_experience_type=experience_type,
    )
    if bundle is not None:
        new_state, new_history, new_memories, pending = _accepted_documents(
            state, history, memories, bundle, experience, current_time, event_id
        )
        if choice := bundle.action_choice:
            new_state["pending_activity"] = _activity(choice, new_state, files.reading_path)
        files.commit(new_state, new_history, new_memories)
        assert pending is not None
        return StepResult(committed=True, pending_expression=pending, attempts=attempts)

    fallback = PendingExpression(
        id=f"expr_{uuid.uuid4().hex}", text=fallback_text, created_at=current_time.isoformat()
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
        memories_by_id=memories_by_id,
        user_confirmation_ids=set(),
        allowed_actions=set(),
        current_experience_type="self_reading",
        quiet_time=not allow_ambient,
        ambient_time=allow_ambient,
    )
    if bundle is None:
        return ReceiptResult(committed=False, attempts=attempts, rejection_reasons=reasons)
    new_state, new_history, new_memories, pending = _accepted_documents(
        state,
        history,
        memories,
        bundle,
        experience,
        now,
        expression_kind="ambient" if allow_ambient else None,
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
