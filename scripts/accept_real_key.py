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
        choices=("chat", "touch-head", "quiet", "ambient"),
        default="chat",
    )
    args = parser.parse_args()

    if args.scenario == "chat":
        payload = {"event": {"event_id": args.event_id, "type": "chat", "content": args.text}}
    elif args.scenario == "touch-head":
        payload = {"event": {"event_id": args.event_id, "type": "touch_head"}}
    elif args.scenario == "ambient":
        payload = {"presence": {"present": True, "fullscreen": False}}
    else:
        payload = {}
    first = _post(args.url, payload)
    expression = first.get("expression")
    shown = None
    if isinstance(expression, dict) and expression.get("id"):
        shown = _post(args.url, {"shown_id": expression["id"]})
    print(
        json.dumps(
            {"scenario": args.scenario, "sent": payload, "step": first, "shown": shown},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
