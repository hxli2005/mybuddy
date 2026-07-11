"""校验 VPet v1 六拍证据包,阻止无证据的 PASS/FULL 进入冻结清单。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_BEAT_FILES = ("steps.md", "events.sql.txt", "shell.log.txt", "result.json")
REQUIRED_RESULT_FIELDS = {
    "beat",
    "status",
    "commit",
    "config_hash",
    "tested_at",
    "codex_evidence",
    "user_experience",
    "deviation",
}


def verify_acceptance(root: str | Path) -> list[str]:
    base = Path(root)
    issues: list[str] = []
    summary = _load_json(base / "RESULT.json", issues, required=False)
    release_level = str((summary or {}).get("release_level") or "REDUCED")
    if summary is not None and release_level not in {"FULL", "REDUCED"}:
        issues.append("RESULT.json: release_level 必须是 FULL 或 REDUCED")
    statuses: list[str] = []

    for beat in range(1, 7):
        directory = base / f"beat-{beat}"
        result_path = directory / "result.json"
        result = _load_json(result_path, issues, required=summary is not None)
        if result is None:
            statuses.append("MISSING")
            continue
        status = str(result.get("status") or "")
        statuses.append(status)
        if result.get("beat") != beat:
            issues.append(f"beat-{beat}/result.json: beat 必须为 {beat}")
        if status not in {"PASS", "FAIL", "DEFERRED"}:
            issues.append(f"beat-{beat}/result.json: status 非法")
        missing_fields = sorted(REQUIRED_RESULT_FIELDS - result.keys())
        if missing_fields:
            issues.append(f"beat-{beat}/result.json: 缺字段 {', '.join(missing_fields)}")
        if status != "PASS":
            continue
        for name in REQUIRED_BEAT_FILES:
            path = directory / name
            if not path.is_file() or path.stat().st_size == 0:
                issues.append(f"beat-{beat}: PASS 缺少非空 {name}")
        screens = [directory / "screen.mp4", *directory.glob("screen*.png"), *directory.glob("screen*.jpg")]
        if not any(path.is_file() and path.stat().st_size > 0 for path in screens):
            issues.append(f"beat-{beat}: PASS 缺少 screen.mp4 或截图序列")
        for field in ("commit", "config_hash", "tested_at", "codex_evidence", "user_experience"):
            if not str(result.get(field) or "").strip():
                issues.append(f"beat-{beat}/result.json: PASS 的 {field} 不能为空")
        if str(result.get("codex_evidence") or "").strip().upper() in {
            "UNREVIEWED",
            "PENDING",
            "TODO",
        }:
            issues.append(f"beat-{beat}/result.json: PASS 前必须完成 Codex 证据审计")

    if release_level == "FULL":
        if statuses != ["PASS"] * 6:
            issues.append("RESULT.json: FULL 要求六拍全部 PASS")
        weekly = (summary or {}).get("weekly_check")
        if not isinstance(weekly, dict) or weekly.get("completed") is not True:
            issues.append("RESULT.json: FULL 要求 weekly_check.completed=true")
        dates = weekly.get("valid_dates", []) if isinstance(weekly, dict) else []
        expected_dates = {f"2026-07-{day:02d}" for day in range(25, 32)}
        actual_dates = set(map(str, dates)) if isinstance(dates, list) else set()
        if not expected_dates.issubset(actual_dates):
            issues.append("RESULT.json: FULL 要求覆盖 2026-07-25..31 七个有效日")
        required_weekly = {
            "memory_reflows_caught": (lambda value: int(value) >= 1),
            "cowork_sessions": (lambda value: int(value) >= 3),
            "night_interruptions": (lambda value: int(value) == 0),
            "beat_5_true_time": (lambda value: value is True),
            "beat_6_true_time": (lambda value: value is True),
        }
        for field, predicate in required_weekly.items():
            try:
                valid = isinstance(weekly, dict) and predicate(weekly.get(field))
            except (TypeError, ValueError):
                valid = False
            if not valid:
                issues.append(f"RESULT.json: FULL 周检字段不满足 {field}")
        if (summary or {}).get("release_blocked") is not False:
            issues.append("RESULT.json: FULL 要求 release_blocked=false")
        if (summary or {}).get("deferred_beats") not in ([], None):
            issues.append("RESULT.json: FULL 不允许 deferred_beats")
    return issues


def _load_json(path: Path, issues: list[str], *, required: bool) -> dict[str, Any] | None:
    if not path.is_file():
        if required:
            issues.append(f"缺少 {path.as_posix()}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exception:
        issues.append(f"{path.as_posix()}: 无法解析 JSON ({exception})")
        return None
    if not isinstance(value, dict):
        issues.append(f"{path.as_posix()}: 顶层必须是对象")
        return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="eval/acceptance/v1")
    args = parser.parse_args()
    issues = verify_acceptance(args.root)
    if issues:
        for issue in issues:
            print(f"FAIL: {issue}")
        return 1
    print(f"Acceptance evidence verified: {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
