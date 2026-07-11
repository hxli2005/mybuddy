from __future__ import annotations

from datetime import datetime, timedelta

from mybuddy.memory import LongTermMemory
from mybuddy.storage import Message, VPetEvent, init_db, record_vpet_event, session_scope
from scripts.vpet_acceptance_evidence import shared_moments
from scripts.vpet_experiment_export import build_notice_rows, write_csv


def test_shared_moment_evidence_filters_date_and_source(tmp_path) -> None:
    memory_dir = tmp_path / "memory"
    ltm = LongTermMemory(persist_dir=memory_dir)
    ltm.add(
        "今天你请我吃了咖喱饭",
        mem_type="shared_moment",
        uid="feed-1",
        extra_meta={"date": "2026-07-11", "source": "vpet_feed"},
    )
    ltm.add(
        "另一天的事",
        mem_type="shared_moment",
        uid="other",
        extra_meta={"date": "2026-07-10", "source": "vpet_feed"},
    )

    rows = shared_moments(memory_dir, local_date="2026-07-11", source="vpet_feed")

    assert len(rows) == 1
    assert rows[0]["id"] == "feed-1"


def test_experiment_export_assigns_user_to_latest_notice(tmp_path) -> None:
    db_file = str(tmp_path / "experiment.db")
    engine = init_db(db_file)
    first, _ = record_vpet_event(
        engine,
        event="notice_shown",
        context={"source": "nudge", "pending_id": 1, "shown_at": "2026-08-04T09:00:00"},
        server_flags={"touch_escalation": True, "physical_proactive": True},
        day_index=3,
    )
    second, _ = record_vpet_event(
        engine,
        event="notice_shown",
        context={"source": "dynamic", "pending_id": 2, "shown_at": "2026-08-04T09:02:00"},
        server_flags={"touch_escalation": True, "physical_proactive": True},
        day_index=3,
    )
    base = datetime(2026, 8, 4, 1, 0)
    with session_scope(engine) as session:
        session.get(VPetEvent, first["id"]).created_at = base
        session.get(VPetEvent, second["id"]).created_at = base + timedelta(minutes=2)
        session.add(
            Message(
                session_id="s1",
                role="user",
                content="我看到啦",
                created_at=base + timedelta(minutes=4),
            )
        )

    rows = build_notice_rows(db_file)

    assert len(rows) == 2
    assert rows[0]["user_response_10m"] is False
    assert rows[1]["user_response_10m"] is True
    assert rows[1]["source"] == "dynamic"

    output = tmp_path / "notices.csv"
    write_csv(rows, output)
    assert output.read_text(encoding="utf-8-sig").startswith("notice_id,local_date")
