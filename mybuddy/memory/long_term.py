"""三层结构化文本记忆。

存储层划分:

  - raw/: append-only 原始事件 JSONL,保留可追溯证据
  - conversations/: 按天整理的一轮对话 JSONL,供回放/摘要/抽取使用
  - archive/: 长期档案 Markdown + YAML frontmatter,供检索注入上下文

本模块保留原 LongTermMemory 的 add/search/delete/list_all 接口,让 Agent、画像、
notes 工具不需要关心底层已从向量库切到文本档案。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from mybuddy._time import utcnow

# 兼容旧构造参数。文本存储不再使用 embedding_fn。
EmbedFn = Callable[[list[str]], list[list[float]]]


class LongTermMemory:
    """基于结构化文本文件的长期记忆。

    archive 层的每条记录是一张 Markdown 档案卡:
      - frontmatter: id/type/tags/keywords/confidence/importance/证据等结构化字段
      - body: 人类可读的自然语言记忆正文
    """

    def __init__(
        self,
        persist_dir: str | Path,
        embedding_model: str = "BAAI/bge-m3",
        collection_name: str = "mybuddy_long_term",
        *,
        embedding_fn: EmbedFn | None = None,
    ) -> None:
        self._persist_dir = Path(persist_dir)
        self._embedding_model = embedding_model
        self._collection_name = collection_name
        self._embedding_fn = embedding_fn

        self._raw_dir = self._persist_dir / "raw"
        self._conversation_dir = self._persist_dir / "conversations"
        self._archive_dir = self._persist_dir / "archive"
        for d in (self._raw_dir, self._conversation_dir, self._archive_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # L0 原始数据层
    # ------------------------------------------------------------------

    def append_raw_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
    ) -> str:
        """追加一条原始事件,返回 event id。"""
        event_id = f"evt_{uuid.uuid4().hex}"
        now = utcnow()
        row = {
            "id": event_id,
            "turn_id": turn_id,
            "type": event_type,
            "payload": payload,
            "created_at": now.isoformat(timespec="seconds"),
        }
        self._append_jsonl(self._raw_dir / f"{now.date().isoformat()}.jsonl", row)
        return event_id

    # ------------------------------------------------------------------
    # L1 对话数据层
    # ------------------------------------------------------------------

    def record_conversation_turn(
        self,
        *,
        session_id: str,
        user_text: str,
        assistant_text: str,
        turn_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        """保存一轮整理后的对话数据,返回 turn id。"""
        tid = turn_id or f"turn_{uuid.uuid4().hex}"
        now = utcnow()
        row = {
            "turn_id": tid,
            "session_id": session_id,
            "user_text": user_text,
            "assistant_text": assistant_text,
            "summary": _compact_summary(user_text, assistant_text),
            "meta": meta or {},
            "created_at": now.isoformat(timespec="seconds"),
        }
        self._append_jsonl(self._conversation_dir / f"{now.date().isoformat()}.jsonl", row)
        self.append_raw_event("conversation_turn", row, turn_id=tid)
        return tid

    # ------------------------------------------------------------------
    # L2 档案数据层
    # ------------------------------------------------------------------

    def add(
        self,
        content: str,
        *,
        mem_type: str = "memory",
        session_id: str = "",
        uid: str | None = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> str:
        """写入一张档案卡,返回 id。"""
        uid = uid or uuid.uuid4().hex
        now = utcnow().isoformat(timespec="seconds")
        extra = dict(extra_meta or {})
        meta: dict[str, Any] = {
            "id": uid,
            "type": mem_type,
            "status": extra.pop("status", "active"),
            "session_id": session_id,
            "tags": _normalize_list(extra.pop("tags", [])),
            "keywords": _normalize_list(extra.pop("keywords", _extract_keywords(content))),
            "importance": float(extra.pop("importance", 0.5)),
            "confidence": float(extra.pop("confidence", 0.8)),
            "created_at": extra.pop("created_at", now),
            "updated_at": extra.pop("updated_at", now),
            **extra,
        }
        self._write_card(uid, meta, content)
        return uid

    def search(
        self,
        query: str,
        top_k: int = 3,
        *,
        mem_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """文本检索,返回 [{id, content, score, metadata}, ...]。"""
        query = (query or "").strip()
        if not query:
            return []
        q_tokens = set(_tokenize(query))
        if not q_tokens:
            return []

        hits: list[dict[str, Any]] = []
        for item in self.list_all(mem_type=mem_type):
            meta = item.get("metadata", {}) or {}
            if meta.get("status", "active") != "active":
                continue
            score = _score(query, q_tokens, item["content"], meta)
            if score <= 0:
                continue
            hits.append({**item, "score": score})

        hits.sort(
            key=lambda h: (
                h["score"],
                h.get("metadata", {}).get("importance", 0),
                h.get("metadata", {}).get("updated_at", ""),
            ),
            reverse=True,
        )
        return hits[:top_k]

    def delete(self, uid: str) -> None:
        """删除指定档案卡。"""
        path = self._card_path(uid)
        if path.exists():
            path.unlink()

    def update(
        self,
        uid: str,
        *,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """更新一张档案卡的正文和 metadata,返回更新后的卡片。"""
        loaded = self._read_card(uid)
        if loaded is None:
            return None
        old_meta, old_content = loaded
        new_content = old_content if content is None else content.strip()
        if not new_content:
            return None
        meta_updates = dict(metadata or {})
        meta_updates.pop("id", None)
        new_meta = {
            **old_meta,
            **meta_updates,
            "id": uid,
            "updated_at": utcnow().isoformat(timespec="seconds"),
        }
        if content is not None and "keywords" not in meta_updates:
            new_meta["keywords"] = _extract_keywords(new_content)
        self._write_card(uid, new_meta, new_content)
        return {"id": uid, "content": new_content, "metadata": new_meta}

    def update_metadata(self, uid: str, meta: dict[str, Any]) -> None:
        """合并更新一张档案卡的 metadata。"""
        loaded = self._read_card(uid)
        if loaded is None:
            return
        old_meta, content = loaded
        new_meta = {
            **old_meta,
            **meta,
            "id": uid,
            "updated_at": utcnow().isoformat(timespec="seconds"),
        }
        if "keywords" not in new_meta:
            new_meta["keywords"] = _extract_keywords(content)
        self._write_card(uid, new_meta, content)

    def count(self) -> int:
        return len(self.list_all())

    def list_all(
        self,
        *,
        mem_type: str | None = None,
        with_embeddings: bool = False,
    ) -> list[dict[str, Any]]:
        """列出所有档案卡。with_embeddings 仅为旧接口兼容,文本存储忽略它。"""
        out: list[dict[str, Any]] = []
        for path in sorted(self._archive_dir.glob("*.md")):
            loaded = self._read_card_by_path(path)
            if loaded is None:
                continue
            meta, content = loaded
            if mem_type is not None and meta.get("type") != mem_type:
                continue
            item: dict[str, Any] = {
                "id": meta.get("id") or path.stem,
                "content": content,
                "metadata": meta,
            }
            if with_embeddings:
                item["embedding"] = []
            out.append(item)
        return out

    # ------------------------------------------------------------------
    # 文件辅助
    # ------------------------------------------------------------------

    def _append_jsonl(self, path: Path, row: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _card_path(self, uid: str) -> Path:
        return self._archive_dir / f"{_safe_filename(uid)}.md"

    def _write_card(self, uid: str, meta: dict[str, Any], content: str) -> None:
        path = self._card_path(uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
        path.write_text(f"---\n{frontmatter}---\n\n{content.strip()}\n", encoding="utf-8")

    def _read_card(self, uid: str) -> tuple[dict[str, Any], str] | None:
        return self._read_card_by_path(self._card_path(uid))

    def _read_card_by_path(self, path: Path) -> tuple[dict[str, Any], str] | None:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return {"id": path.stem, "type": "memory"}, text.strip()
        try:
            _, raw_meta, body = text.split("---", 2)
            meta = yaml.safe_load(raw_meta) or {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, body.strip()
        except ValueError:
            return {"id": path.stem, "type": "memory"}, text.strip()


def _safe_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_") or uuid.uuid4().hex


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,，\s]+", value)
        return [p for p in (x.strip() for x in parts) if p]
    if isinstance(value, list | tuple | set):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _tokenize(text: str) -> list[str]:
    """中文按 1-2 字片段 + 英文数字词做轻量分词。"""
    tokens: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        for length in (1, 2):
            for i in range(len(chunk) - length + 1):
                tokens.append(chunk[i : i + length])
    tokens.extend(w.lower() for w in re.findall(r"[a-zA-Z0-9_]+", text))
    return tokens


def _extract_keywords(content: str, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    for token in _tokenize(content):
        if len(token) < 2 and not re.match(r"[a-zA-Z0-9_]+", token):
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def _score(query: str, q_tokens: set[str], content: str, meta: dict[str, Any]) -> float:
    tags = set(_normalize_list(meta.get("tags")))
    keywords = set(_normalize_list(meta.get("keywords")))
    text_tokens = set(_tokenize(content)) | keywords | tags
    overlap = q_tokens & text_tokens
    if not overlap and query not in content:
        return 0.0

    token_score = len(overlap) / max(len(q_tokens), 1)
    tag_score = len(q_tokens & tags) / max(len(q_tokens), 1)
    keyword_score = len(q_tokens & keywords) / max(len(q_tokens), 1)
    exact_bonus = 0.2 if query and query in content else 0.0
    importance = _clamp01(meta.get("importance", 0.5))
    confidence = _clamp01(meta.get("confidence", 0.8))
    return min(
        1.0,
        token_score * 0.45
        + tag_score * 0.15
        + keyword_score * 0.15
        + importance * 0.1
        + confidence * 0.1
        + exact_bonus,
    )


def _clamp01(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def _compact_summary(user_text: str, assistant_text: str, limit: int = 160) -> str:
    text = f"USER: {user_text.strip()} / AI: {assistant_text.strip()}"
    return text[:limit]
