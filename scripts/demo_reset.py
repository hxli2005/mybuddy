"""一键完整重置演示环境:杀掉应用 → 重灌演示数据 → 新窗口重启应用。

用法: python scripts/demo_reset.py

覆盖 ↺ 按钮管不到的一切:聊天记录回到 220 条预置故事线、短期记忆/警戒窗口
清零(进程重启)、心情/安全/CBT/评估记录回到 seed 状态、admin 自动重建。
浏览器侧的演示按钮序号仍需手动点 ↺ 归零(localStorage 在浏览器里,脚本够不到)。
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PORT = 8000


def kill_app() -> None:
    out = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True
    ).stdout
    pids = set()
    for line in out.splitlines():
        if f":{PORT}" in line and "LISTENING" in line:
            m = re.search(r"(\d+)\s*$", line)
            if m:
                pids.add(m.group(1))
    for pid in pids:
        print(f"[reset] 停止应用进程 PID {pid}")
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    if pids:
        time.sleep(1.5)  # 等文件句柄释放,否则 seed 删库会撞 Windows 文件锁


def reseed() -> None:
    print("[reset] 重灌演示数据……")
    r = subprocess.run([str(PY), "scripts/seed_demo.py"], cwd=ROOT)
    if r.returncode != 0:
        sys.exit("[reset] seed 失败,中止(应用未启动)")


def relaunch() -> None:
    print("[reset] 在新窗口重启应用……")
    subprocess.Popen(
        [str(PY), "-m", "mybuddy.cli", "web", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    for _ in range(30):
        time.sleep(1)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=2):
                print("[reset] 应用已就绪 ✓")
                return
        except Exception:
            continue
    sys.exit("[reset] 应用 30 秒内未起来,请手动检查新窗口的报错")


if __name__ == "__main__":
    kill_app()
    reseed()
    relaunch()
    print("\n完成。最后两步在浏览器里做:")
    print("  1. 刷新 deck 页面(iframe 重新加载干净的聊天)")
    print("  2. 演示页里点 ↺ 把按钮序号归零")
