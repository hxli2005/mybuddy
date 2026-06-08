"""可选的语义召回:API embedding + 旁路向量索引。

设计原则(不破坏纯文本档案的可审计性):
  - ``archive/*.md`` 仍是唯一真相;向量是**从 .md 派生的可弃缓存**,丢了能重建。
  - 默认关闭;关闭时整条链路零开销、零额外依赖,检索保持纯词法、纯离线。
  - 任何 embedding 失败都静默降级为纯词法,绝不阻断主流程。
  - 向量不进 frontmatter(会毁掉可读性/diff),单独放旁路 sqlite。

向量索引按 ``card_id`` 存归一化 float32(余弦 = 点积)+ ``content_hash``(判断卡片
是否变更需重嵌)+ ``mem_type``(按类型过滤候选)。当前规模直接暴力点积,无需 ANN。
"""

from __future__ import annotations

import array
import hashlib
import logging
import math
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from mybuddy.config import EmbeddingConfig

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    """embedding 客户端协议(便于测试注入假实现)。"""

    @property
    def ready(self) -> bool: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingClient:
    """OpenAI 兼容 ``/embeddings`` 客户端。失败返回空列表(由上层降级)。"""

    def __init__(self, cfg: EmbeddingConfig) -> None:
        self._model = cfg.model
        self._base = cfg.base_url.rstrip("/")
        self._key = cfg.api_key
        self._timeout = cfg.timeout

    @property
    def ready(self) -> bool:
        return bool(self._key and self._base and self._model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or not self.ready:
            return []
        try:
            resp = httpx.post(
                f"{self._base}/embeddings",
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
                json={"model": self._model, "input": texts},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            out = [[float(x) for x in d.get("embedding", [])] for d in data]
            if len(out) != len(texts) or any(not v for v in out):
                logger.warning("embedding response shape mismatch")
                return []
            return out
        except Exception:
            logger.warning("embedding request failed", exc_info=True)
            return []


class VectorIndex:
    """sqlite 旁路向量索引,按 card_id 存归一化向量。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS card_vectors ("
                "card_id TEXT PRIMARY KEY, content_hash TEXT, mem_type TEXT, vec BLOB)"
            )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def hashes(self) -> dict[str, str]:
        with self._conn() as c:
            return {
                str(row[0]): str(row[1])
                for row in c.execute("SELECT card_id, content_hash FROM card_vectors")
            }

    def upsert(self, card_id: str, content_hash: str, mem_type: str, vec: list[float]) -> None:
        blob = array.array("f", vec).tobytes()
        with self._conn() as c:
            c.execute(
                "INSERT INTO card_vectors (card_id, content_hash, mem_type, vec) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(card_id) DO UPDATE SET "
                "content_hash=excluded.content_hash, mem_type=excluded.mem_type, vec=excluded.vec",
                (card_id, content_hash, mem_type, blob),
            )

    def prune(self, keep: set[str]) -> int:
        with self._conn() as c:
            rows = [str(r[0]) for r in c.execute("SELECT card_id FROM card_vectors")]
            drop = [cid for cid in rows if cid not in keep]
            c.executemany("DELETE FROM card_vectors WHERE card_id = ?", [(cid,) for cid in drop])
            return len(drop)

    def load(self, *, mem_type: str | None = None) -> list[tuple[str, list[float]]]:
        with self._conn() as c:
            if mem_type is not None:
                cur = c.execute(
                    "SELECT card_id, vec FROM card_vectors WHERE mem_type = ?", (mem_type,)
                )
            else:
                cur = c.execute("SELECT card_id, vec FROM card_vectors")
            out: list[tuple[str, list[float]]] = []
            for card_id, blob in cur:
                a = array.array("f")
                a.frombytes(blob)
                out.append((str(card_id), list(a)))
            return out


class SemanticRecall:
    """把 embedding 客户端 + 向量索引组合成"重建 + 检索"两个动作。"""

    def __init__(
        self,
        cfg: EmbeddingConfig,
        index_path: str | Path,
        *,
        client: Embedder | None = None,
    ) -> None:
        self._cfg = cfg
        self._client: Embedder = client or EmbeddingClient(cfg)
        self._index = VectorIndex(index_path)

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.enabled and self._client.ready)

    @property
    def rrf_k(self) -> int:
        return self._cfg.rrf_k

    @property
    def candidate_multiplier(self) -> int:
        return max(1, self._cfg.candidate_multiplier)

    def reconcile(self, cards: list[dict]) -> int:
        """让向量索引与 .md 档案对齐:变更/缺失的重嵌,被删的丢弃。返回重嵌数量。

        幂等且廉价:无变化时只做 hash 比对。可放后台线程跑(网络调用)。
        """
        if not self.enabled:
            return 0
        existing = self._index.hashes()
        seen: set[str] = set()
        pending: list[tuple[str, str, str, str]] = []  # (card_id, mem_type, text, hash)
        for card in cards:
            card_id = str(card.get("id") or "")
            if not card_id:
                continue
            seen.add(card_id)
            text = _card_text(card)
            if not text:
                continue
            digest = _hash_text(text)
            if existing.get(card_id) != digest:
                mem_type = str((card.get("metadata") or {}).get("type") or "memory")
                pending.append((card_id, mem_type, text, digest))
        self._index.prune(seen)

        embedded = 0
        for i in range(0, len(pending), max(1, self._cfg.batch_size)):
            batch = pending[i : i + max(1, self._cfg.batch_size)]
            vecs = self._client.embed([text for _, _, text, _ in batch])
            if len(vecs) != len(batch):
                break  # 失败:本批跳过,下次 reconcile 再补
            for (card_id, mem_type, _, digest), vec in zip(batch, vecs, strict=False):
                self._index.upsert(card_id, digest, mem_type, _normalize(vec))
                embedded += 1
        return embedded

    def search(self, query: str, top_k: int, *, mem_type: str | None = None) -> list[tuple[str, float]]:
        """返回 [(card_id, cosine), ...],按相似度降序。失败/空返回空。"""
        if not self.enabled or not query.strip():
            return []
        qv = self._client.embed([query])
        if not qv:
            return []
        q = _normalize(qv[0])
        scored: list[tuple[str, float]] = []
        for card_id, vec in self._index.load(mem_type=mem_type):
            if len(vec) != len(q):
                continue
            scored.append((card_id, sum(a * b for a, b in zip(q, vec, strict=False))))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]


def _card_text(card: dict) -> str:
    meta = card.get("metadata") or {}
    title = str(meta.get("title") or "").strip()
    content = str(card.get("content") or "").strip()
    return f"{title} {content}".strip() if title else content


def _hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0:
        return list(vec)
    return [v / norm for v in vec]
