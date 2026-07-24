"""A single-writer memory boundary for a model-hosted continuing persona."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock
from mcp.server.fastmcp import FastMCP

CONTEXT_BYTE_BUDGET = 3_200
MEMORY_KINDS = {"user_fact", "shared_experience", "persona_experience", "pattern"}


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _fields(
    operation: dict[str, Any],
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    missing = required - operation.keys()
    extra = operation.keys() - required - optional
    for fields, label in ((missing, "missing"), (extra, "unexpected")):
        if fields:
            raise ValueError(f"{label} fields: {', '.join(sorted(fields))}")


def _text(operation: dict[str, Any], field: str) -> str:
    value = operation[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _event_ids(
    operation: dict[str, Any],
    history_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    values = operation.get("evidence_ids", [])
    valid = isinstance(values, list) and values and all(isinstance(value, str) for value in values)
    if not valid:
        raise ValueError("evidence_ids must be a non-empty list of strings")
    if len(values) != len(set(values)):
        raise ValueError("evidence_ids must be unique")
    missing = [value for value in values if value not in history_by_id]
    if missing:
        raise ValueError(f"evidence does not exist: {', '.join(missing)}")
    return values


def _current_quote(operation: dict[str, Any], interaction: str) -> str:
    quote = _text(operation, "evidence_quote")
    if quote not in interaction:
        raise ValueError("evidence_quote is not in the current interaction")
    return quote


def _validate_operation(
    raw_operation: object,
    interaction: str,
    current_event_id: str,
    history_by_id: dict[str, dict[str, Any]],
    memories_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(raw_operation, dict):
        raise ValueError("operation must be an object")
    operation = dict(raw_operation)
    action = operation.get("action")

    if action == "record":
        _fields(
            operation,
            {"action", "kind", "content"},
            {"core", "evidence_quote", "evidence_ids"},
        )
        kind = _text(operation, "kind")
        content = _text(operation, "content")
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind: {kind}")
        core = operation.get("core", False)
        if not isinstance(core, bool):
            raise ValueError("core must be a boolean")

        if kind in {"user_fact", "shared_experience"}:
            quote = _current_quote(operation, interaction)
            if content != quote:
                raise ValueError(f"{kind} content must preserve the evidence quote")
            if "evidence_ids" in operation:
                raise ValueError(f"{kind} must cite the current interaction")
            evidence_ids = [current_event_id]
        else:
            if "evidence_quote" in operation:
                raise ValueError(f"{kind} must cite recorded event receipts")
            evidence_ids = _event_ids(operation, history_by_id)
            if kind == "persona_experience" and any(
                history_by_id[event_id]["type"] != "persona_experience" for event_id in evidence_ids
            ):
                raise ValueError("persona_experience requires persona experience receipts")
        operation["core"] = core
        operation["evidence_ids"] = evidence_ids
        operation.pop("evidence_quote", None)
        return operation

    if action in {"correct", "forget"}:
        required = {"action", "memory_id", "evidence_quote"}
        if action == "correct":
            required.add("content")
        _fields(operation, required)
        memory_id = _text(operation, "memory_id")
        if memory_id not in memories_by_id:
            raise ValueError(f"memory does not exist: {memory_id}")
        quote = _current_quote(operation, interaction)
        if action == "correct" and _text(operation, "content") != quote:
            raise ValueError("corrected content must preserve the evidence quote")
        operation.pop("evidence_quote")
        operation["evidence_ids"] = [current_event_id]
        return operation

    raise ValueError("action must be record, correct, or forget")


def _event(event_type: str, at: str, **fields: Any) -> dict[str, Any]:
    return {**fields, "id": _identifier("evt"), "type": event_type, "at": at}


def _apply_operation(
    operation: dict[str, Any],
    memories: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> str:
    at = datetime.now(UTC).isoformat()
    if operation["action"] == "record":
        memory_id = _identifier("mem")
        memory = {
            "id": memory_id,
            "kind": operation["kind"],
            "content": operation["content"],
            "evidence_ids": operation["evidence_ids"],
            "core": operation["core"],
            "created_at": at,
            "updated_at": at,
        }
        memories.append(memory)
        event = _event("memory_recorded", at, **memory, memory_id=memory_id)
    else:
        memory_id = operation["memory_id"]
        index = next(index for index, memory in enumerate(memories) if memory["id"] == memory_id)
        if operation["action"] == "correct":
            memory = memories[index]
            previous_content = memory["content"]
            memory["content"] = operation["content"]
            memory["evidence_ids"] += operation["evidence_ids"]
            memory["updated_at"] = at
            event = _event(
                "memory_corrected",
                at,
                memory_id=memory_id,
                previous_content=previous_content,
                content=memory["content"],
                evidence_ids=operation["evidence_ids"],
            )
        else:
            memory = memories.pop(index)
            event = _event(
                "memory_forgotten",
                at,
                memory_id=memory_id,
                kind=memory["kind"],
                content=memory["content"],
                evidence_ids=operation["evidence_ids"],
            )
    history.append(event)
    return memory_id


def remember(
    interaction: str,
    operations: list[object],
    data_dir: str | Path = "data/persona",
) -> dict[str, Any]:
    """Apply independently validated memory operations from one real interaction."""
    if not isinstance(interaction, str) or not interaction.strip():
        raise ValueError("interaction must be a non-empty string")
    if not isinstance(operations, list):
        raise ValueError("operations must be a list")

    directory = Path(data_dir)
    with FileLock(directory / ".writer.lock", timeout=0):
        state, history, memories, failures = _load_store(directory)
        current_event = _event("interaction", datetime.now(UTC).isoformat(), content=interaction)
        history.append(current_event)
        history_by_id = {event["id"]: event for event in history}
        memories_by_id = {memory["id"]: memory for memory in memories}
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for index, raw_operation in enumerate(operations):
            try:
                operation = _validate_operation(
                    raw_operation,
                    interaction,
                    current_event["id"],
                    history_by_id,
                    memories_by_id,
                )
            except ValueError as error:
                reason = str(error)
                failure = {
                    "at": datetime.now(UTC).isoformat(),
                    "interaction_id": current_event["id"],
                    "operation": raw_operation,
                    "reason": reason,
                }
                failures.append(failure)
                rejected.append({"index": index, "reason": reason})
                continue

            memory_id = _apply_operation(operation, memories, history)
            memories_by_id = {memory["id"]: memory for memory in memories}
            accepted.append({"index": index, "memory_id": memory_id})

        _save_store(directory, state, history, memories, failures)
        return {"interaction_id": current_event["id"], "accepted": accepted, "rejected": rejected}


def recall(query: str, data_dir: str | Path = "data/persona") -> list[dict[str, Any]]:
    """Return active memories containing a case-insensitive literal substring."""
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")
    memories = _read_json(Path(data_dir) / "memories.json", [])
    needle = query.casefold()
    return [dict(memory) for memory in memories if needle in memory["content"].casefold()]


def persona_context(data_dir: str | Path = "data/persona") -> str:
    """Assemble constitution, state, core memories, and recent memories."""
    path = Path(data_dir) / "constitution.md"
    if not path.exists():
        path = Path(__file__).with_name("persona.md")
    constitution = path.read_text(encoding="utf-8").strip()
    directory = Path(data_dir)
    state = _read_json(directory / "state.json", {"current": {}})
    memories = _read_json(directory / "memories.json", [])
    core = [memory for memory in memories if memory["core"]]
    recent = [memory for memory in memories if not memory["core"]][-12:]
    blocks = [
        "# Persona Constitution\n" + constitution,
        "# Current State\n" + json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        "# Core Memories\n" + _memory_lines(core),
        "# Recent Memories\n" + _memory_lines(reversed(recent)),
    ]
    context = "\n\n".join(blocks)
    encoded = context.encode("utf-8")
    return (
        context
        if len(encoded) <= CONTEXT_BYTE_BUDGET
        else encoded[:CONTEXT_BYTE_BUDGET].decode("utf-8", errors="ignore")
    )


def _memory_lines(memories: Any) -> str:
    lines = (
        f"- [{memory['id']}/{memory['kind']}] {memory['content']} "
        f"(evidence: {', '.join(memory['evidence_ids'])})"
        for memory in memories
    )
    return "\n".join(lines) or "(none)"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _load_store(
    directory: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        _read_json(directory / "state.json", {"current": {}}),
        _read_jsonl(directory / "history.jsonl"),
        _read_json(directory / "memories.json", []),
        _read_jsonl(directory / "failures.jsonl"),
    )


def _replace_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def _save_store(
    directory: Path,
    state: dict[str, Any],
    history: list[dict[str, Any]],
    memories: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    compact = {"ensure_ascii": False, "separators": (",", ":")}
    _replace_text(directory / "state.json", json.dumps(state, **compact) + "\n")
    _replace_text(
        directory / "history.jsonl",
        "".join(json.dumps(event, **compact) + "\n" for event in history),
    )
    _replace_text(directory / "memories.json", json.dumps(memories, **compact) + "\n")
    _replace_text(
        directory / "failures.jsonl",
        "".join(json.dumps(failure, **compact) + "\n" for failure in failures),
    )


def initialize_persona(data_dir: str | Path, persona_name: str, user_name: str) -> None:
    """Create a named persona without inventing memories."""
    if not persona_name.strip() or not user_name.strip():
        raise ValueError("persona_name and user_name must be non-empty strings")
    directory = Path(data_dir)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("persona data already exists")
    base = Path(__file__).with_name("persona.md").read_text(encoding="utf-8").strip()
    identity = f"\n\nYour chosen name is {persona_name}.\nAddress the user as {user_name} unless they ask otherwise.\n"
    _replace_text(directory / "constitution.md", base + identity)
    state = {"persona_name": persona_name, "user_name": user_name, "current": {}}
    _save_store(directory, state, [], [], [])


def _serve_mcp(data_dir: Path) -> None:
    server = FastMCP(
        "persona-runtime",
        instructions="Use these tools only for personal and shared memory, never project facts.",
    )

    @server.tool(name="persona_context")
    def load_persona_context() -> str:
        """Load identity, current state, and evidenced personal memories."""
        return persona_context(data_dir)

    @server.tool(name="recall")
    def find_memories(query: str) -> list[dict[str, Any]]:
        """Read personal memories by literal substring without side effects."""
        return recall(query, data_dir)

    @server.tool(name="remember")
    def store_memories(interaction: str, operations: list[object]) -> dict[str, Any]:
        """Store personal memories from an exact current-conversation excerpt.
        record uses action, kind, content, and evidence_quote for current user facts
        or shared experiences, or evidence_ids for persona experiences and patterns.
        correct uses action, memory_id, content, and evidence_quote; forget omits
        content. Never store project facts or fabricated past; copy quotes verbatim.
        """
        return remember(interaction, operations, data_dir)

    server.run(transport="stdio")


def _default_data_dir() -> Path:
    configured = os.environ.get("PERSONA_DATA_DIR")
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    return Path(configured) if configured else project / "data" / "persona"


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=_default_data_dir())
    commands = parser.add_subparsers(dest="command", required=True)
    init_parser = commands.add_parser("init")
    init_parser.add_argument("--persona-name", required=True)
    init_parser.add_argument("--user-name", required=True)
    commands.add_parser("context")
    commands.add_parser("mcp")
    arguments = parser.parse_args()

    if arguments.command == "init":
        initialize_persona(arguments.data_dir, arguments.persona_name, arguments.user_name)
    elif arguments.command == "context":
        print(persona_context(arguments.data_dir))
    else:
        _serve_mcp(arguments.data_dir)


if __name__ == "__main__":
    _main()
