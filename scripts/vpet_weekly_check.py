"""生成 2026-07-25..31 周检只读证据；人工项保留，条件齐全才 completed。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from mybuddy.config import load_config

WEEK_DATES = tuple(f"2026-07-{day:02d}" for day in range(25, 32))


def collect_weekly(
    db_file: str | Path,
    *,
    sleep_start: str,
    sleep_end: str,
) -> dict[str, Any]:
    path = Path(db_file).resolve()
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id,event,count,context_json,gate_reason,created_at "
            "FROM vpet_events ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    heartbeat_minutes = {date: 0 for date in WEEK_DATES}
    cowork_sessions = 0
    night_notices: list[dict[str, Any]] = []
    for row in rows:
        context = _safe_json(row["context_json"])
        local_date = str(context.get("local_date") or "")
        if local_date not in heartbeat_minutes:
            continue
        if row["event"] == "presence_heartbeat":
            heartbeat_minutes[local_date] += max(0, int(row["count"] or 0))
        elif row["event"] == "work_stop" and not row["gate_reason"]:
            cowork_sessions += 1
        elif row["event"] == "notice_shown":
            shown_at = str(context.get("shown_at") or context.get("server_time") or "")
            if _in_sleep_window(shown_at, sleep_start, sleep_end):
                night_notices.append(
                    {
                        "id": row["id"],
                        "local_date": local_date,
                        "shown_at": shown_at,
                        "source": context.get("source"),
                        "pending_id": context.get("pending_id"),
                    }
                )
    valid_dates = [date for date, minutes in heartbeat_minutes.items() if minutes >= 120]
    return {
        "window": "2026-07-25/2026-07-31",
        "heartbeat_minutes": heartbeat_minutes,
        "valid_dates": valid_dates,
        "cowork_sessions": cowork_sessions,
        "night_interruptions": len(night_notices),
        "night_notice_rows": night_notices,
    }


def write_weekly(
    *,
    config_path: str | Path,
    output_root: str | Path,
) -> Path:
    config = load_config(config_path)
    output = Path(output_root).resolve() / "weekly"
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "result.json"
    previous = _safe_json(result_path.read_text(encoding="utf-8-sig")) if result_path.exists() else {}
    automatic = collect_weekly(
        config.paths.db_file,
        sleep_start=config.physio.sleep_start,
        sleep_end=config.physio.sleep_end,
    )
    result = {
        **automatic,
        "memory_reflows_caught": int(previous.get("memory_reflows_caught") or 0),
        "beat_5_true_time": previous.get("beat_5_true_time") is True,
        "beat_6_true_time": previous.get("beat_6_true_time") is True,
        "review_note": str(previous.get("review_note") or ""),
    }
    result["completed"] = (
        set(result["valid_dates"]) == set(WEEK_DATES)
        and result["cowork_sessions"] >= 3
        and result["night_interruptions"] == 0
        and result["memory_reflows_caught"] >= 1
        and result["beat_5_true_time"]
        and result["beat_6_true_time"]
    )
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "events.sql.txt").write_text(
        "-- 自动周检口径\n"
        "SELECT event,count,context_json,gate_reason,created_at FROM vpet_events\n"
        "WHERE json_extract(context_json,'$.local_date') BETWEEN '2026-07-25' AND '2026-07-31'\n"
        "ORDER BY id;\n\n"
        + json.dumps(automatic, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return result_path


def _in_sleep_window(value: str, start: str, end: str) -> bool:
    try:
        current = datetime.fromisoformat(value)
        start_minutes = _hh_mm(start)
        end_minutes = _hh_mm(end)
    except (TypeError, ValueError):
        return False
    minute = current.hour * 60 + current.minute
    if start_minutes == end_minutes:
        return False
    if start_minutes < end_minutes:
        return start_minutes <= minute < end_minutes
    return minute >= start_minutes or minute < end_minutes


def _hh_mm(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":", 1))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(value)
    return hour * 60 + minute


def _safe_json(value: str | None) -> dict[str, Any]:
    try:
        loaded = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="eval/acceptance/v1")
    args = parser.parse_args()
    path = write_weekly(config_path=args.config, output_root=args.output_root)
    print(f"Weekly evidence: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
