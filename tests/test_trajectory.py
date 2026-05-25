"""TrajectoryLogger 测试。"""

from __future__ import annotations

import json

from mybuddy._time import utcnow
from mybuddy.learning import TrajectoryLogger, TrajectoryStep


def test_commit_writes_jsonl(tmp_path) -> None:
    logger = TrajectoryLogger(tmp_path)
    traj = logger.start(session_id="s1", system="sys", user_input="hi")
    traj.steps.append(
        TrajectoryStep(
            assistant_text="hello",
            tool_calls=[{"id": "t1", "name": "weather", "arguments": {"city": "BJ"}}],
            tool_results=[{"tool_call_id": "t1", "name": "weather", "result": "{}"}],
        )
    )
    traj.final_response = "你好"
    traj.finish_reason = "stop"
    path = logger.commit(traj)
    assert path.exists()

    today = utcnow().strftime("%Y-%m-%d")
    assert path.name == f"{today}.jsonl"

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["session_id"] == "s1"
    assert data["user_input"] == "hi"
    assert data["final_response"] == "你好"
    assert data["steps"][0]["tool_calls"][0]["name"] == "weather"


def test_attach_label_writes_labels_file(tmp_path) -> None:
    logger = TrajectoryLogger(tmp_path)
    logger.attach_label("turn-xyz", "good")
    today = utcnow().strftime("%Y-%m-%d")
    label_file = tmp_path / f"{today}.labels.jsonl"
    assert label_file.exists()
    entry = json.loads(label_file.read_text(encoding="utf-8").strip())
    assert entry["turn_id"] == "turn-xyz"
    assert entry["label"] == "good"
