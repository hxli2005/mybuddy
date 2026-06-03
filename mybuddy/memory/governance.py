"""记忆治理层:去重、合并、时间与生命周期元数据。

这层不替代 LongTermMemory 的文件存储,而是在写入前后做产品语义上的治理:
  - 为记忆生成稳定的 memory_key
  - 自动补 observed_at / last_seen_at / source / occurrence_count
  - 相同主题的自动记忆优先合并,避免 archive 堆出重复卡片
  - open_thread 支持 expires_at 过期后转为 stale
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from mybuddy._time import utcnow

if TYPE_CHECKING:
    from mybuddy.memory.long_term import LongTermMemory


MERGEABLE_TYPES = {
    "memory",
    "claim",
    "shared_moment",
    "open_thread",
    "private_code",
    "anti_preference",
    "relationship_note",
    "character_note",
}

TERMINAL_STATUSES = {"resolved", "archived", "deleted"}


@dataclass(frozen=True)
class GovernanceResult:
    action: str
    memory_id: str
    item: dict[str, Any] | None = None


class MemoryGovernance:
    """LongTermMemory 前置治理器。"""

    def __init__(self, ltm: LongTermMemory) -> None:
        self._ltm = ltm

    def add_or_merge(
        self,
        content: str,
        *,
        mem_type: str = "memory",
        session_id: str = "",
        source: str = "extraction",
        uid: str | None = None,
        extra_meta: dict[str, Any] | None = None,
        merge: bool = True,
    ) -> GovernanceResult:
        clean_content = (content or "").strip()
        if not clean_content:
            raise ValueError("memory content is empty")

        now = utcnow().isoformat(timespec="seconds")
        meta = governance_metadata(
            clean_content,
            mem_type=mem_type,
            source=source,
            now=now,
            extra_meta=extra_meta,
        )

        if uid is None and merge and mem_type in MERGEABLE_TYPES:
            existing = self._find_existing(mem_type, clean_content, meta)
            if existing is not None:
                merged_meta = merge_metadata(existing.get("metadata") or {}, meta, now=now)
                merged_content = _choose_content(existing.get("content", ""), clean_content)
                updated = self._ltm.update(
                    existing["id"],
                    content=merged_content,
                    metadata=merged_meta,
                )
                return GovernanceResult("merged", existing["id"], updated)

        memory_id = self._ltm.add(
            clean_content,
            mem_type=mem_type,
            session_id=session_id,
            uid=uid,
            extra_meta=meta,
        )
        return GovernanceResult("created", memory_id, self._find_by_id(memory_id))

    def refresh_open_thread_lifecycle(self) -> int:
        """将已过期 open_thread 标为 stale,返回更新数量。"""
        now = utcnow()
        count = 0
        for item in self._ltm.list_all(mem_type="open_thread"):
            meta = item.get("metadata") or {}
            if meta.get("status", "active") != "active":
                continue
            expires_at = _parse_iso(meta.get("expires_at"))
            if expires_at is None or expires_at > now:
                continue
            self._ltm.update_metadata(
                item["id"],
                {
                    "status": "stale",
                    "stale_at": now.isoformat(timespec="seconds"),
                    "stale_reason": "expires_at passed",
                },
            )
            count += 1
        return count

    def _find_existing(
        self,
        mem_type: str,
        content: str,
        meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        key = meta.get("memory_key")
        if key:
            for item in self._ltm.list_all(mem_type=mem_type):
                item_meta = item.get("metadata") or {}
                if item_meta.get("status", "active") in TERMINAL_STATUSES:
                    continue
                if item_meta.get("memory_key") == key:
                    return item

        threshold = _similarity_threshold(mem_type)
        for hit in self._ltm.search(content, top_k=3, mem_type=mem_type):
            hit_meta = hit.get("metadata") or {}
            if hit_meta.get("status", "active") in TERMINAL_STATUSES:
                continue
            if hit.get("score", 0) >= threshold:
                return hit
        return None

    def _find_by_id(self, memory_id: str) -> dict[str, Any] | None:
        for item in self._ltm.list_all():
            if item.get("id") == memory_id:
                return item
        return None


def governance_metadata(
    content: str,
    *,
    mem_type: str,
    source: str,
    now: str | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = now or utcnow().isoformat(timespec="seconds")
    meta = dict(extra_meta or {})
    meta.setdefault("source", source)
    meta.setdefault("observed_at", current)
    meta.setdefault("last_seen_at", current)
    meta.setdefault("occurrence_count", 1)
    meta.setdefault("memory_key", make_memory_key(mem_type, content, meta))
    if mem_type == "open_thread":
        meta.setdefault("status", "active")
    return meta


def merge_metadata(old: dict[str, Any], new: dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
    current = now or utcnow().isoformat(timespec="seconds")
    merged = dict(old)

    for key, value in new.items():
        if key in {"id", "created_at", "updated_at"}:
            continue
        if key in {"tags", "keywords", "triggers", "source_turn_ids"}:
            merged[key] = _merge_list(merged.get(key), value)
            continue
        if key in {"confidence", "importance"}:
            merged[key] = max(_to_float(merged.get(key), 0.0), _to_float(value, 0.0))
            continue
        if key in {"occurrence_count"}:
            merged[key] = _to_int(merged.get(key), 1) + _to_int(value, 1)
            continue
        if key == "observed_at":
            merged[key] = min(str(merged.get(key) or value), str(value))
            continue
        if key == "last_seen_at":
            merged[key] = current
            continue
        if key == "source":
            merged["sources"] = _merge_list(merged.get("sources"), [merged.get("source"), value])
            merged.setdefault("source", value)
            continue
        if value not in (None, "", []):
            merged[key] = value

    merged["last_seen_at"] = current
    merged.setdefault("observed_at", current)
    merged.setdefault("occurrence_count", 1)
    return merged


def make_memory_key(mem_type: str, content: str, metadata: dict[str, Any] | None = None) -> str:
    meta = metadata or {}
    basis_parts = [
        str(meta.get("title") or ""),
        " ".join(_as_list(meta.get("triggers"))),
        str(meta.get("contact_reason") or ""),
        content,
    ]
    basis = " ".join(part for part in basis_parts if part).lower()
    tokens = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", basis)
    pieces: list[str] = []
    for token in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            pieces.extend(_meaningful_zh_chunks(token))
        else:
            pieces.append(token)
    normalized = "".join(pieces)[:96]
    return f"{mem_type}:{normalized or 'empty'}"


def _meaningful_zh_chunks(text: str) -> list[str]:
    stop = set("用户我的我们一个这个那个最近今天明天昨天觉得可能可以需要正在")
    chunks = [chunk for chunk in re.split(r"[，。；、,.!?！？\s]+", text) if chunk]
    out: list[str] = []
    for chunk in chunks:
        if chunk in stop:
            continue
        if len(chunk) <= 8:
            out.append(chunk)
        else:
            out.extend(chunk[i : i + 4] for i in range(0, len(chunk), 4))
    return out[:16]


def _choose_content(old: str, new: str) -> str:
    old_clean = (old or "").strip()
    new_clean = (new or "").strip()
    if len(new_clean) > len(old_clean) + 8:
        return new_clean
    return old_clean or new_clean


def _similarity_threshold(mem_type: str) -> float:
    if mem_type in {"private_code", "anti_preference"}:
        return 0.82
    if mem_type in {"open_thread", "shared_moment"}:
        return 0.84
    return 0.88


def _merge_list(old: Any, new: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*_as_list(old), *_as_list(new)]:
        clean = str(item).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        merged.append(clean)
    return merged


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return list(value)
    if isinstance(value, str):
        return [part for part in re.split(r"[,，\s]+", value) if part]
    return [value]


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
