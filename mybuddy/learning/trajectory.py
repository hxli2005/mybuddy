"""轨迹采集(Trajectory Logger)。

设计方案要求第一天就开始采集 agent 轨迹,为未来本地 Hermes 模型的 SFT/DPO
微调做数据储备。M2 先落地最小可用版:每个 turn 一条 JSON line,按天分文件。

一个 "turn" 覆盖用户单次输入到 agent 给出最终回复的完整过程,包含中间所有
工具调用和工具结果。outcome_label 字段 M2 默认为 null,M5 引入 /good /bad
/fix 指令后回填。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mybuddy._time import utcnow


@dataclass
class TrajectoryStep:
    """一次 LLM 调用 + 可能的工具调用与结果。"""

    assistant_text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Trajectory:
    turn_id: str
    session_id: str
    started_at: str
    system: str
    user_input: str
    steps: list[TrajectoryStep] = field(default_factory=list)
    final_response: str = ""
    finish_reason: str = ""
    outcome_label: str | None = None  # good | bad | fix:<text> | None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "system": self.system,
            "user_input": self.user_input,
            "steps": [
                {
                    "assistant_text": s.assistant_text,
                    "tool_calls": s.tool_calls,
                    "tool_results": s.tool_results,
                }
                for s in self.steps
            ],
            "final_response": self.final_response,
            "finish_reason": self.finish_reason,
            "outcome_label": self.outcome_label,
            "meta": self.meta,
        }


class TrajectoryLogger:
    """按天追加 JSONL。线程不安全——M2 是单进程 CLI,够用;上 API 后改 aiofiles。"""

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def start(self, *, session_id: str, system: str, user_input: str) -> Trajectory:
        return Trajectory(
            turn_id=uuid.uuid4().hex,
            session_id=session_id,
            started_at=utcnow().isoformat(timespec="seconds"),
            system=system,
            user_input=user_input,
        )

    def commit(self, traj: Trajectory) -> Path:
        """写入当天文件,返回文件路径。"""
        today = utcnow().strftime("%Y-%m-%d")
        path = self._base_dir / f"{today}.jsonl"
        line = json.dumps(traj.to_json_dict(), ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return path

    def attach_label(self, turn_id: str, label: str, *, date: str | None = None) -> bool:
        """追加一条 label 记录,不改写原始行(保留历史证据)。

        真正把 label 合并到轨迹内,留给未来的离线导出管线处理。
        """
        today = date or utcnow().strftime("%Y-%m-%d")
        path = self._base_dir / f"{today}.labels.jsonl"
        line = json.dumps(
            {
                "turn_id": turn_id,
                "label": label,
                "labeled_at": utcnow().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
