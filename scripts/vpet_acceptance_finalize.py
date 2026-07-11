"""汇总六拍与周检结果；证据不完整时只会生成 REDUCED，绝不自动签 PASS。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.vpet_acceptance_verify import verify_acceptance


def finalize_acceptance(root: str | Path, *, release_blocked: bool = False) -> dict[str, Any]:
    base = Path(root).resolve()
    previous = _load(base / "RESULT.json", required=False) or {}
    beat_results = [_load(base / f"beat-{beat}" / "result.json") for beat in range(1, 7)]
    weekly = _load(base / "weekly" / "result.json", required=False) or {
        "completed": False,
        "valid_dates": [],
        "memory_reflows_caught": 0,
        "cowork_sessions": 0,
        "night_interruptions": 0,
        "beat_5_true_time": False,
        "beat_6_true_time": False,
    }
    statuses = {str(index): result.get("status") for index, result in enumerate(beat_results, 1)}
    blocked = release_blocked or previous.get("release_blocked") is True
    full_candidate = all(status == "PASS" for status in statuses.values()) and (
        weekly.get("completed") is True and not blocked
    )
    deviations = [
        f"beat-{index}: {text}"
        for index, result in enumerate(beat_results, 1)
        if (text := str(result.get("deviation") or "").strip())
    ]
    summary: dict[str, Any] = {
        "release_level": "FULL" if full_candidate else "REDUCED",
        "release_blocked": blocked,
        "beat_status": statuses,
        "deferred_beats": [
            index for index, result in enumerate(beat_results, 1) if result.get("status") == "DEFERRED"
        ],
        "failed_beats": [
            index for index, result in enumerate(beat_results, 1) if result.get("status") == "FAIL"
        ],
        "known_deviations": deviations,
        "weekly_check": weekly,
        "generated_at": datetime.now().astimezone().isoformat(),
    }
    result_path = base / "RESULT.json"
    result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    issues = verify_acceptance(base)
    if full_candidate and issues:
        summary["release_level"] = "REDUCED"
        summary["verification_issues"] = issues
        result_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def _load(path: Path, *, required: bool = True) -> dict[str, Any] | None:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return None
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 顶层必须是对象")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="eval/acceptance/v1")
    parser.add_argument("--release-blocked", action="store_true")
    args = parser.parse_args()
    result = finalize_acceptance(args.root, release_blocked=args.release_blocked)
    print(f"Acceptance summary: {result['release_level']} -> {Path(args.root) / 'RESULT.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
