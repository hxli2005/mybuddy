import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import persona_runtime
from persona_runtime import initialize_persona, persona_context, recall, remember


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_forged_evidence_is_rejected_without_blocking_a_true_memory(tmp_path: Path) -> None:
    forged = {
        "action": "record",
        "kind": "pattern",
        "content": "The user always agrees.",
        "evidence_ids": ["evt_never_happened"],
    }
    true_memory = {
        "action": "record",
        "kind": "user_fact",
        "content": "I prefer tea.",
        "evidence_quote": "I prefer tea.",
    }

    result = remember("I prefer tea.", [forged, true_memory], tmp_path)

    assert [item["index"] for item in result["accepted"]] == [1]
    assert result["rejected"] == [
        {"index": 0, "reason": "evidence does not exist: evt_never_happened"}
    ]
    memories = json.loads((tmp_path / "memories.json").read_text(encoding="utf-8"))
    assert [memory["content"] for memory in memories] == ["I prefer tea."]
    failures = read_jsonl(tmp_path / "failures.jsonl")
    assert failures[0]["operation"] == forged
    assert failures[0]["reason"] == result["rejected"][0]["reason"]


def test_correction_and_forgetting_leave_the_historical_truth_intact(tmp_path: Path) -> None:
    recorded = remember(
        "My favorite color is blue.",
        [
            {
                "action": "record",
                "kind": "user_fact",
                "content": "My favorite color is blue.",
                "evidence_quote": "My favorite color is blue.",
            }
        ],
        tmp_path,
    )
    memory_id = recorded["accepted"][0]["memory_id"]
    remember(
        "Correction: my favorite color is green.",
        [
            {
                "action": "correct",
                "memory_id": memory_id,
                "content": "my favorite color is green.",
                "evidence_quote": "my favorite color is green.",
            }
        ],
        tmp_path,
    )
    remember(
        "Please forget my favorite color.",
        [
            {
                "action": "forget",
                "memory_id": memory_id,
                "evidence_quote": "forget my favorite color",
            }
        ],
        tmp_path,
    )

    assert json.loads((tmp_path / "memories.json").read_text(encoding="utf-8")) == []
    history = read_jsonl(tmp_path / "history.jsonl")
    changes = [event for event in history if event["type"].startswith("memory_")]
    assert [event["type"] for event in changes] == [
        "memory_recorded",
        "memory_corrected",
        "memory_forgotten",
    ]
    assert changes[0]["content"] == "My favorite color is blue."
    assert changes[1]["previous_content"] == "My favorite color is blue."
    assert changes[1]["content"] == "my favorite color is green."
    assert changes[2]["content"] == "my favorite color is green."


def test_recall_is_a_literal_read_with_zero_side_effects(tmp_path: Path) -> None:
    remember(
        "I collect Blue pottery.",
        [
            {
                "action": "record",
                "kind": "user_fact",
                "content": "I collect Blue pottery.",
                "evidence_quote": "I collect Blue pottery.",
            }
        ],
        tmp_path,
    )
    store_files = ("state.json", "history.jsonl", "memories.json", "failures.jsonl")
    before = {name: (tmp_path / name).read_bytes() for name in store_files}

    found = recall("blue pot", tmp_path)

    assert [memory["content"] for memory in found] == ["I collect Blue pottery."]
    assert {name: (tmp_path / name).read_bytes() for name in store_files} == before


