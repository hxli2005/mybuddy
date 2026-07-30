"""recall_memory 工具:让 Agent 能主动检索长期记忆。

在对话中,Agent 需要回忆之前聊过的内容时,调用此工具搜索长期记忆。
"""

from __future__ import annotations

import json
import re
from typing import Any

from mybuddy.memory.long_term import LongTermMemory

from .context import get_config, get_long_term
from .registry import tool

# 模块级注册 LongTermMemory 引用;CLI 启动时由 setup 注入
_ltm: LongTermMemory | None = None

# 饮食安全是需要覆盖多个约束的场景,不能只按全局相关度取 Top-K。复合请求里常同时
# 出现提醒、时间和项目词,这些内容可能把"香菜避雷"或"过敏"挤出结果。检测到点餐/
# 忌口意图后,分别为 anti_preference 与健康 profile 预留一个位置。
_DIETARY_INTENT_RE = re.compile(
    r"吃|喝|饭|餐|外卖|点单|点菜|食物|零食|饮食|忌口|过敏|雷区|避雷|不能碰"
)
_DIETARY_RISK_RE = re.compile(r"过敏|忌口|禁忌|不能吃|不能碰|避开|避免|不吃")
_DIETARY_QUERY_HINTS = "饮食 吃饭 点餐 外卖 食物 忌口 过敏 雷区 避雷 不吃 不能吃"


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


def _dietary_guardrail_hits(
    ltm: LongTermMemory,
    query: str,
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    """饮食复合意图的约束覆盖召回:优先保留一条忌口和一条健康风险。"""
    if top_k <= 0 or not _DIETARY_INTENT_RE.search(query):
        return []

    expanded = f"{query} {_DIETARY_QUERY_HINTS}"
    reserved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mem_type in ("anti_preference", "profile"):
        for hit in ltm.search(
            expanded,
            top_k=max(top_k, 3),
            mem_type=mem_type,
            use_semantic=True,
        ):
            uid = str(hit.get("id") or "")
            if not uid or uid in seen:
                continue
            if mem_type == "profile":
                meta = hit.get("metadata") or {}
                risk_text = " ".join(
                    (
                        str(hit.get("content") or ""),
                        " ".join(str(x) for x in meta.get("tags") or []),
                        " ".join(str(x) for x in meta.get("keywords") or []),
                    )
                )
                if not _DIETARY_RISK_RE.search(risk_text):
                    continue
            seen.add(uid)
            reserved.append(hit)
            break
        if len(reserved) >= top_k:
            break
    return reserved


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
    top_k = cfg.memory.long_term_top_k
    reserved = _dietary_guardrail_hits(ltm, query, top_k=top_k)
    hits: list[dict[str, Any]] = []
    seen: set[str] = {
        str(hit.get("id") or "") for hit in reserved if str(hit.get("id") or "")
    }
    for mem_type in (
        "open_thread",
        "shared_moment",
        "preference",
        "anti_preference",
        "profile",
        "memory",
        "entity",
    ):
        # use_semantic=True:挂了语义召回时走词法+向量 RRF 融合,补回换词召回。
        # 评测显示换词类 query 的 MRR 由此 +0.28(见 eval/RESULTS.md 第 1 轮)。
        for hit in ltm.search(
            query, top_k=top_k, mem_type=mem_type, use_semantic=True
        ):
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
    # 先覆盖饮食安全约束,再用普通相关度结果补满。这样复合请求中的提醒/时间词不会
    # 把花生过敏或香菜忌口挤出 Top-K。
    hits = (reserved + hits)[:top_k]
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
