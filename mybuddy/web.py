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
from mybuddy.auth.manager import AuthManager

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
            if path == "/api/profile":
                self._send_json(self.server.state.profile_payload())
                return
            if path == "/api/messages":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_json({"messages": []})
                    return
                self._send_json(
                    self.server.state.messages_payload(
                        limit=_first_int(query.get("limit"), default=100),
                        session_id=_first_str(query.get("session_id")),
                        user_id=user_id,
                        user_scoped=True,
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
            if path == "/api/auth/me":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_json({})
                    return
                user = self.server.state.auth.get_user(user_id) if self.server.state.auth else None
                self._send_json(user if user is not None else {})
                return
            if path == "/api/safety/resources":
                from mybuddy.safety.constants import HOTLINES
                self._send_json({"hotlines": HOTLINES})
                return
            if path == "/api/mood":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_json({"records": [], "daily_averages": []})
                    return
                self._send_json(self.server.state.mood_payload(
                    user_id, limit=_first_int(query.get("limit"), default=30)))
                return
            if path == "/api/mood/trends":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_json({"daily_averages": []})
                    return
                self._send_json(self.server.state.mood_trends_payload(
                    user_id, days=_first_int(query.get("days"), default=30)))
                return
            if path == "/api/mood/stats":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_json({"total_records": 0, "streak": 0, "categories": {}})
                    return
                self._send_json(self.server.state.mood_stats_payload(user_id))
                return
            if path == "/api/assessment/status":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_json({"phq9": [], "gad7": []})
                    return
                self._send_json(self.server.state.assessment_status_payload(user_id))
                return
            if path == "/api/assessment/history":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_json({"cycles": []})
                    return
                self._send_json(self.server.state.assessment_history_payload(user_id))
                return
            if path == "/api/cbt/status":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_json({"events": []})
                    return
                self._send_json(self.server.state.cbt_status_payload(user_id))
                return
            if path == "/api/user/export":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_error(HTTPStatus.UNAUTHORIZED, "请先登录")
                    return
                self._send_json(self.server.state.export_user_data_payload(user_id))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:  # noqa: BLE001
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_POST(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            if path == "/api/transcribe":
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0:
                    self._send_error(HTTPStatus.BAD_REQUEST, "empty body")
                    return
                audio_bytes = self.rfile.read(length)
                payload = self.server.bg.run(self.server.state.transcribe_payload(audio_bytes))
                self._send_json(payload)
                return
            data = self._read_json()
            if path == "/api/chat/reset":
                self._send_json(self.server.state.reset_chat_context())
                return
            if path == "/api/chat":
                message = str(data.get("message", "")).strip()
                if not message:
                    self._send_error(HTTPStatus.BAD_REQUEST, "message is required")
                    return
                # 投递到常驻 loop:回复返回后,agent 起的后台抽取/复盘 task 仍能跑完
                # (不像 asyncio.run 那样在请求结束时把它们一起取消)。
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                payload = self.server.bg.run(self.server.state.chat_payload(message, user_id=user_id))
                self._send_json(payload)
                return
            if path == "/api/auth/register":
                username = str(data.get("username", "")).strip()
                password = str(data.get("password", ""))
                if not username or not password:
                    self._send_error(HTTPStatus.BAD_REQUEST, "username and password required")
                    return
                try:
                    result = self.server.state.auth.register(username, password)
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Set-Cookie", AuthManager.make_cookie(result["user_id"]))
                    body = json.dumps({"user_id": result["user_id"], "username": result["username"]}, ensure_ascii=False).encode("utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except ValueError as e:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(e))
                    return
            if path == "/api/auth/login":
                username = str(data.get("username", "")).strip()
                password = str(data.get("password", ""))
                if not username or not password:
                    self._send_error(HTTPStatus.BAD_REQUEST, "username and password required")
                    return
                try:
                    result = self.server.state.auth.login(username, password)
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Set-Cookie", AuthManager.make_cookie(result["user_id"]))
                    body = json.dumps({"user_id": result["user_id"], "username": result["username"]}, ensure_ascii=False).encode("utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except ValueError as e:
                    self._send_error(HTTPStatus.UNAUTHORIZED, str(e))
                    return
            if path == "/api/auth/logout":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Set-Cookie", AuthManager.clear_cookie())
                body = json.dumps({"ok": True}).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/mood/checkin":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_error(HTTPStatus.UNAUTHORIZED, "请先登录")
                    return
                mood_score = int(data.get("mood_score", 5))
                notes = data.get("notes")
                payload = self.server.state.mood_checkin_payload(user_id, mood_score, notes)
                self._send_json(payload)
                return
            if path == "/api/messages/import":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_error(HTTPStatus.UNAUTHORIZED, "请先登录")
                    return
                messages = data.get("messages") or []
                if not isinstance(messages, list):
                    self._send_error(HTTPStatus.BAD_REQUEST, "messages must be a list")
                    return
                payload = self.server.state.import_messages_payload(user_id, messages)
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
            if path == "/api/user/data":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_error(HTTPStatus.UNAUTHORIZED, "请先登录")
                    return
                self._send_json(self.server.state.clear_user_data_payload(user_id))
                return
            if path == "/api/auth/account":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_error(HTTPStatus.UNAUTHORIZED, "请先登录")
                    return
                self._send_json(self.server.state.delete_account_payload(user_id))
                return
            if path == "/api/assessment/status":
                user_id = _get_user_id_from_request(self.headers.get("Cookie"))
                if user_id is None:
                    self._send_error(HTTPStatus.UNAUTHORIZED, "请先登录")
                    return
                from mybuddy.assessment import ConversationalAssessmentTracker
                tracker = ConversationalAssessmentTracker(self.server.state.engine, user_id)
                tracker.reset_cycle()
                self._send_json({"ok": True})
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


def _get_user_id_from_request(cookie_header: str | None) -> int | None:
    """从 Cookie header 中提取已验证的 user_id。"""
    from mybuddy.auth.manager import get_user_id_from_cookie
    return get_user_id_from_cookie(cookie_header)
