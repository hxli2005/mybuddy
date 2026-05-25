"""notes 工具:write_note / search_notes。

落盘策略:
  - SQLite 里的 Note 表是事务性主存(保证不丢)
  - 写完同步 add 到长期档案文本存储(`mem_type="note"`, uid=`note_{sql_id}`),检索能命中
  - 档案写入失败不回滚 SQLite;极端情况下可以用 dream job 补索引(M8 的事)

search_notes 与 recall_memory 的区别:
  - recall_memory: 检索 `mem_type="memory"`(agent 抽取的事实 + 历史对话片段)
  - search_notes:  检索 `mem_type="note"`(用户显式"记下来"的东西)

一条笔记的价值:用户主动固化的信息,权重比对话抽取更高。LLM 应当优先引用。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mybuddy.storage import Note, session_scope

from .context import get_engine, get_long_term
from .registry import tool

logger = logging.getLogger(__name__)


def _archive_uid(sql_id: int) -> str:
    return f"note_{sql_id}"


@tool(
    name="write_note",
    description=(
        "把用户想要记下来的内容保存为一条笔记(可选标题和标签)。"
        "适合用户说『帮我记一下』『把这个保存起来』这类场景。"
    ),
)
def write_note(content: str, title: str = "", tags: list[str] | None = None) -> dict:
    """保存一条笔记。

    参数:
      content: 笔记正文
      title: 可选标题(≤128 字),不填则自动截取正文首 30 字
      tags: 可选标签列表,如 ["工作", "灵感"]
    """
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "笔记内容为空"}

    title = (title or "").strip() or content[:30]
    tag_list = [t.strip() for t in (tags or []) if t and t.strip()]
    tags_json = json.dumps(tag_list, ensure_ascii=False) if tag_list else None

    engine = get_engine()
    with session_scope(engine) as s:
        note = Note(title=title, content=content, tags_json=tags_json)
        s.add(note)
        s.flush()
        note_id = note.id

    # 同步索引到长期档案层(失败不抛,主存已落盘)
    try:
        ltm = get_long_term()
        ltm.add(
            content,
            mem_type="note",
            uid=_archive_uid(note_id),
            extra_meta={"sql_id": note_id, "title": title, "tags": ",".join(tag_list)},
        )
    except Exception:  # noqa: BLE001
        logger.exception("notes: 档案索引失败,笔记已存 SQLite")

    return {
        "ok": True,
        "id": note_id,
        "title": title,
        "tags": tag_list,
    }


@tool(
    name="search_notes",
    description=(
        "在用户的笔记里做语义搜索,返回相关的笔记条目。"
        "当用户问『我之前记过什么』或『关于 X 我记录了什么』时使用。"
    ),
)
def search_notes(query: str, top_k: int = 5) -> str:
    """搜索笔记(语义)。

    参数:
      query: 搜索关键词或问题
      top_k: 返回条数上限(默认 5)
    """
    query = (query or "").strip()
    if not query:
        return "查询为空。"

    try:
        ltm = get_long_term()
    except RuntimeError:
        return "笔记检索未初始化。"

    hits = ltm.search(query, top_k=top_k, mem_type="note")
    if not hits:
        return "没有相关笔记。"

    engine = get_engine()
    sql_ids = [
        h.get("metadata", {}).get("sql_id")
        for h in hits
        if isinstance(h.get("metadata", {}).get("sql_id"), int)
    ]
    notes_by_id: dict[int, Note] = {}
    if sql_ids:
        with session_scope(engine) as s:
            rows = s.query(Note).filter(Note.id.in_(sql_ids)).all()
            notes_by_id = {n.id: n for n in rows}

    results: list[dict[str, Any]] = []
    for h in hits:
        meta = h.get("metadata", {}) or {}
        sid = meta.get("sql_id")
        note_row = notes_by_id.get(sid) if isinstance(sid, int) else None
        tags_raw = meta.get("tags", "") or ""
        tags = tags_raw if isinstance(tags_raw, list) else tags_raw.split(",")
        results.append(
            {
                "id": sid,
                "title": meta.get("title") or (note_row.title if note_row else ""),
                "content": h["content"],
                "tags": [t for t in tags if t],
                "created_at": note_row.created_at.isoformat() if note_row else None,
                "relevance": round(h["score"], 3),
            }
        )
    return json.dumps(results, ensure_ascii=False, default=str)
