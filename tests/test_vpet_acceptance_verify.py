from __future__ import annotations

import json

from scripts.vpet_acceptance_verify import verify_acceptance


def test_full_acceptance_requires_all_evidence_and_seven_valid_dates(tmp_path) -> None:
    root = tmp_path / "acceptance"
    root.mkdir()
    (root / "RESULT.json").write_text(
        json.dumps(
            {
                "release_level": "FULL",
                "weekly_check": {
                    "completed": True,
                    "valid_dates": [f"2026-07-{day:02d}" for day in range(25, 32)],
                    "memory_reflows_caught": 1,
                    "cowork_sessions": 3,
                    "night_interruptions": 0,
                    "beat_5_true_time": True,
                    "beat_6_true_time": True,
                },
                "release_blocked": False,
                "deferred_beats": [],
            }
        ),
        encoding="utf-8",
    )
    for beat in range(1, 7):
        directory = root / f"beat-{beat}"
        directory.mkdir()
        for name in ("steps.md", "events.sql.txt", "shell.log.txt", "screen.mp4"):
            (directory / name).write_text("evidence", encoding="utf-8")
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "beat": beat,
                    "status": "PASS",
                    "commit": "abc",
                    "config_hash": "sha256",
                    "tested_at": "2026-07-24T12:00:00+08:00",
                    "codex_evidence": "checked",
                    "user_experience": "confirmed",
                    "deviation": "",
                }
            ),
            encoding="utf-8",
        )

    assert verify_acceptance(root) == []

    (root / "beat-3" / "screen.mp4").unlink()
    assert "beat-3: PASS 缺少 screen.mp4 或截图序列" in verify_acceptance(root)

    beat_two = root / "beat-2" / "result.json"
    result = json.loads(beat_two.read_text(encoding="utf-8"))
    result["codex_evidence"] = "UNREVIEWED"
    beat_two.write_text(json.dumps(result), encoding="utf-8")
    assert "beat-2/result.json: PASS 前必须完成 Codex 证据审计" in verify_acceptance(root)


def test_reduced_without_claimed_pass_can_freeze(tmp_path) -> None:
    root = tmp_path / "acceptance"
    root.mkdir()
    (root / "RESULT.json").write_text(
        json.dumps({"release_level": "REDUCED", "deferred_beats": [1, 2, 3, 4, 5, 6]}),
        encoding="utf-8",
    )
    for beat in range(1, 7):
        directory = root / f"beat-{beat}"
        directory.mkdir()
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "beat": beat,
                    "status": "DEFERRED",
                    "commit": "abc",
                    "config_hash": "sha256",
                    "tested_at": "2026-07-27T12:00:00+08:00",
                    "codex_evidence": "缺口已审计",
                    "user_experience": "未通过",
                    "deviation": "转入 v1.1",
                }
            ),
            encoding="utf-8",
        )

    assert verify_acceptance(root) == []
