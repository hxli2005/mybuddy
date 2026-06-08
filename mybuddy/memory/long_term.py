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
from mybuddy.memory.governance import make_memory_key

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

        # 档案卡读缓存:path -> (st_mtime_ns, meta, content)。
        # search/list_all 每轮多次全量扫描,缓存消除重复 read_text + yaml 解析。
        # 写入/删除时按 key 失效;mtime 变化也会自动 miss,双保险。
        self._card_cache: dict[str, tuple[int, dict[str, Any], str]] = {}

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
        key_meta = dict(extra)
        meta: dict[str, Any] = {
            "id": uid,
            "type": mem_type,
            "status": extra.pop("status", "active"),
            "session_id": session_id,
            "source": extra.pop("source", "manual"),
            "tags": _normalize_list(extra.pop("tags", [])),
            "keywords": _normalize_list(extra.pop("keywords", _extract_keywords(content))),
            "importance": float(extra.pop("importance", 0.5)),
            "confidence": float(extra.pop("confidence", 0.8)),
            "memory_key": extra.pop("memory_key", make_memory_key(mem_type, content, key_meta)),
            "observed_at": extra.pop("observed_at", now),
            "last_seen_at": extra.pop("last_seen_at", now),
            "occurrence_count": int(extra.pop("occurrence_count", 1)),
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
        self._card_cache.pop(str(path), None)

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
        if content is not None and "memory_key" not in meta_updates:
            new_meta["memory_key"] = make_memory_key(
                str(new_meta.get("type") or "memory"),
                new_content,
                new_meta,
            )
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

    def normalize_metadata(self) -> int:
        """补齐旧档案卡缺失的治理字段,返回更新数量。"""
        now = utcnow().isoformat(timespec="seconds")
        count = 0
        for item in self.list_all():
            uid = str(item.get("id") or "")
            content = str(item.get("content") or "")
            meta = dict(item.get("metadata") or {})
            mem_type = str(meta.get("type") or "memory")
            created_at = str(meta.get("created_at") or meta.get("updated_at") or now)
            updates: dict[str, Any] = {}

            defaults = {
                "id": uid,
                "type": mem_type,
                "status": "active",
                "source": "legacy",
                "tags": [],
                "keywords": _extract_keywords(content),
                "importance": 0.5,
                "confidence": 0.8,
                "memory_key": make_memory_key(mem_type, content, meta),
                "observed_at": created_at,
                "last_seen_at": str(meta.get("updated_at") or created_at),
                "occurrence_count": 1,
                "created_at": created_at,
                "updated_at": str(meta.get("updated_at") or created_at),
            }
            for key, value in defaults.items():
                if meta.get(key) in (None, "", []):
                    updates[key] = value
            if not updates:
                continue
            self._write_card(uid, {**meta, **updates}, content)
            count += 1
        return count

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
        self._card_cache.pop(str(path), None)

    def _read_card(self, uid: str) -> tuple[dict[str, Any], str] | None:
        return self._read_card_by_path(self._card_path(uid))

    def _read_card_by_path(self, path: Path) -> tuple[dict[str, Any], str] | None:
        if not path.exists():
            self._card_cache.pop(str(path), None)
            return None
        key = str(path)
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = -1
        cached = self._card_cache.get(key)
        if cached is not None and cached[0] == mtime:
            # 返回 meta 副本,避免调用方就地改动污染缓存。
            return dict(cached[1]), cached[2]

        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            meta: dict[str, Any] = {"id": path.stem, "type": "memory"}
            body = text.strip()
        else:
            try:
                _, raw_meta, body_raw = text.split("---", 2)
                loaded = yaml.safe_load(raw_meta) or {}
                meta = loaded if isinstance(loaded, dict) else {}
                body = body_raw.strip()
            except ValueError:
                meta = {"id": path.stem, "type": "memory"}
                body = text.strip()
        self._card_cache[key] = (mtime, meta, body)
        return dict(meta), body


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


# 停用词:只放真正无区分度的功能词/高频噪声。它们几乎在所有句子里都出现,留着只会
# 稀释 token_score 的分母、拉低真正命中的相关度。不含任何有语义区分的词(如"喜欢"
# "周末""拖延"),也不含单独的内容字,确保 1-2 字 n-gram 召回不受影响。
_STOPWORDS = frozenset(
    {
        "的", "了", "是", "我", "你", "他", "她", "它", "们", "在", "有", "和", "或",
        "就", "也", "都", "会", "要", "把", "被", "让", "给", "跟", "对", "为", "与",
        "及", "等", "这", "那", "个", "吗", "呢", "吧", "啊", "呀", "嘛", "哦", "噢",
        "嗯", "么", "很", "太", "挺", "啦", "哈", "呵", "嘞", "哟", "之", "其",
        "什么", "怎么", "可以", "可能", "一个", "一下", "已经", "还是", "但是", "因为",
        "所以", "如果", "然后", "觉得", "知道", "这个", "那个", "自己", "我们", "你们",
        "他们", "这样", "那样", "一些", "有点", "还有", "就是", "不是", "这种", "那种",
        "the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "and", "or",
        "in", "on", "at", "for", "it", "this", "that", "i", "you", "he", "she", "we",
        "they", "my", "your", "with", "as", "by",
    }
)


def _tokenize(text: str) -> list[str]:
    """中文按 1-2 字片段 + 英文数字词做轻量分词,过滤停用词。"""
    tokens: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        for length in (1, 2):
            for i in range(len(chunk) - length + 1):
                tokens.append(chunk[i : i + length])
    tokens.extend(w.lower() for w in re.findall(r"[a-zA-Z0-9_]+", text))
    return [t for t in tokens if t not in _STOPWORDS]


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
