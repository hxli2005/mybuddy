"""启动最小身体桥。"""

from __future__ import annotations

import argparse

from mybuddy import __version__


def main() -> None:
    parser = argparse.ArgumentParser(prog="mybuddy")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("version")
    web = subcommands.add_parser("web", help="启动唯一的 /api/body/step 本机桥")
    web.add_argument("--config", default="config.yaml")
    web.add_argument("--data-dir", default="data/mini")
    web.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "version":
        print(f"mybuddy {__version__}")
        return

    import uvicorn

    from mybuddy.body_api import create_body_app

    app = create_body_app(args.config, args.data_dir)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
