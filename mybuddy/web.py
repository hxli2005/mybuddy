"""无额外依赖的演示 Web 服务。

默认用于 `mybuddy web`:标准库 HTTPServer 托管前端静态文件和 JSON API。
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import threading
from collections.abc import Awaitable
from concurrent.futures import TimeoutError as FutureTimeoutError
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from mybuddy.api import AppState, _frontend_index_path, _frontend_not_built_html

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
    def __init__(self, server_address, handler, *, state: AppState, frontend_dir: Path):
        super().__init__(server_address, handler)
        self.state = state
        self.frontend_dir = frontend_dir
        self.bg = _BackgroundLoop()

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
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/":
                index = _frontend_index_path(self.server.frontend_dir)
                if index is None:
                    # 前端未构建:给可读提示页(503),而不是莫名的 404 file not found。
                    self._send_html(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        _frontend_not_built_html(self.server.frontend_dir),
                    )
                else:
                    self._send_file(index)
                return
            if path.startswith("/static/"):
                name = unquote(path.removeprefix("/static/"))
                dist_path = self.server.frontend_dir / "dist" / name
                legacy_path = self.server.frontend_dir / name
                self._send_file(dist_path if dist_path.exists() else legacy_path)
                return
            if path == "/api/status":
                self._send_json(self.server.state.status_payload())
                return
            if path == "/api/vpet/status":
                self._send_json(self.server.state.vpet_status_payload())
                return
            if path == "/api/vpet/pending":
                self._send_json(self.server.state.vpet_pending_payload(drain=False))
                return
            if path == "/api/persona":
                self._send_json(self.server.state.persona_payload())
                return
            if path == "/api/profile":
                self._send_json(self.server.state.profile_payload())
                return
            if path == "/api/messages":
                self._send_json(
                    self.server.state.messages_payload(
                        limit=_first_int(query.get("limit"), default=100),
                        session_id=_first_str(query.get("session_id")),
                    )
                )
                return
            if path == "/api/memory":
                self._send_json(self.server.state.memory_payload())
                return
            if path == "/api/reminders":
                self._send_json(self.server.state.reminders_payload())
                return
            if path == "/api/skills":
                self._send_json(self.server.state.skills_payload())
                return
            if path == "/api/notes":
                self._send_json(self.server.state.notes_payload())
                return
            if path == "/api/users":
                self._send_json(self.server.state.users_payload())
                return
            user_persona_id = _match_user_persona_route(path)
            if user_persona_id is not None:
                self._send_json(self.server.state.user_persona_payload(user_persona_id))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:  # noqa: BLE001
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_POST(self) -> None:  # noqa: N802
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
            if path == "/api/vpet/chat":
                message = str(data.get("message", "")).strip()
                if not message:
                    self._send_error(HTTPStatus.BAD_REQUEST, "message is required")
                    return
                event = str(data.get("event", "chat")).strip() or "chat"
                payload = self.server.bg.run(
                    self.server.state.vpet_chat_payload(message, event=event)
                )
                self._send_json(payload)
                return
            if path == "/api/vpet/pending/drain":
                payload = self.server.state.vpet_pending_payload(drain=True)
                self._send_json(payload)
                return
            if path == "/v1/chat/completions":
                if bool(data.get("stream")):
                    self._send_error(HTTPStatus.BAD_REQUEST, "stream=true 暂不支持")
                    return
                messages = data.get("messages")
                if not isinstance(messages, list):
                    self._send_error(HTTPStatus.BAD_REQUEST, "messages is required")
                    return
                payload = self.server.bg.run(
                    self.server.state.openai_chat_completion_payload(
                        messages=messages,
                        model=str(data.get("model", "mybuddy")),
                    )
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
            if path == "/api/persona":
                payload = self.server.state.update_persona_payload(data)
                self._send_json(payload)
                return
            if path == "/api/notes":
                payload = self.server.state.create_note_payload(
                    content=str(data.get("content", "")),
                    title=data.get("title"),
                    tags=data.get("tags"),
                )
                self._send_json(payload)
                return
            if path == "/api/users":
                payload = self.server.state.create_user_payload(
                    display_name=str(data.get("display_name", "")),
                    daily_message_limit=int(data.get("daily_message_limit", 30)),
                )
                self._send_json(payload)
                return
            user_qq_id = _match_user_qq_route(path)
            if user_qq_id is not None:
                payload = self.server.state.bind_user_qq_payload(
                    user_qq_id,
                    external_id=str(data.get("external_id", "")),
                    display_name=data.get("display_name"),
                )
                self._send_json(payload)
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except RuntimeError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:  # noqa: BLE001
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_PUT(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            data = self._read_json()
            user_persona_id = _match_user_persona_route(path)
            if user_persona_id is not None:
                payload = self.server.state.update_user_persona_payload(user_persona_id, data)
                self._send_json(payload)
                return
            if path == "/api/persona":
                payload = self.server.state.update_persona_payload(data)
                self._send_json(payload)
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except RuntimeError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:  # noqa: BLE001
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_PATCH(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            data = self._read_json()
            user_id = _match_user_route(path)
            if user_id is not None:
                payload = self.server.state.update_user_payload(
                    user_id,
                    status=data.get("status"),
                    daily_message_limit=data.get("daily_message_limit"),
                )
                self._send_json(payload)
                return
            if path.startswith("/api/profile/fields/"):
                key = unquote(path.removeprefix("/api/profile/fields/"))
                payload = self.server.state.update_profile_field_payload(
                    key,
                    str(data.get("value", "")),
                )
                self._send_json(payload)
                return
            if path.startswith("/api/memory/archive/"):
                memory_id = unquote(path.removeprefix("/api/memory/archive/"))
                payload = self.server.state.update_memory_payload(
                    memory_id,
                    content=data.get("content"),
                    metadata=data.get("metadata"),
                )
                self._send_json(payload)
                return
            if path.startswith("/api/notes/"):
                note_id = int(path.removeprefix("/api/notes/"))
                payload = self.server.state.update_note_payload(
                    note_id,
                    content=data.get("content"),
                    title=data.get("title"),
                    tags=data.get("tags"),
                )
                self._send_json(payload)
                return
            if path.startswith("/api/reminders/"):
                reminder_id = int(path.removeprefix("/api/reminders/"))
                payload = self.server.state.update_reminder_payload(
                    reminder_id,
                    str(data.get("status", "")),
                )
                self._send_json(payload)
                return
            if path.startswith("/api/skills/"):
                name = unquote(path.removeprefix("/api/skills/"))
                payload = self.server.state.update_skill_payload(name, data.get("archived"))
                self._send_json(payload)
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except RuntimeError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:  # noqa: BLE001
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            user_persona_id = _match_user_persona_route(path)
            if user_persona_id is not None:
                self._send_json(self.server.state.delete_user_persona_payload(user_persona_id))
                return
            if path.startswith("/api/profile/fields/"):
                key = unquote(path.removeprefix("/api/profile/fields/"))
                self._send_json(self.server.state.delete_profile_field_payload(key))
                return
            if path.startswith("/api/memory/archive/"):
                memory_id = unquote(path.removeprefix("/api/memory/archive/"))
                self._send_json(self.server.state.delete_memory_payload(memory_id))
                return
            if path.startswith("/api/notes/"):
                note_id = int(path.removeprefix("/api/notes/"))
                self._send_json(self.server.state.delete_note_payload(note_id))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except RuntimeError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except ValueError as e:
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

    def _send_html(self, status: HTTPStatus, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
    if _frontend_index_path(frontend_dir) is None:
        # 非致命:dev 下前端走 `npm run dev`(Vite 代理 /api 到本服务),API 仍需可用。
        logger.warning(
            "前端未构建(%s 不存在):/ 会返回‘前端未构建’提示页。要由本服务托管前端,"
            "先 `cd frontend && npm run build`;本地开发用 `npm run dev`(Vite 代理 /api 到此)。",
            frontend_dir / "dist" / "index.html",
        )
    state = AppState(config_path=config_path, max_steps=max_steps, enable_scheduler=False)
    state.startup()
    server = DemoServer((host, port), DemoHandler, state=state, frontend_dir=frontend_dir)
    try:
        server.serve_forever()
    finally:
        state.shutdown()
        server.server_close()


def _match_user_route(path: str) -> int | None:
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[:2] == ["api", "users"]:
        return int(parts[2])
    return None


def _match_user_qq_route(path: str) -> int | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[:2] == ["api", "users"] and parts[3] == "qq":
        return int(parts[2])
    return None


def _match_user_persona_route(path: str) -> int | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[:2] == ["api", "users"] and parts[3] == "persona":
        return int(parts[2])
    return None


def _first_int(values: list[str] | None, *, default: int) -> int:
    if not values:
        return default
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return default


def _first_str(values: list[str] | None) -> str | None:
    if not values:
        return None
    clean = values[0].strip()
    return clean or None
