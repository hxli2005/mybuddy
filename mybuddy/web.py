"""无额外依赖的 Web 服务。

默认用于 `mybuddy web`:标准库 HTTPServer 暴露 JSON API(对话 + vpet 桥)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Awaitable
from concurrent.futures import TimeoutError as FutureTimeoutError
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from mybuddy.api import (
    AppState,
    VPetEventRequest,
    _bridge_token,
    _parse_client_flags,
    _requires_bridge_auth,
)
from mybuddy.body import PhysioBusyError

logger = logging.getLogger(__name__)


class _BackgroundLoop:
    """常驻事件循环(后台线程),所有请求共用。

    ThreadingHTTPServer 每个请求跑在自己的 worker 线程里。若每请求都用
    ``asyncio.run(...)`` 起一个一次性 loop,回复返回后该 loop 立即关闭并取消所有未完成
    task —— agent 在对话里 fire-and-forget 起的后台记忆抽取 / skill 复盘(挂着等
    small-model 往返)会被静默腰斩,网页对话几乎学不到新事实。

    改为所有请求把协程投递到这一个常驻 loop(贴近 uvicorn 行为):请求协程跑完拿到
    回复后,后台 task 仍挂在 loop 上继续跑完。
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="mybuddy-web-loop", daemon=True
        )
        self._closed = False
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro: Awaitable[Any]) -> Any:
        """把协程投递到常驻 loop 并阻塞等其结果(供同步 worker 线程调用)。"""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def drain(self, tasks: set[asyncio.Task], *, timeout: float = 10.0) -> None:
        """关闭前把在途后台 task 跑完;超时则放弃等待,不阻塞退出。"""
        if self._closed:
            return

        async def _await_pending() -> None:
            pending = [t for t in tasks if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(_await_pending(), self._loop).result(timeout)
        except FutureTimeoutError:
            logger.warning("后台任务在 %.0fs 内未跑完,放弃等待直接关闭", timeout)
        except RuntimeError:
            pass  # loop 已停

    def close(self, *, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=timeout)
        self._loop.close()


class DemoServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler, *, state: AppState):
        self.state = state
        self.bg = _BackgroundLoop()
        super().__init__(server_address, handler)

    def server_close(self) -> None:
        # 关闭前把在途后台 task(记忆抽取 / skill 复盘)跑完,再停常驻 loop,最后关 socket。
        agent = self.state.agent
        if agent is not None:
            self.bg.drain(agent._bg_tasks)
        self.bg.close()
        super().server_close()


class DemoHandler(BaseHTTPRequestHandler):
    server: DemoServer

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorize_request():
            return
        try:
            path = urlparse(self.path).path
            if path == "/":
                self._send_json({"ok": True, "service": "mybuddy"})
                return
            if path == "/api/status":
                self._send_json(self.server.state.status_payload())
                return
            if path == "/api/vpet/status":
                self._send_json(self.server.state.vpet_status_payload())
                return
            if path == "/api/vpet/state":
                self._send_json(self.server.state.vpet_state_payload())
                return
            if path == "/api/vpet/pending":
                self._send_json(self.server.state.vpet_pending_payload(drain=False))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except PhysioBusyError as e:
            self._send_json(
                {
                    "ok": False,
                    "error": {"code": "physio_busy", "message": str(e)},
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:  # noqa: BLE001
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorize_request():
            return
        try:
            path = urlparse(self.path).path
            data = self._read_json()
            if path == "/api/chat":
                message = str(data.get("message", "")).strip()
                if not message:
                    self._send_error(HTTPStatus.BAD_REQUEST, "message is required")
                    return
                # 投递到常驻 loop:回复返回后,agent 起的后台抽取/复盘 task 仍能跑完
                # (不像 asyncio.run 那样在请求结束时把它们一起取消)。
                payload = self.server.bg.run(self.server.state.chat_payload(message))
                self._send_json(payload)
                return
            if path == "/api/vpet/event":
                req = VPetEventRequest.model_validate(data)
                payload = self.server.bg.run(
                    self.server.state.vpet_event_payload(
                        req,
                        client_flags=_parse_client_flags(
                            self.headers.get("X-MyBuddy-Client-Flags")
                        ),
                    )
                )
                self._send_json(payload)
                return
            if path == "/api/vpet/pending/drain":
                payload = self.server.state.vpet_pending_payload(
                    drain=True,
                    digest=bool(data.get("digest")),
                    client_flags=_parse_client_flags(self.headers.get("X-MyBuddy-Client-Flags")),
                )
                self._send_json(payload)
                return
            if path == "/api/feedback":
                label = str(data.get("label", "")).strip()
                if not label:
                    self._send_error(HTTPStatus.BAD_REQUEST, "label is required")
                    return
                payload = self.server.state.feedback_payload(label, data.get("turn_id"))
                self._send_json(payload)
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except PhysioBusyError as e:
            self._send_json(
                {
                    "ok": False,
                    "error": {"code": "physio_busy", "message": str(e)},
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        except RuntimeError as e:
            if path.startswith("/api/vpet/"):
                self._send_vpet_error("invalid_request", str(e))
            else:
                self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except ValueError as e:
            if path.startswith("/api/vpet/"):
                self._send_vpet_error("invalid_request", str(e))
            else:
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
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # 桌宠有明确请求超时;客户端先离开时不再尝试对同一 socket 回写 500。
            return

    def _send_error(self, status: HTTPStatus, detail: str) -> None:
        self._send_json({"detail": detail}, status=status)

    def _send_vpet_error(
        self,
        code: str,
        message: str,
        status: HTTPStatus = HTTPStatus.BAD_REQUEST,
    ) -> None:
        self._send_json(
            {"ok": False, "error": {"code": code, "message": message}},
            status=status,
        )

    def _authorize_request(self) -> bool:
        path = urlparse(self.path).path
        token = _bridge_token(self.server.state)
        if not token or not _requires_bridge_auth(path):
            return True
        if self.headers.get("X-MyBuddy-Token", "") == token:
            return True
        self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
        return False


def serve(
    *,
    config_path: str = "config.yaml",
    host: str = "127.0.0.1",
    port: int = 8000,
    max_steps: int = 6,
) -> None:
    state = AppState(config_path=config_path, max_steps=max_steps, enable_scheduler=False)
    state.startup()
    server = DemoServer((host, port), DemoHandler, state=state)
    try:
        server.serve_forever()
    finally:
        state.shutdown()
        server.server_close()
