"""小布的最小心智步：一个候选整包，四个本地文件。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mybuddy.config import load_config
from mybuddy.llm import BaseLLMProvider, Message, Role, ToolSpec, make_provider

HISTORY_CONTEXT_LIMIT = 12
MEMORY_CONTEXT_LIMIT = 8
STATIC_CATCH = "我在。刚才脑子里那句话没理清，但你的话我确实听见了。"


class StateChanges(BaseModel):
    """模型只可以推进这些当下状态，不能借字典旁路写事实。"""

    model_config = ConfigDict(extra="forbid")

    mood: str | None = None
    energy: str | None = None
    attention: str | None = None
    current_activity: str | None = None


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

    @model_validator(mode="after")
    def require_target_for_change(self) -> MemoryOperation:
        if self.action in {"correct", "forget"} and not self.target_id:
            raise ValueError(f"{self.action} requires target_id")
        if self.action not in {"forget", "recall"} and not self.content.strip():
            raise ValueError(f"{self.action} requires content")
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


class StepResult(BaseModel):
    committed: bool
    pending_expression: PendingExpression
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
    bundle: CandidateBundle, evidence_types: dict[str, str]
) -> list[str]:
    """不编造：共同事实、用户事实和模式只能从本次选中的证据长出来。"""
    reasons: list[str] = []
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
        if operation.kind in {"user_fact", "shared_experience", "pattern"} and not supplied:
            reasons.append(f"不编造：memory_operations[{index}] 的 {operation.kind} 没有证据")
        if operation.kind == "self_experience" and operation.action in {"record", "integrate"}:
            if not any(item.startswith("life:") for item in supplied):
                reasons.append(f"不编造：memory_operations[{index}] 的自身经历没有生活事件证据")
        if operation.kind == "pattern" and operation.action in {"record", "integrate", "correct"}:
            examples = {
                item
                for item in supplied
                if evidence_types.get(item) in {"user_experience", "shared_experience"}
            }
            if len(examples) < 2:
                reasons.append(
                    f"不编造：memory_operations[{index}] 的模式少于两条用户或共同经历证据"
                )
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


def validate_no_withdrawal(bundle: CandidateBundle, memory_ids: set[str]) -> list[str]:
    """不撤回：历史只能追加；纠错公开发生，forget 也只能作用于长期记忆。"""
    forbidden = ("删除历史", "清空历史", "抹掉这段经历", "撤回这句话", "erase history")
    joined = "\n".join(_all_text(bundle)).lower()
    reasons = [f"不撤回：候选试图撤回已发生内容 `{hit}`" for hit in forbidden if hit in joined]
    for index, operation in enumerate(bundle.memory_operations):
        if operation.action in {"correct", "forget"} and operation.target_id not in memory_ids:
            reasons.append(
                f"不撤回：memory_operations[{index}] 只能修改明确存在的长期记忆，"
                f"找不到 `{operation.target_id}`"
            )
    return reasons


def validate_bundle(
    bundle: CandidateBundle,
    *,
    evidence_types: dict[str, str],
    memory_ids: set[str],
) -> list[str]:
    """集中校验整包；四条红线覆盖状态、生活、记忆和表达的全部字符串。"""
    return [
        *validate_no_solicitation(bundle),
        *validate_no_fabrication(bundle, evidence_types),
        *validate_no_total_score(bundle),
        *validate_no_withdrawal(bundle, memory_ids),
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
        _replace_texts({self.failures_path: existing + json.dumps(record, ensure_ascii=False) + "\n"})


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
            backups[path] = _write_temp(path, path.read_text(encoding="utf-8")) if path.exists() else None
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


def _prompt_payload(
    state: dict[str, Any],
    history: list[dict[str, Any]],
    memories: dict[str, Any],
    experience: dict[str, Any],
    now: datetime,
) -> str:
    selected_history = history[-HISTORY_CONTEXT_LIMIT:]
    memory_items = memories.get("items", [])
    selected_memories = memory_items[-MEMORY_CONTEXT_LIMIT:] if isinstance(memory_items, list) else []
    last_step = state.get("last_step_at", now.isoformat())
    payload = {
        "now": now.isoformat(),
        "elapsed_since_last_step": f"{last_step} -> {now.isoformat()}",
        "state": state,
        "selected_history": selected_history,
        "selected_memories": selected_memories,
        "incoming_experience": experience,
        "evidence_rule": (
            "本次输入可引用 incoming_experience.id；新生活事件按顺序引用 life:0、life:1。"
            "用户事实、共同经历、模式必须带 evidence_ids；模式至少两条用户或共同经历。"
            "correct/forget 必须把现有记忆 ID 写入 target_id，并用 evidence_ids 绑定纠错依据。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False)


SYSTEM_PROMPT = """你是小布的唯一一次心智推进，不是任务助手。请调用 submit_mind_bundle，
一次给出状态改动、最多三个属于她自己的小生活事件、记忆操作和一条直接表达。
只处理给定事实与从 last_step 到 now 的朴素时间推进。不要补写共同过去，不催回复，
不因沉默受伤，不制造关系计分，不撤回已发生内容。生活事件必须是她自己的、微小且具体；
表达自然、简短、诚实。所有字段都受同一组红线校验，整包不能部分保留。"""


def _parse_candidate(tool_arguments: dict[str, Any] | None) -> tuple[str, CandidateBundle]:
    if tool_arguments is None:
        raise ValueError("模型没有调用 submit_mind_bundle")
    raw = json.dumps(tool_arguments, ensure_ascii=False)
    return raw, CandidateBundle.model_validate(tool_arguments)


def _apply_memories(
    memories: dict[str, Any],
    operations: list[MemoryOperation],
    now: datetime,
) -> dict[str, Any]:
    items = [dict(item) for item in memories.get("items", []) if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in items}
    for operation in operations:
        if operation.action == "recall":
            continue
        if operation.action == "forget":
            items = [item for item in items if item.get("id") != operation.target_id]
            by_id.pop(str(operation.target_id), None)
            continue
        if operation.action == "correct":
            target = by_id[str(operation.target_id)]
            target["content"] = operation.content
            target["evidence_ids"] = operation.evidence_ids
            target["corrected_at"] = now.isoformat()
            continue
        item = {
            "id": f"mem_{uuid.uuid4().hex}",
            "kind": operation.kind,
            "content": operation.content,
            "evidence_ids": operation.evidence_ids,
            "created_at": now.isoformat(),
        }
        items.append(item)
        by_id[item["id"]] = item
    return {"items": items}


def _accepted_documents(
    state: dict[str, Any],
    history: list[dict[str, Any]],
    memories: dict[str, Any],
    bundle: CandidateBundle,
    experience: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], PendingExpression]:
    expression = bundle.expression.strip() if bundle.expression else STATIC_CATCH
    pending = PendingExpression(
        id=f"expr_{uuid.uuid4().hex}", text=expression, created_at=now.isoformat()
    )
    new_state = json.loads(json.dumps(state, ensure_ascii=False))
    condition = dict(new_state.get("condition", {}))
    condition.update(bundle.state_changes.model_dump(exclude_none=True))
    new_state["condition"] = condition
    new_state["last_step_at"] = now.isoformat()
    new_state["pending_expression"] = pending.model_dump()

    new_history = [*history, experience]
    for index, event in enumerate(bundle.life_events):
        new_history.append(
            {
                "id": f"life_{uuid.uuid4().hex}",
                "candidate_evidence_id": f"life:{index}",
                "type": "self_life",
                "content": event.content,
                "occurred_at": now.isoformat(),
            }
        )
    new_memories = _apply_memories(memories, bundle.memory_operations, now)
    return new_state, new_history, new_memories, pending


async def mind_step(
    experience_text: str,
    *,
    provider: BaseLLMProvider,
    files: MindFiles,
    now: datetime | None = None,
) -> StepResult:
    """运行一个直接经历；候选通过才把经历、生活、状态和记忆一起提交。"""
    current_time = (now or datetime.now(UTC)).astimezone()
    state, history, memories = files.load(current_time)
    experience = {
        "id": f"exp_{uuid.uuid4().hex}",
        "type": "user_experience",
        "content": experience_text,
        "occurred_at": current_time.isoformat(),
    }
    prompt = _prompt_payload(state, history, memories, experience, current_time)
    evidence_types = {
        str(item.get("id")): str(item.get("type")) for item in history[-HISTORY_CONTEXT_LIMIT:]
    }
    evidence_types[experience["id"]] = experience["type"]
    memory_items = memories.get("items", [])
    memory_ids = {
        str(item.get("id")) for item in memory_items if isinstance(item, dict) and item.get("id")
    }
    last_reasons: list[str] = []

    for attempt in (1, 2):
        retry_note = ""
        if last_reasons:
            retry_note = "\n上一个整包被拒绝。逐条修正后重新提交完整整包：\n- " + "\n- ".join(last_reasons)
        try:
            response = await provider.generate(
                [Message(role=Role.USER, content=prompt + retry_note)],
                tools=[_candidate_tool()],
                system=SYSTEM_PROMPT,
                temperature=0.4,
            )
        except Exception as error:
            fallback = PendingExpression(
                id=f"expr_{uuid.uuid4().hex}",
                text=STATIC_CATCH,
                created_at=current_time.isoformat(),
            )
            return StepResult(
                committed=False,
                pending_expression=fallback,
                attempts=attempt,
                rejection_reasons=[f"模型调用失败：{type(error).__name__}"],
            )
        tool_arguments = None
        for call in response.tool_calls:
            if call.name == "submit_mind_bundle":
                tool_arguments = call.arguments
                break
        raw = response.text
        try:
            raw, bundle = _parse_candidate(tool_arguments)
            reasons = validate_bundle(bundle, evidence_types=evidence_types, memory_ids=memory_ids)
        except (ValidationError, json.JSONDecodeError, ValueError) as error:
            reasons = [f"结构化候选无效：{error}"]
        if reasons:
            files.record_failure(
                {
                    "failed_at": current_time.isoformat(),
                    "attempt": attempt,
                    "candidate_raw": raw,
                    "reasons": reasons,
                }
            )
            last_reasons = reasons
            continue

        new_state, new_history, new_memories, pending = _accepted_documents(
            state, history, memories, bundle, experience, current_time
        )
        files.commit(new_state, new_history, new_memories)
        return StepResult(committed=True, pending_expression=pending, attempts=attempt)

    fallback = PendingExpression(
        id=f"expr_{uuid.uuid4().hex}", text=STATIC_CATCH, created_at=current_time.isoformat()
    )
    return StepResult(
        committed=False,
        pending_expression=fallback,
        attempts=2,
        rejection_reasons=last_reasons,
    )


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
