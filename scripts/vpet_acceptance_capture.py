"""按冻结规格采集 VPet v1 单拍证据骨架；只读业务数据，永不自动标 PASS。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from mybuddy.config import load_config

BEAT_STEPS = {
    1: "制造离场超过 30 分钟，恢复输入；确认 user_back、digest、近期真实话题与 notice_shown。",
    2: "保持无交互至少 3 分钟；录制 10 秒 idle 动画，确认全程无气泡。",
    3: "当天连续摸头两次；确认首次有短句、第二次只有原生反射。",
    4: "14:00 偏移拖入咖喱，21:00 后闲聊至多两轮；确认投喂记忆自发回流。",
    5: "制造 3 小时空档与 reminder/nudge/greeting；回来确认 digest、overdue 与丢弃。",
    6: "00:40 偏移聊天后说晚安；确认困意、睡姿以及睡眠窗零主动展示。",
}

BEAT_QUERIES: dict[int, list[tuple[str, str]]] = {
    1: [
        ("latest_user_back", "SELECT * FROM vpet_events WHERE event='user_back' ORDER BY id DESC LIMIT 1"),
        (
            "delivery_audit",
            "SELECT * FROM vpet_events WHERE event IN "
            "('pending_drained','pending_digested','notice_shown') ORDER BY id DESC LIMIT 50",
        ),
    ],
    2: [
        (
            "recent_notices",
            "SELECT * FROM vpet_events WHERE event='notice_shown' ORDER BY id DESC LIMIT 50",
        ),
    ],
    3: [
        (
            "touches",
            "SELECT * FROM vpet_events WHERE event IN ('touch_head','touch_body') "
            "ORDER BY id DESC LIMIT 50",
        ),
    ],
    4: [
        (
            "feeds",
            "SELECT * FROM vpet_events WHERE event='feed' ORDER BY id DESC LIMIT 50",
        ),
    ],
    5: [
        (
            "pending_audit",
            "SELECT * FROM vpet_events WHERE event IN "
            "('pending_discarded','pending_digested','pending_overdue','notice_shown') "
            "ORDER BY id DESC LIMIT 100",
        ),
    ],
    6: [
        (
            "sleep_window_audit",
            "SELECT * FROM vpet_events WHERE event IN ('user_chat','notice_shown') "
            "ORDER BY id DESC LIMIT 100",
        ),
    ],
}


def capture_beat(
    beat: int,
    *,
    config_path: str | Path,
    output_root: str | Path,
    bridge_url: str | None = None,
    shell_log: str | Path | None = None,
    fetch_state: bool = True,
) -> Path:
    if beat not in BEAT_STEPS:
        raise ValueError("beat 必须是 1..6")
    config_file = Path(config_path).resolve()
    config = load_config(str(config_file))
    output = Path(output_root).resolve() / f"beat-{beat}"
    output.mkdir(parents=True, exist_ok=True)

    _write_steps(output / "steps.md", beat)
    _write_sql_evidence(output / "events.sql.txt", Path(config.paths.db_file), beat)
    _copy_shell_log(output / "shell.log.txt", shell_log)
    if fetch_state:
        _write_state(
            output / "state.json",
            bridge_url or "http://127.0.0.1:8000",
            token=config.vpet.bridge_token,
        )
    _write_initial_result(output / "result.json", beat, config_file)
    return output


def _write_steps(path: Path, beat: int) -> None:
    if path.exists():
        return
    path.write_text(
        f"# 拍 {beat} 操作记录\n\n## 冻结步骤\n\n{BEAT_STEPS[beat]}\n\n"
        "## 人工记录\n\n- 开始时间:\n- 结束时间:\n- 屏幕证据文件:\n- 观察与偏差:\n",
        encoding="utf-8",
    )


def _write_sql_evidence(path: Path, db_file: Path, beat: int) -> None:
    resolved = db_file.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"数据库不存在:{resolved}")
    lines = [f"database={resolved}", f"captured_at={datetime.now().astimezone().isoformat()}"]
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for name, sql in BEAT_QUERIES[beat]:
            lines.extend(["", f"-- {name}", sql + ";"])
            rows = connection.execute(sql).fetchall()
            lines.append(json.dumps([dict(row) for row in rows], ensure_ascii=False, default=str, indent=2))
    finally:
        connection.close()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_shell_log(target: Path, source: str | Path | None) -> None:
    if source is None:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return
        source = Path(appdata) / "BuddyShell" / "logs" / f"{datetime.now():%Y-%m-%d}.log"
    source_path = Path(source)
    if source_path.is_file() and source_path.stat().st_size > 0:
        shutil.copyfile(source_path, target)


def _write_state(path: Path, bridge_url: str, *, token: str = "") -> None:
    url = bridge_url.rstrip("/") + "/api/vpet/state"
    request = urllib.request.Request(url)  # noqa: S310 -- 本地验收 URL
    if token:
        request.add_header("X-MyBuddy-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            payload: Any = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exception:
        payload = {"ok": False, "capture_error": str(exception), "url": url}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_initial_result(path: Path, beat: int, config_file: Path) -> None:
    if path.exists():
        return
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path.cwd(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    config_hash = hashlib.sha256(config_file.read_bytes()).hexdigest()
    payload = {
        "beat": beat,
        "status": "FAIL",
        "commit": commit,
        "config_hash": config_hash,
        "tested_at": datetime.now().astimezone().isoformat(),
        "codex_evidence": "UNREVIEWED",
        "user_experience": "",
        "deviation": "初始状态；缺少屏幕证据或用户体验确认时不得改为 PASS。",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--beat", required=True, type=int, choices=range(1, 7))
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="eval/acceptance/v1")
    parser.add_argument("--bridge-url", default="http://127.0.0.1:8000")
    parser.add_argument("--shell-log")
    parser.add_argument("--skip-state", action="store_true")
    args = parser.parse_args()
    output = capture_beat(
        args.beat,
        config_path=args.config,
        output_root=args.output_root,
        bridge_url=args.bridge_url,
        shell_log=args.shell_log,
        fetch_state=not args.skip_state,
    )
    print(f"Captured beat {args.beat}: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
