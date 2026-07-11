from __future__ import annotations

import json

from scripts.vpet_acceptance_finalize import finalize_acceptance


def test_finalize_reduced_collects_all_deferred_beats(tmp_path) -> None:
    for beat in range(1, 7):
        directory = tmp_path / f"beat-{beat}"
        directory.mkdir()
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "beat": beat,
                    "status": "DEFERRED",
                    "commit": "abc",
                    "config_hash": "hash",
                    "tested_at": "2026-07-27T12:00:00+08:00",
                    "codex_evidence": "已确认缺口",
                    "user_experience": "未通过",
                    "deviation": "转入 v1.1",
                }
            ),
            encoding="utf-8",
        )

    summary = finalize_acceptance(tmp_path)

    assert summary["release_level"] == "REDUCED"
    assert summary["deferred_beats"] == [1, 2, 3, 4, 5, 6]
    assert summary["failed_beats"] == []
    persisted = json.loads((tmp_path / "RESULT.json").read_text(encoding="utf-8"))
    assert persisted["weekly_check"]["completed"] is False
