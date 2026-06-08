"""recall_memory 工具:让 Agent 能主动检索长期记忆。

在对话中,Agent 需要回忆之前聊过的内容时,调用此工具搜索长期记忆。
"""

from __future__ import annotations

import json
from typing import Any

from mybuddy.memory.long_term import LongTermMemory

from .context import get_config, get_long_term
from .registry import tool

# 模块级注册 LongTermMemory 引用;CLI 启动时由 setup 注入
_ltm: LongTermMemory | None = None


def setup_memory_tool(ltm: LongTermMemory) -> None:
    """注入 LongTermMemory 实例。在 CLI/测试启动时调用。

    同时也写入 ToolContext,其它工具(如 notes)可通过 `get_long_term()` 复用。
    """
    global _ltm
    _ltm = ltm
    # 同步到 ToolContext 供其它工具复用
    from .context import set_context

    set_context(long_term=ltm)


def _get_ltm() -> LongTermMemory:
    try:
        return get_long_term()
    except RuntimeError:
        if _ltm is None:
            raise RuntimeError("LongTermMemory 未注入,请先调用 setup_memory_tool()") from None
        return _ltm


@tool(
    name="recall_memory",
    description=(
        "搜索与你(用户)过去对话中存储的相关记忆。"
        "当用户问及之前聊过的事情、需要回忆历史信息时使用。"
    ),
)
def recall_memory(query: str) -> str:
    """搜索长期记忆。

    参数:
      query: 搜索关键词或问题(自然语言)
    """
    ltm = _get_ltm()
    cfg = get_config()
    hits = []
    seen: set[str] = set()
    for mem_type in ("open_thread", "shared_moment", "preference", "profile", "memory"):
        for hit in ltm.search(query, top_k=cfg.memory.long_term_top_k, mem_type=mem_type):
            uid = str(hit.get("id") or "")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            hits.append(hit)
    hits.sort(
        key=lambda h: (
            h.get("score", 0),
            (h.get("metadata") or {}).get("importance", 0),
            (h.get("metadata") or {}).get("updated_at", ""),
        ),
        reverse=True,
    )
    hits = hits[:cfg.memory.long_term_top_k]
    if not hits:
        return "没有找到相关记忆。"

    results: list[dict[str, Any]] = []
    for h in hits:
        results.append({
            "id": h["id"],
            "content": h["content"],
            "relevance": round(h["score"], 3),
        })
    return json.dumps(results, ensure_ascii=False, default=str)
