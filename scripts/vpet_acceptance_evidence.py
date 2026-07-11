"""导出 VPet v1 验收所需的只读记忆证据。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mybuddy.config import load_config
from mybuddy.memory import LongTermMemory


def shared_moments(
    memory_dir: str | Path,
    *,
    local_date: str,
    source: str,
) -> list[dict[str, Any]]:
    ltm = LongTermMemory(persist_dir=memory_dir)
    output: list[dict[str, Any]] = []
    for item in ltm.list_all(mem_type="shared_moment"):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if str(metadata.get("date") or "") != local_date:
            continue
        if str(metadata.get("source") or "") != source:
            continue
        output.append(item)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)
    shared = subparsers.add_parser("shared-moments")
    shared.add_argument("--date", required=True)
    shared.add_argument("--source", required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    if args.command == "shared-moments":
        payload = shared_moments(
            config.paths.chroma_dir,
            local_date=args.date,
            source=args.source,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
