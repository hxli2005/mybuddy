"""用明确的 UTF-8 请求做本机真实-key 验收。"""

from __future__ import annotations

import argparse
import json
import urllib.request
from typing import Any

DEFAULT_TEXT = "我刚忙完，回来看看你。"


def encode_payload(payload: dict[str, Any]) -> bytes:
    """Windows shell 不参与 JSON 编码；发送的字节始终是 UTF-8。"""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=encode_payload(payload),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=120) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/body/step")
    parser.add_argument("--event-id", default="real-key-chat-001")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument(
        "--scenario",
        choices=("chat", "touch-head", "raise", "quiet", "ambient"),
        default="chat",
    )
    args = parser.parse_args()

    if args.scenario == "chat":
        payload = {"event": {"event_id": args.event_id, "type": "chat", "content": args.text}}
    elif args.scenario == "touch-head":
        payload = {"event": {"event_id": args.event_id, "type": "touch_head"}}
    elif args.scenario == "raise":
        payload = {"event": {"event_id": args.event_id, "type": "raise"}}
    elif args.scenario == "ambient":
        payload = {"presence": {"present": True, "fullscreen": False}}
    else:
        payload = {}
    first = _post(args.url, payload)
    scheduled = None
    if args.scenario in {"quiet", "ambient"} and isinstance(first.get("activity"), dict):
        scheduled = first
        receipt: dict[str, Any] = {
            "activity_id": first["activity"]["id"],
            "status": "completed",
        }
        if first["activity"].get("type") == "walk":
            # 本脚本在验收里代行身体层;这份位移是模拟身体的诚实收据,
            # 真实窗口物理已由 BuddyShell 测试与 S15/S16 留档覆盖。
            receipt["reason"] = "animation_finished"
            receipt["motion"] = {
                "start_left": 100,
                "start_top": 80,
                "end_left": 220,
                "end_top": 80,
                "window_width": 200,
                "window_height": 240,
                "work_left": 0,
                "work_top": 0,
                "work_right": 800,
                "work_bottom": 600,
            }
        receipt_payload = {"activity_receipt": receipt}
        if args.scenario == "ambient":
            receipt_payload["presence"] = payload["presence"]
        first = _post(args.url, receipt_payload)
    expression = first.get("expression")
    shown = None
    if isinstance(expression, dict) and expression.get("id"):
        shown_payload = {"shown_id": expression["id"]}
        if args.scenario == "ambient":
            shown_payload["presence"] = payload["presence"]
        shown = _post(args.url, shown_payload)
    print(
        json.dumps(
            {
                "scenario": args.scenario,
                "sent": payload,
                "scheduled": scheduled,
                "step": first,
                "shown": shown,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
