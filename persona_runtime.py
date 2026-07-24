"""A local memory boundary for a model-hosted continuing persona.
The host supplies language; this module admits evidenced memories, preserves their
append-only history, and assembles compact context in plain JSON for one writer.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

CONTEXT_BYTE_BUDGET = 3_200
MEMORY_KINDS = {"user_fact", "shared_experience", "persona_experience", "pattern"}
RECEIPT_TYPES = {"persona_experience"}


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _fields(
    operation: dict[str, Any],
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    missing = required - operation.keys()
    extra = operation.keys() - required - optional
    if missing:
        raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"unexpected fields: {', '.join(sorted(extra))}")


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
    if not isinstance(values, list) or not values:
        raise ValueError("evidence_ids must be a non-empty list")
    if any(not isinstance(value, str) for value in values):
        raise ValueError("evidence_ids must contain strings")
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
                history_by_id[event_id]["type"] not in RECEIPT_TYPES for event_id in evidence_ids
            ):
                raise ValueError("persona_experience requires persona experience receipts")

        return {
            "action": action,
            "kind": kind,
            "content": content,
            "core": core,
            "evidence_ids": evidence_ids,
        }

    if action == "correct":
        _fields(operation, {"action", "memory_id", "content", "evidence_quote"})
        memory_id = _text(operation, "memory_id")
        content = _text(operation, "content")
        if memory_id not in memories_by_id:
            raise ValueError(f"memory does not exist: {memory_id}")
        quote = _current_quote(operation, interaction)
        if content != quote:
            raise ValueError("corrected content must preserve the evidence quote")
        return {
            "action": action,
            "memory_id": memory_id,
            "content": content,
            "evidence_ids": [current_event_id],
        }

    if action == "forget":
        _fields(operation, {"action", "memory_id", "evidence_quote"})
        memory_id = _text(operation, "memory_id")
        if memory_id not in memories_by_id:
            raise ValueError(f"memory does not exist: {memory_id}")
        _current_quote(operation, interaction)
        return {
            "action": action,
            "memory_id": memory_id,
            "evidence_ids": [current_event_id],
        }

    raise ValueError("action must be record, correct, or forget")


def _apply_operation(
    operation: dict[str, Any],
    memories: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
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
        event = {
            **memory,
            "id": _identifier("evt"),
            "type": "memory_recorded",
            "at": at,
            "memory_id": memory_id,
        }
    else:
        memory_id = operation["memory_id"]
        index = next(index for index, memory in enumerate(memories) if memory["id"] == memory_id)
        previous = memories[index]
        if operation["action"] == "correct":
            memory = {
                **previous,
                "content": operation["content"],
                "evidence_ids": [*previous["evidence_ids"], *operation["evidence_ids"]],
                "updated_at": at,
            }
            memories[index] = memory
            event = {
                "id": _identifier("evt"),
                "type": "memory_corrected",
                "at": at,
                "memory_id": memory_id,
                "previous_content": previous["content"],
                "content": memory["content"],
                "evidence_ids": operation["evidence_ids"],
            }
        else:
            memories.pop(index)
            event = {
                "id": _identifier("evt"),
                "type": "memory_forgotten",
                "at": at,
                "memory_id": memory_id,
                "kind": previous["kind"],
                "content": previous["content"],
                "evidence_ids": operation["evidence_ids"],
            }
    history.append(event)
    return memory_id, event


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
    state, history, memories, failures = _load_store(directory)
    current_event = {
        "id": _identifier("evt"),
        "type": "interaction",
        "at": datetime.now(UTC).isoformat(),
        "content": interaction,
    }
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
            failures.append(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "interaction_id": current_event["id"],
                    "operation": raw_operation,
                    "reason": reason,
                }
            )
            rejected.append({"index": index, "reason": reason})
            continue

        memory_id, event = _apply_operation(operation, memories, history)
        history_by_id[event["id"]] = event
        memories_by_id = {memory["id"]: memory for memory in memories}
        accepted.append({"index": index, "memory_id": memory_id})

    _save_store(directory, state, history, memories, failures)
    return {
        "interaction_id": current_event["id"],
        "accepted": accepted,
        "rejected": rejected,
    }


def recall(
    query: str,
    data_dir: str | Path = "data/persona",
) -> list[dict[str, Any]]:
    """Return active memories containing a case-insensitive literal substring."""
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")
    memories = _read_json(Path(data_dir) / "memories.json", [])
    needle = query.casefold()
    return [dict(memory) for memory in memories if needle in memory["content"].casefold()]


def persona_context(
    data_dir: str | Path = "data/persona",
    constitution_path: str | Path | None = None,
) -> str:
    """Assemble constitution, state, core memories, and recent memories."""
    path = Path(constitution_path) if constitution_path else Path(__file__).with_name("persona.md")
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
    if len(encoded) <= CONTEXT_BYTE_BUDGET:
        return context
    return encoded[:CONTEXT_BYTE_BUDGET].decode("utf-8", errors="ignore")


def _memory_lines(memories: Any) -> str:
    lines = [
        f"- [{memory['id']}/{memory['kind']}] {memory['content']} "
        f"(evidence: {', '.join(memory['evidence_ids'])})"
        for memory in memories
    ]
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


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/persona"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("context")
    recall_parser = commands.add_parser("recall")
    recall_parser.add_argument("query")
    remember_parser = commands.add_parser("remember")
    remember_parser.add_argument("interaction")
    remember_parser.add_argument("operations", help="JSON array of memory operations")
    arguments = parser.parse_args()

    if arguments.command == "context":
        print(persona_context(arguments.data_dir))
    elif arguments.command == "recall":
        print(json.dumps(recall(arguments.query, arguments.data_dir), ensure_ascii=False, indent=2))
    else:
        operations = json.loads(arguments.operations)
        print(json.dumps(remember(arguments.interaction, operations, arguments.data_dir), indent=2))


if __name__ == "__main__":
    _main()
