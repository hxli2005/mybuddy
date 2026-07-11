from __future__ import annotations

import json
from datetime import datetime

from mybuddy.storage import VPetEvent, init_db, session_scope
from scripts.vpet_weekly_check import WEEK_DATES, collect_weekly


def test_weekly_check_uses_server_dates_and_sleep_window(tmp_path) -> None:
    db_file = tmp_path / "weekly.db"
    engine = init_db(str(db_file))
    with session_scope(engine) as session:
        for index, local_date in enumerate(WEEK_DATES):
            session.add(
                VPetEvent(
                    event="presence_heartbeat",
                    count=120,
                    context_json=json.dumps({"local_date": local_date}),
                    server_flags_json="{}",
                    created_at=datetime(2026, 7, 25 + index, 4, 0),
                )
            )
        for day in (25, 26, 27):
            session.add(
                VPetEvent(
                    event="work_stop",
                    count=1,
                    context_json=json.dumps({"local_date": f"2026-07-{day:02d}"}),
                    server_flags_json="{}",
                    created_at=datetime(2026, 7, day, 10, 0),
                )
            )
        session.add(
            VPetEvent(
                event="notice_shown",
                count=1,
                context_json=json.dumps(
                    {
                        "local_date": "2026-07-28",
                        "shown_at": "2026-07-28T00:40:00+08:00",
                        "source": "nudge",
                    }
                ),
                server_flags_json="{}",
                created_at=datetime(2026, 7, 27, 16, 40),
            )
        )

    result = collect_weekly(db_file, sleep_start="00:30", sleep_end="08:30")

    assert result["valid_dates"] == list(WEEK_DATES)
    assert result["cowork_sessions"] == 3
    assert result["night_interruptions"] == 1
    assert result["night_notice_rows"][0]["source"] == "nudge"