def test_atomic_replacement_preserves_the_previous_file_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.json"
    target.write_text('{"current":"safe"}\n', encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(persona_runtime.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        persona_runtime._replace_text(target, '{"current":"partial"}\n')

    assert target.read_text(encoding="utf-8") == '{"current":"safe"}\n'


def test_silence_creates_no_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="interaction must be a non-empty string"):
        remember("", [], tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_relationship_score_fields_have_no_place_in_the_world(tmp_path: Path) -> None:
    result = remember(
        "We had a good conversation.",
        [
            {
                "action": "record",
                "kind": "shared_experience",
                "content": "We had a good conversation.",
                "evidence_quote": "We had a good conversation.",
                "relationship_score": 10,
            }
        ],
        tmp_path,
    )

    assert result["accepted"] == []
    assert result["rejected"] == [{"index": 0, "reason": "unexpected fields: relationship_score"}]
    assert json.loads((tmp_path / "memories.json").read_text(encoding="utf-8")) == []


def test_persona_context_contains_each_layer_within_the_injection_budget(
    tmp_path: Path,
) -> None:
    result = remember(
        "You can call me River. We chose that name together.",
        [
            {
                "action": "record",
                "kind": "user_fact",
                "content": "You can call me River.",
                "evidence_quote": "You can call me River.",
                "core": True,
            },
            {
                "action": "record",
                "kind": "shared_experience",
                "content": "We chose that name together.",
                "evidence_quote": "We chose that name together.",
            },
        ],
        tmp_path,
    )

    context = persona_context(tmp_path)

    assert "# Persona Constitution" in context
    assert "# Current State" in context
    assert "# Core Memories" in context
    assert "# Recent Memories" in context
    assert "You can call me River." in context
    assert "We chose that name together." in context
    assert result["accepted"][0]["memory_id"] in context
    assert len(context.encode("utf-8")) <= persona_runtime.CONTEXT_BYTE_BUDGET


def test_runtime_file_stays_below_the_owner_line_limit() -> None:
    runtime = Path(__file__).parents[1] / "persona_runtime.py"
    assert len(runtime.read_text(encoding="utf-8").splitlines()) <= 500


def test_init_creates_identity_data_once_without_seed_memories(tmp_path: Path) -> None:
    initialize_persona(tmp_path, "Aster", "River")

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    constitution = (tmp_path / "constitution.md").read_text(encoding="utf-8")
    assert state == {"persona_name": "Aster", "user_name": "River", "current": {}}
    assert "Your chosen name is Aster." in constitution
    assert "Address the user as River" in constitution
    assert json.loads((tmp_path / "memories.json").read_text(encoding="utf-8")) == []

    with pytest.raises(ValueError, match="persona data already exists"):
        initialize_persona(tmp_path, "Other", "Other")


@pytest.mark.asyncio
async def test_mcp_exposes_only_the_three_persona_tools(tmp_path: Path) -> None:
    runtime = Path(__file__).parents[1] / "persona_runtime.py"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(runtime), "--data-dir", str(tmp_path), "mcp"],
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listed = await session.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert set(tools) == {"persona_context", "recall", "remember"}
            remember_description = (tools["remember"].description or "").lower()
            assert "project facts" in remember_description
            assert "fabricated past" in remember_description

            result = await session.call_tool(
                "remember",
                {
                    "interaction": "We connected this persona through MCP.",
                    "operations": [
                        {
                            "action": "record",
                            "kind": "shared_experience",
                            "content": "We connected this persona through MCP.",
                            "evidence_quote": "We connected this persona through MCP.",
                        }
                    ],
                },
            )
            assert result.isError is not True

    memories = json.loads((tmp_path / "memories.json").read_text(encoding="utf-8"))
    assert [memory["content"] for memory in memories] == ["We connected this persona through MCP."]


def test_project_config_connects_mcp_and_injects_session_context() -> None:
    root = Path(__file__).parents[1]
    mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))

    server = mcp["mcpServers"]["persona"]
    assert server["command"] == "uv"
    assert "${CLAUDE_PROJECT_DIR:-.}" in server["args"]
    assert server["args"][-1] == "mcp"
    hook = settings["hooks"]["SessionStart"][0]
    assert hook["matcher"] == "startup|resume|clear|compact"
    assert "persona_runtime.py" in hook["hooks"][0]["command"]
    assert hook["hooks"][0]["command"].endswith(" context")
