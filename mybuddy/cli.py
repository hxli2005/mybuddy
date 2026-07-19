"""启动最小身体桥。"""

from __future__ import annotations

import argparse
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mybuddy import __version__


def main() -> None:
    parser = argparse.ArgumentParser(prog="mybuddy")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("version")
    web = subcommands.add_parser("web", help="启动唯一的 /api/body/step 本机桥")
    web.add_argument("--config", default="config.yaml")
    web.add_argument("--data-dir", default="data/mini")
    web.add_argument("--port", type=int, default=8000)
    web.add_argument("--reading-file")
    web.add_argument("--parent-pid", type=int)
    args = parser.parse_args()

    if args.command == "version":
        print(f"mybuddy {__version__}")
        return

    import uvicorn

    from mybuddy.body_api import create_body_app

    with _single_writer(Path(args.data_dir)):
        options = {"reading_path": args.reading_file} if args.reading_file else {}
        app = create_body_app(args.config, args.data_dir, **options)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info")
        )
        if args.parent_pid:
            threading.Thread(
                target=_watch_parent,
                args=(server, args.parent_pid),
                daemon=True,
            ).start()
        server.run()


@contextmanager
def _single_writer(data_dir: Path) -> Iterator[None]:
    """Windows 文件锁：端口之外，同一数据目录也只允许一个写者。"""
    import msvcrt

    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / ".writer.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise SystemExit("数据目录已由另一个 MyBuddy 心智桥使用。") from error
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _watch_parent(server: object, parent_pid: int) -> None:
    while _process_is_running(parent_pid):
        time.sleep(1)
    server.should_exit = True  # type: ignore[attr-defined]


def _process_is_running(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, process_id)
        if not handle:
            return ctypes.windll.kernel32.GetLastError() == 5
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(process_id, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


if __name__ == "__main__":
    main()
