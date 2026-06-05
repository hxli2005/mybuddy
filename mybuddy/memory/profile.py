"""用户画像:核心字段 + 动态命题集。

借鉴 Hermes Agent / Honcho 的辩证式用户建模:
  - 核心字段(hard facts):姓名、生日、偏好、禁忌等,KV 形式存 SQLite。
  - 动态命题(soft claims):带置信度和证据链,新证据持续增强/削弱旧命题。
    命题同时写入 LongTermMemory 档案层,支持文本检索。

用法:
    profile = UserProfile(engine, long_term_memory)
    profile.set_field("名字", "小明")
    profile.add_claim("用户周日晚上情绪较低", confidence=0.7, evidence_ids=["msg_1"])
    hits = profile.search_claims("周末心情", top_k=3)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.memory.long_term import LongTermMemory

from mybuddy._time import utcnow
from mybuddy.memory.governance import make_memory_key
from mybuddy.storage import ProfileClaim, ProfileField, session_scope

VISIBLE_CLAIM_STATUSES = {"candidate", "active", "stable"}
HIDDEN_CLAIM_STATUSES = {"promoted", "stale", "refuted", "archived"}


class UserProfile:
    """混合型用户画像:核心字段 + 动态命题集。

    核心字段读写走 SQLite,命题集增删改也走 SQLite,
    但命题文本同时索引到 LongTermMemory 档案层以支持检索。
    """

    def __init__(self, engine: Engine, ltm: LongTermMemory | None = None) -> None:
        self._engine = engine
        self._ltm = ltm

    # ------------------------------------------------------------------
    # 核心字段(hard facts)
    # ------------------------------------------------------------------

    def set_field(self, key: str, value: str) -> None:
        """写入或更新一个核心字段。"""
        with session_scope(self._engine) as s:
            field = s.query(ProfileField).filter_by(key=key).one_or_none()
            if field is None:
                field = ProfileField(key=key, value=value)
                s.add(field)
            else:
                field.value = value
                field.updated_at = utcnow()

    def get_field(self, key: str) -> str | None:
        """读取单个字段值。"""
        with session_scope(self._engine) as s:
            field = s.query(ProfileField).filter_by(key=key).one_or_none()
            return field.value if field else None

    def get_all_fields(self) -> dict[str, str]:
        """返回所有核心字段的 KV 字典。"""
        with session_scope(self._engine) as s:
            return {f.key: f.value for f in s.query(ProfileField).all()}

    def delete_field(self, key: str) -> bool:
        """删除字段,返回是否删除成功。"""
        with session_scope(self._engine) as s:
            field = s.query(ProfileField).filter_by(key=key).one_or_none()
            if field is None:
                return False
            s.delete(field)
            return True

    # ------------------------------------------------------------------
    # 动态命题(soft claims)
    # ------------------------------------------------------------------

    def add_claim(
        self,
        claim: str,
        confidence: float = 0.5,
        evidence_ids: list[str] | None = None,
        *,
        category: str | None = None,
        status: str | None = None,
    ) -> int:
        """新增一条命题,返回 SQL 主键 id。同时索引到档案层。"""
        clean_claim = (claim or "").strip()
        if not clean_claim:
            raise ValueError("claim is empty")
        memory_key = make_memory_key("claim", clean_claim)
        claim_category = _clean_category(category) or _infer_claim_category(clean_claim)
        claim_status = _clean_status(status) or ("active" if confidence >= 0.5 else "candidate")
        existing_id = self._find_existing_claim_id(memory_key, clean_claim)
        if existing_id is not None:
            return self._merge_claim(
                existing_id,
                clean_claim,
                confidence=confidence,
                evidence_ids=evidence_ids or [],
                memory_key=memory_key,
                category=claim_category,
                status=claim_status,
            )

        now = utcnow()
        ev_ids = _merge_unique([], evidence_ids or [])
        ev_json = json.dumps(ev_ids, ensure_ascii=False)
        ev_days = _evidence_days_for(ev_ids, now)
        ev_days_json = json.dumps(ev_days, ensure_ascii=False)
        with session_scope(self._engine) as s:
            pc = ProfileClaim(
                claim=clean_claim,
                confidence=confidence,
                evidence_ids_json=ev_json,
                status=claim_status,
                category=claim_category,
                evidence_count=len(ev_ids),
                evidence_days_json=ev_days_json,
                first_seen_at=now,
                last_seen_at=now,
            )
            s.add(pc)
            s.flush()
            sql_id = pc.id

        # 同步到档案层(使用确定性 id,方便后续更新)
        if self._ltm is not None:
            self._ltm.add(
                clean_claim,
                mem_type="claim",
                uid=self._claim_archive_id(sql_id),
                extra_meta={
                    "sql_id": sql_id,
                    "confidence": confidence,
                    "evidence_ids": ev_json,
                    "memory_key": memory_key,
                    "source": "profile_claim",
                    "status": claim_status,
                    "category": claim_category,
                    "evidence_count": len(ev_ids),
                    "evidence_days": ev_days,
                    "first_seen_at": now.isoformat(timespec="seconds"),
                    "last_seen_at": now.isoformat(timespec="seconds"),
                },
            )

        return sql_id

    def _find_existing_claim_id(self, memory_key: str, claim: str) -> int | None:
        if self._ltm is not None:
            for item in self._ltm.list_all(mem_type="claim"):
                meta = item.get("metadata") or {}
                if meta.get("status", "active") != "active":
                    continue
                if meta.get("memory_key") == memory_key and isinstance(meta.get("sql_id"), int):
                    return meta["sql_id"]
            hits = [
                hit for hit in self._ltm.search(claim, top_k=3, mem_type="claim")
                if hit.get("score", 0) >= 0.9
            ]
            if hits:
                sql_id = (hits[0].get("metadata") or {}).get("sql_id")
                if isinstance(sql_id, int):
                    return sql_id

        with session_scope(self._engine) as s:
            for row in s.query(ProfileClaim).all():
                if make_memory_key("claim", row.claim) == memory_key:
                    return row.id
        return None

    def _merge_claim(
        self,
        claim_id: int,
        claim: str,
        *,
        confidence: float,
        evidence_ids: list[str],
        memory_key: str,
        category: str,
        status: str,
    ) -> int:
        now = utcnow()
        with session_scope(self._engine) as s:
            pc = s.query(ProfileClaim).filter_by(id=claim_id).one_or_none()
            if pc is None:
                return claim_id
            if len(claim) > len(pc.claim) + 8:
                pc.claim = claim
            pc.confidence = max(pc.confidence, min(1.0, float(confidence)))
            pc.category = pc.category or category
            if pc.status not in HIDDEN_CLAIM_STATUSES:
                pc.status = _stronger_status(pc.status, status)
            pc.updated_at = now
            pc.last_seen_at = now
            if pc.first_seen_at is None:
                pc.first_seen_at = now
            ev_ids = _merge_unique(_json_list(pc.evidence_ids_json), evidence_ids)
            pc.evidence_ids_json = json.dumps(ev_ids, ensure_ascii=False)
            ev_days = _merge_unique(_json_list(pc.evidence_days_json), _evidence_days_for(evidence_ids, now))
            pc.evidence_days_json = json.dumps(ev_days, ensure_ascii=False)
            pc.evidence_count = max(len(ev_ids), pc.evidence_count or 0)
            content = pc.claim
            conf = pc.confidence
            ev_json = pc.evidence_ids_json
            meta = _claim_meta(pc, memory_key=memory_key)

        if self._ltm is not None:
            self._ltm.update(
                self._claim_archive_id(claim_id),
                content=content,
                metadata={**meta, "confidence": conf, "evidence_ids": ev_json},
            )
        return claim_id

    def update_confidence(
        self,
        claim_id: int,
        delta: float,
        new_evidence_id: str | None = None,
    ) -> bool:
        """更新命题置信度(加 delta),可选追加证据。返回是否成功。"""
        with session_scope(self._engine) as s:
            pc = s.query(ProfileClaim).filter_by(id=claim_id).one_or_none()
            if pc is None:
                return False

            pc.confidence = max(0.0, min(1.0, pc.confidence + delta))
            now = utcnow()
            pc.updated_at = now

            if new_evidence_id:
                ev_ids = _merge_unique(_json_list(pc.evidence_ids_json), [new_evidence_id])
                pc.evidence_ids_json = json.dumps(ev_ids, ensure_ascii=False)
                ev_days = _merge_unique(_json_list(pc.evidence_days_json), [now.date().isoformat()])
                pc.evidence_days_json = json.dumps(ev_days, ensure_ascii=False)
                pc.evidence_count = max(len(ev_ids), pc.evidence_count or 0)
                pc.last_seen_at = now

            # 同步更新档案层 metadata
            if self._ltm is not None:
                self._ltm.update_metadata(
                    self._claim_archive_id(claim_id),
                    _claim_meta(pc),
                )

            return True

    def update_claim(
        self,
        claim_id: int,
        *,
        claim: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any] | None:
        """更新一条动态命题的正文或置信度,返回更新后的命题。"""
        clean_claim = claim.strip() if claim is not None else None
        if claim is not None and not clean_claim:
            return None

        with session_scope(self._engine) as s:
            pc = s.query(ProfileClaim).filter_by(id=claim_id).one_or_none()
            if pc is None:
                return None

            if clean_claim is not None:
                pc.claim = clean_claim
                pc.category = _infer_claim_category(clean_claim)
            if confidence is not None:
                pc.confidence = max(0.0, min(1.0, float(confidence)))
            pc.updated_at = utcnow()

            payload = _claim_payload(pc)

            if self._ltm is not None:
                self._ltm.update(
                    self._claim_archive_id(claim_id),
                    content=pc.claim,
                    metadata=_claim_meta(pc),
                )

            return payload

    def mark_claim_conflicts(self, claim_ids: list[int], conflict_ids: list[int]) -> None:
        """记录命题冲突关系,供晋升校验跳过 contested 命题。"""
        clean_conflicts = [int(i) for i in conflict_ids if isinstance(i, int)]
        with session_scope(self._engine) as s:
            rows = s.query(ProfileClaim).filter(ProfileClaim.id.in_(claim_ids)).all()
            for pc in rows:
                existing = _json_int_list(pc.conflict_ids_json)
                pc.conflict_ids_json = json.dumps(
                    sorted(set(existing) | set(clean_conflicts) - {pc.id}),
                    ensure_ascii=False,
                )
                pc.updated_at = utcnow()
                if self._ltm is not None:
                    self._ltm.update_metadata(self._claim_archive_id(pc.id), _claim_meta(pc))

    def mark_claim_promotion_checked(self, claim_id: int) -> None:
        """记录一次晋升检查。"""
        with session_scope(self._engine) as s:
            pc = s.query(ProfileClaim).filter_by(id=claim_id).one_or_none()
            if pc is None:
                return
            pc.promotion_checked_at = utcnow()
            if self._ltm is not None:
                self._ltm.update_metadata(self._claim_archive_id(claim_id), _claim_meta(pc))

    def mark_claim_promoted(self, claim_id: int, promoted_memory_id: str) -> bool:
        """将动态命题标记为已晋升,前端和召回默认隐藏。"""
        with session_scope(self._engine) as s:
            pc = s.query(ProfileClaim).filter_by(id=claim_id).one_or_none()
            if pc is None:
                return False
            now = utcnow()
            pc.status = "promoted"
            pc.promoted_memory_id = promoted_memory_id
            pc.promotion_checked_at = now
            pc.updated_at = now
            payload = _claim_payload(pc)
            if self._ltm is not None:
                self._ltm.update(
                    self._claim_archive_id(claim_id),
                    content=pc.claim,
                    metadata={**_claim_meta(pc), "status": "promoted"},
                )
            return bool(payload)

    def delete_claim(self, claim_id: int) -> bool:
        """删除动态命题,并同步删除档案层索引。"""
        with session_scope(self._engine) as s:
            pc = s.query(ProfileClaim).filter_by(id=claim_id).one_or_none()
            if pc is None:
                return False
            if self._ltm is not None:
                self._ltm.delete(self._claim_archive_id(claim_id))
            s.delete(pc)
            return True

    def search_claims(
        self,
        query: str,
        top_k: int = 5,
        min_confidence: float = 0.5,
    ) -> list[dict[str, Any]]:
        """语义搜索相关命题,返回 [{sql_id, claim, confidence, evidence_ids, score}]。

        先走档案层文本检索,再按置信度过滤。若无档案层则走 SQL 全表扫描。
        """
        if self._ltm is not None:
            hits = self._ltm.search(query, top_k=top_k * 2, mem_type="claim")
            result: list[dict[str, Any]] = []
            for h in hits:
                meta = h.get("metadata", {})
                conf = meta.get("confidence", 0.0)
                if meta.get("status", "active") not in VISIBLE_CLAIM_STATUSES:
                    continue
                if conf < min_confidence:
                    continue
                ev_raw = meta.get("evidence_ids", "[]")
                try:
                    ev_ids = json.loads(ev_raw) if isinstance(ev_raw, str) else ev_raw
                except (json.JSONDecodeError, TypeError):
                    ev_ids = []
                result.append(
                    {
                        "sql_id": meta.get("sql_id"),
                        "claim": h["content"],
                        "confidence": conf,
                        "evidence_ids": ev_ids,
                        "status": meta.get("status", "active"),
                        "category": meta.get("category", "general"),
                        "evidence_count": meta.get("evidence_count", len(ev_ids)),
                        "first_seen_at": meta.get("first_seen_at"),
                        "last_seen_at": meta.get("last_seen_at"),
                        "score": h["score"],
                    }
                )
            return result[:top_k]

        # 降级:SQL 全表扫描(按置信度排序)
        with session_scope(self._engine) as s:
            pcs = (
                s.query(ProfileClaim)
                .filter(ProfileClaim.confidence >= min_confidence)
                .filter(ProfileClaim.status.in_(VISIBLE_CLAIM_STATUSES))
                .order_by(ProfileClaim.confidence.desc())
                .limit(top_k)
                .all()
            )
            return [
                {
                    "sql_id": pc.id,
                    "claim": pc.claim,
                    "confidence": pc.confidence,
                    "evidence_ids": _json_list(pc.evidence_ids_json),
                    "status": pc.status,
                    "category": pc.category,
                    "evidence_count": pc.evidence_count,
                    "first_seen_at": _iso(pc.first_seen_at),
                    "last_seen_at": _iso(pc.last_seen_at),
                    "score": pc.confidence,  # 降级时用置信度作为相关性分
                }
                for pc in pcs
            ]

    def get_all_claims(
        self,
        min_confidence: float = 0.0,
        *,
        include_hidden: bool = True,
    ) -> list[dict[str, Any]]:
        """读取所有命题(按置信度降序)。"""
        with session_scope(self._engine) as s:
            q = s.query(ProfileClaim).order_by(ProfileClaim.confidence.desc())
            if min_confidence > 0:
                q = q.filter(ProfileClaim.confidence >= min_confidence)
            if not include_hidden:
                q = q.filter(ProfileClaim.status.in_(VISIBLE_CLAIM_STATUSES))
            return [
                _claim_payload(pc)
                for pc in q.all()
            ]

    def prune_low_confidence(self, threshold: float = 0.3) -> int:
        """删除低于阈值的命题,返回删除数量。"""
        with session_scope(self._engine) as s:
            low = (
                s.query(ProfileClaim)
                .filter(ProfileClaim.confidence < threshold)
                .filter(ProfileClaim.status.in_(VISIBLE_CLAIM_STATUSES))
                .all()
            )
            count = len(low)
            for pc in low:
                if self._ltm is not None:
                    archive_id = self._claim_archive_id(pc.id)
                    self._ltm.delete(archive_id)
                s.delete(pc)
            return count

    def _claim_archive_id(self, sql_id: int) -> str:
        return f"claim_{sql_id}"


def _claim_payload(pc: ProfileClaim) -> dict[str, Any]:
    return {
        "sql_id": pc.id,
        "claim": pc.claim,
        "confidence": pc.confidence,
        "evidence_ids": _json_list(pc.evidence_ids_json),
        "status": pc.status or "active",
        "category": pc.category or "general",
        "evidence_count": pc.evidence_count or 0,
        "evidence_days": _json_list(pc.evidence_days_json),
        "conflict_ids": _json_int_list(pc.conflict_ids_json),
        "first_seen_at": _iso(pc.first_seen_at),
        "last_seen_at": _iso(pc.last_seen_at),
        "promoted_memory_id": pc.promoted_memory_id,
        "promotion_checked_at": _iso(pc.promotion_checked_at),
        "updated_at": _iso(pc.updated_at),
    }


def _claim_meta(pc: ProfileClaim, *, memory_key: str | None = None) -> dict[str, Any]:
    meta = _claim_payload(pc)
    return {
        **{k: v for k, v in meta.items() if v not in (None, "", [])},
        "type": "claim",
        "sql_id": pc.id,
        "confidence": pc.confidence,
        "evidence_ids": json.dumps(meta["evidence_ids"], ensure_ascii=False),
        "memory_key": memory_key or make_memory_key("claim", pc.claim),
        "source": "profile_claim",
    }


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _json_int_list(value: str | None) -> list[int]:
    out: list[int] = []
    for item in _json_list(value):
        try:
            out.append(int(item))
        except ValueError:
            continue
    return out


def _merge_unique(old: list[str], new: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in [*old, *new]:
        clean = str(item).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _evidence_days_for(evidence_ids: list[str], now: datetime) -> list[str]:
    return [now.date().isoformat()] if evidence_ids else []


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _clean_status(value: str | None) -> str | None:
    if not value:
        return None
    clean = value.strip()
    allowed = VISIBLE_CLAIM_STATUSES | HIDDEN_CLAIM_STATUSES
    return clean if clean in allowed else None


def _clean_category(value: str | None) -> str | None:
    if not value:
        return None
    clean = value.strip()
    allowed = {"general", "fact", "preference", "relationship", "emotion_pattern", "task", "boundary"}
    return clean if clean in allowed else None


def _stronger_status(old: str | None, new: str) -> str:
    order = {"candidate": 0, "active": 1, "stable": 2}
    old_clean = old if old in order else "candidate"
    return new if order.get(new, 0) > order.get(old_clean, 0) else old_clean


def _infer_claim_category(claim: str) -> str:
    text = claim or ""
    if any(k in text for k in ("明天", "今天", "截止", "报告", "任务", "要做", "计划", "ddl", "DDL")):
        return "task"
    if any(k in text for k in ("不喜欢", "讨厌", "反感", "不要", "别", "边界", "越界")):
        return "boundary"
    if any(k in text for k in ("喜欢", "偏好", "更接受", "习惯", "倾向", "适合")):
        return "preference"
    if any(k in text for k in ("情绪", "焦虑", "低落", "压力", "崩溃", "累", "拖延")):
        return "emotion_pattern"
    if any(k in text for k in ("关系", "默契", "陪伴", "信任")):
        return "relationship"
    if any(k in text for k in ("叫", "生日", "住在", "工作", "过敏", "不吃")):
        return "fact"
    return "general"
