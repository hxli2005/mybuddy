"""答辩演示助手:静态托管 defense_deck.html + 白名单命令流式执行。

用法:
    python scripts/demo_runner.py
    然后浏览器打开  http://127.0.0.1:8765/   (即答辩 deck,内嵌终端与应用实况)

只执行 COMMANDS 白名单里的两条命令,监听仅限 127.0.0.1。
"""
from __future__ import annotations

import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "docs" / "defense_deck.html"
PORT = 8765

COMMANDS: dict[str, list[str]] = {
    "tests": [
        "uv", "run", "--extra", "dev", "--extra", "api",
        "pytest", "-q", "--continue-on-collection-errors",
    ],
    "memory": ["uv", "run", "python", "eval/memory_eval.py", "--mode", "lexical"],
}


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0:响应不带长度,边执行边写,连接关闭即结束 —— fetch 可流式读取
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # 安静
        pass

    def _head(self, code: int = 200, ctype: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        # 允许 file:// 直接打开 deck 时跨源调用本助手
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path in ("/", "/deck"):
            self._head(200, "text/html; charset=utf-8")
            self.wfile.write(DECK.read_bytes())
        elif u.path == "/ping":
            self._head()
            self.wfile.write(b"ok")
        elif u.path == "/run":
            self._run((parse_qs(u.query).get("id") or [""])[0])
        else:
            self._head(404)
            self.wfile.write(b"not found")

    def _run(self, cmd_id: str) -> None:
        cmd = COMMANDS.get(cmd_id)
        if cmd is None:
            self._head(404)
            self.wfile.write(f"unknown command id: {cmd_id}".encode())
            return
        self._head()
        proc = None
        try:
            self.wfile.write(f"$ {' '.join(cmd)}\n\n".encode())
            self.wfile.flush()
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, b""):
                self.wfile.write(line)
                self.wfile.flush()
            proc.wait()
            self.wfile.write(f"\n[exit {proc.returncode}]\n".encode())
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    print(f"演示助手已启动: http://127.0.0.1:{PORT}/  (Ctrl+C 退出)")
    print(f"deck: {DECK}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
