from __future__ import annotations

import json
from pathlib import Path

import yaml

from mybuddy.storage import init_db, record_vpet_event
from scripts.vpet_acceptance_capture import capture_beat


def test_capture_creates_honest_fixed_evidence_without_overwriting_review(tmp_path) -> None:
    db_file = tmp_path / "capture.db"
    engine = init_db(str(db_file))
    record_vpet_event(
        engine,
        event="touch_head",
        client_event_id="capture-touch",
        server_flags={},
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump({"paths": {"db_file": str(db_file)}}),
        encoding="utf-8",
    )
    shell_log = tmp_path / "shell.log"
    shell_log.write_text("event=touch_head client_event_id=capture-touch\n", encoding="utf-8")

    output = capture_beat(
        3,
        config_path=config,
        output_root=tmp_path / "acceptance",
        shell_log=shell_log,
        fetch_state=False,
    )

    assert {path.name for path in output.iterdir()} == {
        "steps.md",
        "events.sql.txt",
        "shell.log.txt",
        "result.json",
    }
    assert "capture-touch" in (output / "events.sql.txt").read_text(encoding="utf-8")
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["beat"] == 3
    assert result["status"] == "FAIL"
    assert result["codex_evidence"] == "UNREVIEWED"
    assert result["user_experience"] == ""
    assert not (output / "screen.mp4").exists()

    reviewed = {**result, "status": "DEFERRED", "user_experience": "已人工复核"}
    (output / "result.json").write_text(json.dumps(reviewed), encoding="utf-8")
    capture_beat(
        3,
        config_path=config,
        output_root=tmp_path / "acceptance",
        shell_log=shell_log,
        fetch_state=False,
    )
    assert json.loads((output / "result.json").read_text(encoding="utf-8"))["status"] == "DEFERRED"


def test_capture_rejects_unknown_beat(tmp_path) -> None:
    try:
        capture_beat(7, config_path=Path("config.yaml"), output_root=tmp_path)
    except ValueError as exception:
        assert "1..6" in str(exception)
    else:
        raise AssertionError("expected ValueError")
