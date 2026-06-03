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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.memory.long_term import LongTermMemory

from mybuddy._time import utcnow
from mybuddy.storage import ProfileClaim, ProfileField, session_scope


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
    ) -> int:
        """新增一条命题,返回 SQL 主键 id。同时索引到档案层。"""
        ev_json = json.dumps(evidence_ids or [], ensure_ascii=False)
        with session_scope(self._engine) as s:
            pc = ProfileClaim(
                claim=claim,
                confidence=confidence,
                evidence_ids_json=ev_json,
            )
            s.add(pc)
            s.flush()
            sql_id = pc.id

        # 同步到档案层(使用确定性 id,方便后续更新)
        if self._ltm is not None:
            self._ltm.add(
                claim,
                mem_type="claim",
                uid=self._claim_archive_id(sql_id),
                extra_meta={
                    "sql_id": sql_id,
                    "confidence": confidence,
                    "evidence_ids": ev_json,
                },
            )

        return sql_id

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
            pc.updated_at = utcnow()

            if new_evidence_id:
                ev_ids: list[str] = json.loads(pc.evidence_ids_json or "[]")
                if new_evidence_id not in ev_ids:
                    ev_ids.append(new_evidence_id)
                pc.evidence_ids_json = json.dumps(ev_ids, ensure_ascii=False)

            # 同步更新档案层 metadata
            if self._ltm is not None:
                self._ltm.update_metadata(
                    self._claim_archive_id(claim_id),
                    {
                        "type": "claim",
                        "sql_id": claim_id,
                        "confidence": pc.confidence,
                        "evidence_ids": pc.evidence_ids_json or "[]",
                    },
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
            if confidence is not None:
                pc.confidence = max(0.0, min(1.0, float(confidence)))
            pc.updated_at = utcnow()

            evidence_ids = json.loads(pc.evidence_ids_json or "[]")
            payload = {
                "sql_id": pc.id,
                "claim": pc.claim,
                "confidence": pc.confidence,
                "evidence_ids": evidence_ids,
                "updated_at": pc.updated_at.isoformat() if pc.updated_at else None,
            }

            if self._ltm is not None:
                self._ltm.update(
                    self._claim_archive_id(claim_id),
                    content=pc.claim,
                    metadata={
                        "type": "claim",
                        "sql_id": claim_id,
                        "confidence": pc.confidence,
                        "evidence_ids": pc.evidence_ids_json or "[]",
                    },
                )

            return payload

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
                        "score": h["score"],
                    }
                )
            return result[:top_k]

        # 降级:SQL 全表扫描(按置信度排序)
        with session_scope(self._engine) as s:
            pcs = (
                s.query(ProfileClaim)
                .filter(ProfileClaim.confidence >= min_confidence)
                .order_by(ProfileClaim.confidence.desc())
                .limit(top_k)
                .all()
            )
            return [
                {
                    "sql_id": pc.id,
                    "claim": pc.claim,
                    "confidence": pc.confidence,
                    "evidence_ids": json.loads(pc.evidence_ids_json or "[]"),
                    "score": pc.confidence,  # 降级时用置信度作为相关性分
                }
                for pc in pcs
            ]

    def get_all_claims(
        self, min_confidence: float = 0.0
    ) -> list[dict[str, Any]]:
        """读取所有命题(按置信度降序)。"""
        with session_scope(self._engine) as s:
            q = s.query(ProfileClaim).order_by(ProfileClaim.confidence.desc())
            if min_confidence > 0:
                q = q.filter(ProfileClaim.confidence >= min_confidence)
            return [
                {
                    "sql_id": pc.id,
                    "claim": pc.claim,
                    "confidence": pc.confidence,
                    "evidence_ids": json.loads(pc.evidence_ids_json or "[]"),
                    "updated_at": pc.updated_at.isoformat() if pc.updated_at else None,
                }
                for pc in q.all()
            ]

    def prune_low_confidence(self, threshold: float = 0.3) -> int:
        """删除低于阈值的命题,返回删除数量。"""
        with session_scope(self._engine) as s:
            low = (
                s.query(ProfileClaim)
                .filter(ProfileClaim.confidence < threshold)
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
