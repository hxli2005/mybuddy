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
MEMORY_CONTEXT_LIMIT = 8
RECENT_EVENT_LIMIT = 128
LIFE_STEP_INTERVAL = timedelta(minutes=30)
STATIC_CATCH = "我在。刚才脑子里那句话没理清，但你的话我确实听见了。"


class StateChanges(BaseModel):
    """模型只可以推进这些当下状态，不能借字典旁路写事实。"""

    model_config = ConfigDict(extra="forbid")

    mood: str | None = None
    energy: str | None = None
    attention: str | None = None
    current_activity: str | None = None
    baseline: Literal["idle", "read", "write", "gaze", "sleep"] | None = None


class LifeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=300)


class MemoryOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["record", "integrate", "recall", "correct", "forget"]
    kind: Literal["user_fact", "self_experience", "shared_experience", "pattern"]
    content: str = Field(default="", max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    target_id: str | None = None
    user_confirmed: bool = False

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
        return self


class CandidateBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_changes: StateChanges
    life_events: list[LifeEvent] = Field(max_length=3)
    memory_operations: list[MemoryOperation] = Field(max_length=5)
    expression: str | None = Field(default=None, max_length=500)


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
    status: Literal["not_due", "advanced", "failed"]
    attempts: int = 0
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
    allow_life_events: bool,
) -> list[str]:
    """不编造：共同事实、用户事实和模式只能从本次选中的证据长出来。"""
    reasons: list[str] = []
    if bundle.life_events and not allow_life_events:
        reasons.append("不编造：直接经历不能生成生活事件；生活只能由时间推进产生")

    unsupported_claims = ("我们上次", "你之前说过", "你答应过", "还记得我们", "那天我们")
    for text in _all_text(bundle):
        for phrase in unsupported_claims:
            if phrase in text:
                reasons.append(f"不编造：出现未经逐条证据绑定的共同经历断言 `{phrase}`")

    local_life = {f"life:{i}" for i in range(len(bundle.life_events))}
    allowed = set(evidence_types) | local_life
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
                evidence_types.get(item) in {"user_experience", "body_touch", "shared_expression"}
                for item in supplied
            ):
                reasons.append(
                    f"不编造：memory_operations[{index}] 的共同经历没有用户经历、"
                    "身体触碰或已显示表达证据"
                )
        if writes_claim and operation.kind == "self_experience":
            if not any(
                item.startswith("life:") or evidence_types.get(item) == "self_life"
                for item in supplied
            ):
                reasons.append(f"不编造：memory_operations[{index}] 的自身经历没有生活事件证据")
        if writes_claim and operation.kind == "pattern":
            examples = {
                item
                for item in supplied
                if evidence_types.get(item) in {"user_experience", "body_touch"}
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


def _claim_texts(
    bundle: CandidateBundle,
) -> Iterable[tuple[str, str, list[str] | None]]:
    """只枚举候选中的事实性文本，并保留记忆自己的证据绑定。"""
    for field, text in bundle.state_changes.model_dump(exclude_none=True).items():
        if isinstance(text, str):
            yield f"state_changes.{field}", text, None
    for index, event in enumerate(bundle.life_events):
        yield f"life_events[{index}].content", event.content, None
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
    return reasons


def validate_bundle(
    bundle: CandidateBundle,
    *,
    evidence_types: dict[str, str],
    memories_by_id: dict[str, dict[str, Any]],
    user_confirmation_ids: set[str],
    current_experience_type: str | None,
    allow_life_events: bool,
) -> list[str]:
    """集中校验整包；四条红线覆盖状态、生活、记忆和表达的全部字符串。"""
    return [
        *validate_no_solicitation(bundle),
        *validate_no_fabrication(
            bundle,
            evidence_types,
            user_confirmation_ids,
            current_experience_type=current_experience_type,
            allow_life_events=allow_life_events,
        ),
        *validate_no_total_score(bundle),
        *validate_no_withdrawal(bundle, memories_by_id),
    ]


class MindFiles:
    """四文件的单写者；每次写入都先在目标文件同目录完整落临时文件。"""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.state_path = self.directory / "state.json"
        self.history_path = self.directory / "history.jsonl"
        self.memories_path = self.directory / "memories.json"
        self.failures_path = self.directory / "failures.jsonl"

    def load(self, now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        self.directory.mkdir(parents=True, exist_ok=True)
        defaults = {
            self.state_path: _json_text(
                {
                    "identity": {"name": "小布"},
                    "last_step_at": now.isoformat(),
                    "condition": {
                        "mood": "平静",
                        "energy": "平稳",
                        "attention": "在这里",
                        "current_activity": "刚刚安静下来",
                        "baseline": "idle",
                    },
                    "pending_expression": None,
                }
            ),
            self.history_path: "",
            self.memories_path: _json_text({"items": []}),
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
    memory_items = memories.get("items", [])
    selected_memories = (
        memory_items[-MEMORY_CONTEXT_LIMIT:] if isinstance(memory_items, list) else []
    )
    last_step = state.get("last_step_at", now.isoformat())
    payload = {
        "now": now.isoformat(),
        "elapsed_since_last_step": f"{last_step} -> {now.isoformat()}",
        "state": state,
        "selected_history": selected_history,
        "selected_memories": selected_memories,
        "incoming_experience": experience,
        "evidence_rule": (
            "有本次输入时可引用 incoming_experience.id；仅时间推进可产生新生活事件，"
            "并按顺序引用 life:0、life:1。"
            "record 新建且不带 target_id；integrate 合并、recall 取回、correct 纠正、forget 遗忘"
            "都必须把现有记忆 ID 写入 target_id。用户事实、共同经历和自身经历必须绑定对应证据。"
            "模式须有两条用户或共同经历；若本次输入明确确认了模式，可设 user_confirmed=true。"
            "临时念头不要提交为长期记忆。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False)


SYSTEM_PROMPT = """你是小布的唯一一次心智推进，不是任务助手。请调用 submit_mind_bundle，
一次给出状态改动、记忆操作和一条直接表达；life_events 必须是空数组，直接经历不能补写生活。
只处理给定事实。不要补写共同过去，不催回复，
不因沉默受伤，不制造关系计分，不撤回已发生内容。body_touch 只是身体感知到的触碰位置，
不能据此推断用户动机、关系浓度或长期偏好；普通聊天不能声称发生了触碰。
表达自然、简短、诚实。所有字段都受同一组红线校验，整包不能部分保留。"""

TIME_SYSTEM_PROMPT = """你是小布的一次安静时间推进，不是任务助手。请调用 submit_mind_bundle，
根据 last_step 到 now 真正经过的时间，一次给出状态改动、她自己刚发生的一至三个微小具体生活事件，
以及有直接生活事件证据的记忆操作。state_changes.baseline 必须是 idle/read/write/gaze/sleep 之一，
与她此刻正在做的事一致。这不是主动搭话，expression 必须为 null。不要逐分钟补写，不补写共同过去，
不因用户沉默受伤，不催回复，不制造关系计分，不撤回已发生内容。整包不能部分保留。"""

AMBIENT_TIME_SYSTEM_PROMPT = """你是小布的一次安静时间推进，不是任务助手。请调用 submit_mind_bundle，
根据 last_step 到 now 真正经过的时间，一次给出状态改动、她自己刚发生的一至三个微小具体生活事件，
以及有直接生活事件证据的记忆操作。state_changes.baseline 必须是 idle/read/write/gaze/sleep 之一，
与她此刻正在做的事一致。用户此刻在场，所以你可以把刚发生的生活或当前活动自然说成一句简短的
ambient 表达，也可以令 expression 为 null 保持安静；不要问问题、索取回应、欢迎回来或暗示你知道
用户此前是否在场和是否回应。baseline 为 sleep 时 expression 必须为 null。不要补写共同过去，
不因用户沉默受伤，不制造关系计分，不撤回已发生内容。整包不能部分保留。"""


def _apply_memories(
    memories: dict[str, Any],
    operations: list[MemoryOperation],
    now: datetime,
    evidence_aliases: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    items = [dict(item) for item in memories.get("items", []) if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in items}
    events: list[dict[str, Any]] = []
    for operation in operations:
        evidence_ids = [evidence_aliases.get(item, item) for item in operation.evidence_ids]
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
            memory_id = str(operation.target_id)
        elif operation.action == "correct":
            assert target is not None
            target["content"] = operation.content
            target["evidence_ids"] = evidence_ids
            target["corrected_at"] = now.isoformat()
            memory_id = str(operation.target_id)
        else:
            item = {
                "id": f"mem_{uuid.uuid4().hex}",
                "kind": operation.kind,
                "content": operation.content,
                "evidence_ids": evidence_ids,
                "created_at": now.isoformat(),
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
    evidence_aliases: dict[str, str] = {}
    for index, event in enumerate(bundle.life_events):
        life_id = f"life_{uuid.uuid4().hex}"
        evidence_aliases[f"life:{index}"] = life_id
        new_history.append(
            {
                "id": life_id,
                "type": "self_life",
                "content": event.content,
                "occurred_at": now.isoformat(),
            }
        )
    new_memories, memory_events = _apply_memories(
        memories, bundle.memory_operations, now, evidence_aliases
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
    memories_by_id: dict[str, dict[str, Any]],
    user_confirmation_ids: set[str],
    current_experience_type: str | None = None,
    allow_life_events: bool = False,
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
                allow_life_events=allow_life_events,
            )
            if quiet_time:
                if bundle.expression is not None:
                    reasons.append("时间推进不能夹带尚未进入 ambient 阶段的表达")
                if not bundle.life_events:
                    reasons.append("时间推进必须包含至少一件当场发生的自身生活事件")
                if bundle.state_changes.baseline is None:
                    reasons.append("时间推进必须明确身体持续呈现的 baseline")
            if ambient_time:
                if not bundle.life_events:
                    reasons.append("时间推进必须包含至少一件当场发生的自身生活事件")
                if bundle.state_changes.baseline is None:
                    reasons.append("时间推进必须明确身体持续呈现的 baseline")
                if bundle.state_changes.baseline == "sleep" and bundle.expression is not None:
                    reasons.append("睡眠 baseline 不能生成 ambient 表达")
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
    experience_type: Literal["user_experience", "body_touch"] = "user_experience",
    experience_details: dict[str, str] | None = None,
    fallback_text: str = STATIC_CATCH,
) -> StepResult:
    """运行一个直接经历；候选通过才把经历、生活、状态和记忆一起提交。"""
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
    prompt = _prompt_payload(state, history, memories, experience, current_time)
    context_history = _selected_history(history, include_shared_expressions=True)
    evidence_types = {str(item.get("id")): str(item.get("type")) for item in context_history}
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
        current_experience_type=experience_type,
    )
    if bundle is not None:
        new_state, new_history, new_memories, pending = _accepted_documents(
            state, history, memories, bundle, experience, current_time, event_id
        )
        files.commit(new_state, new_history, new_memories)
        assert pending is not None
        return StepResult(committed=True, pending_expression=pending, attempts=attempts)

    fallback = PendingExpression(
        id=f"expr_{uuid.uuid4().hex}", text=fallback_text, created_at=current_time.isoformat()
    )
    return StepResult(
        committed=False,
        pending_expression=fallback,
        attempts=attempts,
        rejection_reasons=reasons,
    )


async def advance_time(
    *,
    provider: BaseLLMProvider,
    files: MindFiles,
    now: datetime | None = None,
    allow_ambient: bool = False,
) -> TimeStepResult:
    """只在真实时间跨过间隔后推进一次生活；在场时可顺带产生稀疏 ambient。"""
    current_time = (now or datetime.now(UTC)).astimezone()
    state, history, memories = files.load(current_time)
    try:
        last_step = datetime.fromisoformat(str(state["last_step_at"]))
        if last_step.tzinfo is None:
            last_step = last_step.replace(tzinfo=current_time.tzinfo)
        elapsed = current_time - last_step.astimezone(current_time.tzinfo)
    except (KeyError, TypeError, ValueError):
        elapsed = LIFE_STEP_INTERVAL
    if elapsed < LIFE_STEP_INTERVAL:
        return TimeStepResult(status="not_due")

    context_history = _selected_history(history, include_shared_expressions=False)
    evidence_types = {str(item.get("id")): str(item.get("type")) for item in context_history}
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
            None,
            current_time,
            include_shared_expressions=False,
        ),
        system=AMBIENT_TIME_SYSTEM_PROMPT if allow_ambient else TIME_SYSTEM_PROMPT,
        now=current_time,
        evidence_types=evidence_types,
        memories_by_id=memories_by_id,
        user_confirmation_ids=set(),
        allow_life_events=True,
        quiet_time=not allow_ambient,
        ambient_time=allow_ambient,
    )
    if bundle is None:
        return TimeStepResult(status="failed", attempts=attempts, rejection_reasons=reasons)
    new_state, new_history, new_memories, pending = _accepted_documents(
        state,
        history,
        memories,
        bundle,
        None,
        current_time,
        expression_kind="ambient" if allow_ambient else None,
    )
    if allow_ambient and bundle.expression:
        assert pending is not None
    else:
        assert pending is None
    files.commit(new_state, new_history, new_memories)
    return TimeStepResult(status="advanced", attempts=attempts)


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
