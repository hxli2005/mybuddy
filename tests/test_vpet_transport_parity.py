from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from fastapi.testclient import TestClient

from mybuddy.api import AppState, create_app
from mybuddy.config import Config
from mybuddy.storage import init_db
from mybuddy.web import DemoHandler, DemoServer


def test_fastapi_and_standard_web_share_vpet_v2_contract(tmp_path) -> None:
    fast_app = create_app(str(tmp_path / "unused-fast.yaml"))
    fast_state = fast_app.state.mybuddy
    fast_state.cfg = Config()
    fast_state.engine = init_db(str(tmp_path / "fast.db"))
    fast_client = TestClient(fast_app)

    web_state = AppState(config_path=str(tmp_path / "unused-web.yaml"))
    web_state.cfg = Config()
    web_state.engine = init_db(str(tmp_path / "web.db"))
    server = DemoServer(
        ("127.0.0.1", 0),
        DemoHandler,
        state=web_state,
        frontend_dir=tmp_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        fast_state_payload = fast_client.get("/api/vpet/state").json()
        web_status, web_state_payload = _request_json(base_url, "GET", "/api/vpet/state")
        assert web_status == 200
        for key in (
            "ok",
            "bridge",
            "time_offset_minutes",
            "physio",
            "idle_hint",
            "warmth",
            "server_flags",
            "day_index",
        ):
            assert web_state_payload[key] == fast_state_payload[key]

        heartbeat = {
            "event": "presence_heartbeat",
            "count": 20,
            "client_event_id": "parity-heartbeat",
        }
        fast_event = fast_client.post("/api/vpet/event", json=heartbeat)
        web_event_status, web_event = _request_json(
            base_url,
            "POST",
            "/api/vpet/event",
            heartbeat,
        )
        assert web_event_status == fast_event.status_code == 200
        assert web_event == fast_event.json()

        fast_drain = fast_client.post("/api/vpet/pending/drain", json={"digest": True})
        web_drain_status, web_drain = _request_json(
            base_url,
            "POST",
            "/api/vpet/pending/drain",
            {"digest": True},
        )
        assert web_drain_status == fast_drain.status_code == 200
        assert web_drain == fast_drain.json()

        fast_invalid = fast_client.post("/api/vpet/event", json={"event": "bad"})
        web_invalid_status, web_invalid = _request_json(
            base_url,
            "POST",
            "/api/vpet/event",
            {"event": "bad"},
        )
        assert web_invalid_status == fast_invalid.status_code == 400
        assert web_invalid == fast_invalid.json()

        fast_chat = fast_client.post("/api/vpet/chat", json={"message": "在吗"})
        web_chat_status, web_chat = _request_json(
            base_url,
            "POST",
            "/api/vpet/chat",
            {"message": "在吗"},
        )
        assert web_chat_status == fast_chat.status_code == 400
        assert web_chat == fast_chat.json()

        fast_state.cfg.vpet.bridge_token = "secret"
        web_state.cfg.vpet.bridge_token = "secret"
        fast_denied = fast_client.get("/api/vpet/state")
        web_denied_status, web_denied = _request_json(base_url, "GET", "/api/vpet/state")
        assert web_denied_status == fast_denied.status_code == 401
        assert web_denied == fast_denied.json()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exception:
        return exception.code, json.loads(exception.read().decode("utf-8"))
