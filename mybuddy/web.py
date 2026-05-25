"""无额外依赖的演示 Web 服务。

默认用于 `mybuddy web`:标准库 HTTPServer 托管前端静态文件和 JSON API。
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from mybuddy.api import AppState


class DemoServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler, *, state: AppState, frontend_dir: Path):
        super().__init__(server_address, handler)
        self.state = state
        self.frontend_dir = frontend_dir


class DemoHandler(BaseHTTPRequestHandler):
    server: DemoServer

    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path == "/":
                self._send_file(self.server.frontend_dir / "index.html")
                return
            if self.path.startswith("/static/"):
                name = unquote(self.path.removeprefix("/static/"))
                self._send_file(self.server.frontend_dir / name)
                return
            if self.path == "/api/status":
                self._send_json(self.server.state.status_payload())
                return
            if self.path == "/api/persona":
                self._send_json(self.server.state.persona_payload())
                return
            if self.path == "/api/profile":
                self._send_json(self.server.state.profile_payload())
                return
            if self.path == "/api/memory":
                self._send_json(self.server.state.memory_payload())
                return
            if self.path == "/api/reminders":
                self._send_json(self.server.state.reminders_payload())
                return
            if self.path == "/api/skills":
                self._send_json(self.server.state.skills_payload())
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except Exception as e:  # noqa: BLE001
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_POST(self) -> None:  # noqa: N802
        try:
            data = self._read_json()
            if self.path == "/api/chat":
                message = str(data.get("message", "")).strip()
                if not message:
                    self._send_error(HTTPStatus.BAD_REQUEST, "message is required")
                    return
                payload = asyncio.run(self.server.state.chat_payload(message))
                self._send_json(payload)
                return
            if self.path == "/api/feedback":
                label = str(data.get("label", "")).strip()
                if not label:
                    self._send_error(HTTPStatus.BAD_REQUEST, "label is required")
                    return
                payload = self.server.state.feedback_payload(label, data.get("turn_id"))
                self._send_json(payload)
                return
            if self.path == "/api/persona":
                payload = self.server.state.update_persona_payload(data)
                self._send_json(payload)
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except RuntimeError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:  # noqa: BLE001
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_PUT(self) -> None:  # noqa: N802
        try:
            data = self._read_json()
            if self.path == "/api/persona":
                payload = self.server.state.update_persona_payload(data)
                self._send_json(payload)
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except RuntimeError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:  # noqa: BLE001
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        root = self.server.frontend_dir.resolve()
        resolved = path.resolve()
        if not str(resolved).startswith(str(root)) or not resolved.exists() or not resolved.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "file not found")
            return
        body = resolved.read_bytes()
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, detail: str) -> None:
        self._send_json({"detail": detail}, status=status)


def serve(
    *,
    config_path: str = "config.yaml",
    host: str = "127.0.0.1",
    port: int = 8000,
    max_steps: int = 6,
) -> None:
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    state = AppState(config_path=config_path, max_steps=max_steps, enable_scheduler=False)
    state.startup()
    server = DemoServer((host, port), DemoHandler, state=state, frontend_dir=frontend_dir)
    try:
        server.serve_forever()
    finally:
        state.shutdown()
        server.server_close()
