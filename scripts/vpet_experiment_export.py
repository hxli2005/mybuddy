"""把冻结实验数据库导出为逐展示 CSV,口径唯一且可测试。"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mybuddy.storage import Message, VPetEvent, init_db, session_scope


@dataclass(frozen=True)
class Interaction:
    created_at: datetime
    kind: str
    row_id: int


def build_notice_rows(db_file: str) -> list[dict[str, Any]]:
    engine = init_db(db_file)
    with session_scope(engine) as session:
        notices = (
            session.query(VPetEvent)
            .filter(VPetEvent.event == "notice_shown")
            .order_by(VPetEvent.created_at.asc(), VPetEvent.id.asc())
            .all()
        )
        vpet_interactions = (
            session.query(VPetEvent)
            .filter(
                VPetEvent.event.in_(
                    [
                        "chat",
                        "user_chat",
                        "touch_head",
                        "touch_body",
                        "feed",
                        "work_start",
                        "work_stop",
                    ]
                )
            )
            .order_by(VPetEvent.created_at.asc(), VPetEvent.id.asc())
            .all()
        )
        messages = (
            session.query(Message)
            .filter(Message.role == "user")
            .order_by(Message.created_at.asc(), Message.id.asc())
            .all()
        )

    interactions = [
        Interaction(row.created_at, row.event, row.id) for row in vpet_interactions
    ] + [Interaction(row.created_at, "user_message", row.id) for row in messages]
    interactions.sort(key=lambda item: (item.created_at, item.row_id))

    user_assignment: dict[int, Interaction] = {}
    for interaction in (item for item in interactions if item.kind == "user_message"):
        candidates = [
            notice
            for notice in notices
            if notice.created_at <= interaction.created_at <= notice.created_at + timedelta(minutes=10)
        ]
        if candidates:
            user_assignment[candidates[-1].id] = interaction

    rows: list[dict[str, Any]] = []
    for notice in notices:
        context = _safe_json(notice.context_json)
        flags = _safe_json(notice.server_flags_json)
        any_5m = next(
            (
                item
                for item in interactions
                if notice.created_at < item.created_at <= notice.created_at + timedelta(minutes=5)
            ),
            None,
        )
        user_10m = user_assignment.get(notice.id)
        rows.append(
            {
                "notice_id": notice.id,
                "local_date": context.get("local_date", ""),
                "source": context.get("source", ""),
                "pending_id": context.get("pending_id", ""),
                "shown_at": context.get("shown_at") or notice.created_at.isoformat(timespec="seconds"),
                "any_interaction_5m": bool(any_5m),
                "first_interaction_kind": any_5m.kind if any_5m else "",
                "user_response_10m": bool(user_10m),
                "user_message_id": user_10m.row_id if user_10m else "",
                "touch_escalation": flags.get("touch_escalation"),
                "physical_proactive": flags.get("physical_proactive"),
                "day_index": notice.day_index,
            }
        )
    return rows


def write_csv(rows: list[dict[str, Any]], output: str | Path) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "notice_id",
        "local_date",
        "source",
        "pending_id",
        "shown_at",
        "any_interaction_5m",
        "first_interaction_kind",
        "user_response_10m",
        "user_message_id",
        "touch_escalation",
        "physical_proactive",
        "day_index",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _safe_json(value: str | None) -> dict[str, Any]:
    try:
        loaded = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="./data/mybuddy.db")
    parser.add_argument("--output", default="./eval/experiment/vpet-notices.csv")
    args = parser.parse_args()
    rows = build_notice_rows(args.db)
    write_csv(rows, args.output)
    print(f"exported {len(rows)} notices -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
