"""对话式评估追踪器:管理PHQ-9和GAD-7共16个维度的状态。"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from mybuddy._time import utcnow
from mybuddy.storage.db import session_scope
from mybuddy.storage.models import AssessmentCycle, AssessmentDimension

DIMENSION_EXPIRY_DAYS = 14
CYCLE_RESET_DAYS = 14
# 两次维度投放之间至少间隔的对话轮数
MIN_ROUNDS_BETWEEN_ASKS = 3

# 投放节流:user_id -> 距上次投放的轮数(进程内计数,重启后重新计)
_ROUNDS_SINCE_LAST_ASK: dict[int, int] = {}

# PHQ-9 维度:名称和自然提问提示
PHQ9_DIMENSIONS = [
    {"name": "兴趣与愉悦感", "hint": "做什么事还有兴致吗？最近有什么事让你觉得有意思？"},
    {"name": "情绪低落", "hint": "最近心情怎么样？有没有觉得低落或者打不起精神？"},
    {"name": "睡眠问题", "hint": "最近睡得好不好？"},
    {"name": "精力不足", "hint": "最近感觉累不累？做事精力够用吗？"},
    {"name": "食欲问题", "hint": "最近胃口怎么样？"},
    {"name": "自我评价低", "hint": "最近对自己满意吗？"},
    {"name": "注意力问题", "hint": "最近能集中注意力做事吗？"},
    {"name": "精神运动", "hint": "有没有觉得自己动作变慢或别人说你坐不住？"},
    {"name": "自伤意念", "hint": "不会主动询问:仅在用户自然提及时记录"},
]

# GAD-7 维度
GAD7_DIMENSIONS = [
    {"name": "紧张不安", "hint": "最近有没有觉得紧张或者烦躁？"},
    {"name": "无法停止担忧", "hint": "有没有什么事让你一直放不下心？"},
    {"name": "过度担忧", "hint": "你会不会在担心很多不同的事情？"},
    {"name": "难以放松", "hint": "最近能放松下来吗？"},
    {"name": "坐立不安", "hint": "会不会觉得坐着也不踏实、想走来走去？"},
    {"name": "易怒", "hint": "最近有没有觉得特别容易上火？"},
    {"name": "害怕失控", "hint": "有没有突然害怕要发生什么可怕的事？"},
]


class ConversationalAssessmentTracker:
    """管理所有评估维度的状态机。"""

    def __init__(self, engine: Engine, user_id: int):
        self._engine = engine
        self._user_id = user_id

    def ensure_dimensions(self) -> None:
        """确保所有16个维度都有记录(首次使用时初始化);检查周期是否到期自动重置。"""
        with session_scope(self._engine) as s:
            for atype, dims in [("phq9", PHQ9_DIMENSIONS), ("gad7", GAD7_DIMENSIONS)]:
                for i in range(len(dims)):
                    existing = (
                        s.query(AssessmentDimension)
                        .filter(AssessmentDimension.user_id == self._user_id)
                        .filter(AssessmentDimension.assessment_type == atype)
                        .filter(AssessmentDimension.dimension_index == i)
                        .one_or_none()
                    )
                    if existing is None:
                        s.add(AssessmentDimension(
                            user_id=self._user_id,
                            assessment_type=atype,
                            dimension_index=i,
                            status="unasked",
                        ))
        self._maybe_auto_reset_cycle()

    def _maybe_auto_reset_cycle(self) -> None:
        """周期开始超过 CYCLE_RESET_DAYS 时自动重置所有维度,开启新评估周期。"""
        cutoff = utcnow() - timedelta(days=CYCLE_RESET_DAYS)
        with session_scope(self._engine) as s:
            rows = (
                s.query(AssessmentDimension)
                .filter(AssessmentDimension.user_id == self._user_id)
                .all()
            )
            timestamps = [t for r in rows for t in (r.asked_at, r.scored_at) if t]
            if not timestamps:
                return
            if min(timestamps) >= cutoff:
                return
        self.reset_cycle()

    def get_pending_dimensions(self) -> list[dict]:
        """获取所有可以投放的维度(UNASKED或已过期)。"""
        self._reset_expired()
        with session_scope(self._engine) as s:
            rows = (
                s.query(AssessmentDimension)
                .filter(AssessmentDimension.user_id == self._user_id)
                .filter(AssessmentDimension.status == "unasked")
                .all()
            )
            return [_row_to_dim_dict(r) for r in rows]

    def get_asked_dimensions(self) -> list[dict]:
        """获取等待评分的维度(ASKED状态)。"""
        with session_scope(self._engine) as s:
            rows = (
                s.query(AssessmentDimension)
                .filter(AssessmentDimension.user_id == self._user_id)
                .filter(AssessmentDimension.status == "asked")
                .all()
            )
            return [_row_to_dim_dict(r) for r in rows]

    def get_scored_dimensions(self) -> list[dict]:
        """获取已评分的维度。"""
        with session_scope(self._engine) as s:
            rows = (
                s.query(AssessmentDimension)
                .filter(AssessmentDimension.user_id == self._user_id)
                .filter(AssessmentDimension.status == "scored")
                .all()
            )
            return [_row_to_dim_dict(r) for r in rows]

    def get_all_dimensions(self) -> dict[str, list[dict]]:
        """获取所有维度的当前状态(API用)。"""
        self.ensure_dimensions()
        with session_scope(self._engine) as s:
            rows = (
                s.query(AssessmentDimension)
                .filter(AssessmentDimension.user_id == self._user_id)
                .all()
            )
        phq9 = []
        gad7 = []
        for r in rows:
            dims = PHQ9_DIMENSIONS if r.assessment_type == "phq9" else GAD7_DIMENSIONS
            name = dims[r.dimension_index]["name"] if r.dimension_index < len(dims) else f"维度{r.dimension_index}"
            item = {
                "dimension_index": r.dimension_index,
                "name": name,
                "status": r.status,
                "score": r.score,
                "source_conversation": r.source_conversation,
                "scored_at": r.scored_at.isoformat() if r.scored_at else None,
            }
            if r.assessment_type == "phq9":
                phq9.append(item)
            else:
                gad7.append(item)
        phq9.sort(key=lambda x: x["dimension_index"])
        gad7.sort(key=lambda x: x["dimension_index"])

        # 计算总分和等级
        phq9_total = sum(d.get("score") or 0 for d in phq9 if d["status"] == "scored")
        gad7_total = sum(d.get("score") or 0 for d in gad7 if d["status"] == "scored")
        return {
            "phq9": phq9,
            "gad7": gad7,
            "phq9_total": phq9_total if all(d["status"] == "scored" for d in phq9) else None,
            "gad7_total": gad7_total if all(d["status"] == "scored" for d in gad7) else None,
            "phq9_level": _score_level_phq9(phq9_total) if all(d["status"] == "scored" for d in phq9) else None,
            "gad7_level": _score_level_gad7(gad7_total) if all(d["status"] == "scored" for d in gad7) else None,
        }

    def mark_asked(self, assessment_type: str, dimension_index: int) -> None:
        """标记维度已提问。"""
        with session_scope(self._engine) as s:
            row = _get_dimension(s, self._user_id, assessment_type, dimension_index)
            if row:
                row.status = "asked"
                row.asked_at = utcnow()

    def record_score(self, assessment_type: str, dimension_index: int, score: int, source_conv: str | None = None) -> None:
        """记录评分。量表最后一个维度评分完成时,自动归档评估周期。"""
        completed = False
        with session_scope(self._engine) as s:
            row = _get_dimension(s, self._user_id, assessment_type, dimension_index)
            if row is None:
                return
            was_scored = row.status == "scored"
            row.status = "scored"
            row.score = score
            row.scored_at = utcnow()
            if source_conv:
                row.source_conversation = source_conv
            if not was_scored:
                remaining = (
                    s.query(AssessmentDimension)
                    .filter(AssessmentDimension.user_id == self._user_id)
                    .filter(AssessmentDimension.assessment_type == assessment_type)
                    .filter(AssessmentDimension.status != "scored")
                    .count()
                )
                completed = remaining == 0
        if completed:
            self._archive_cycle(assessment_type)

    def _archive_cycle(self, assessment_type: str) -> None:
        """量表全部维度评分完成 → 写入 assessment_cycles 归档。"""
        with session_scope(self._engine) as s:
            rows = (
                s.query(AssessmentDimension)
                .filter(AssessmentDimension.user_id == self._user_id)
                .filter(AssessmentDimension.assessment_type == assessment_type)
                .all()
            )
            total = sum(r.score or 0 for r in rows)
            started = min((r.asked_at for r in rows if r.asked_at), default=None)
            severity = (
                _score_level_phq9(total)
                if assessment_type == "phq9"
                else _score_level_gad7(total)
            )
            s.add(AssessmentCycle(
                user_id=self._user_id,
                assessment_type=assessment_type,
                total_score=total,
                severity=severity,
                started_at=started,
            ))

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """历次评估周期的归档结果(新→旧)。"""
        with session_scope(self._engine) as s:
            rows = (
                s.query(AssessmentCycle)
                .filter(AssessmentCycle.user_id == self._user_id)
                .order_by(AssessmentCycle.completed_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "assessment_type": r.assessment_type,
                    "total_score": r.total_score,
                    "severity": r.severity,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in rows
            ]

    def get_dimension_hint(self, assessment_type: str, dimension_index: int) -> str:
        """获取维度的自然提问提示。"""
        dims = PHQ9_DIMENSIONS if assessment_type == "phq9" else GAD7_DIMENSIONS
        if dimension_index < len(dims):
            return dims[dimension_index]["hint"]
        return ""

    def pick_next_dimension(self) -> dict | None:
        """选取一个待投放的维度(带轮数节流)。

        - 两次投放之间至少间隔 MIN_ROUNDS_BETWEEN_ASKS 轮对话
        - PHQ-9 自伤维度(index 8)不主动投放,仅在用户自然提及时由评分器记录
        """
        rounds = _ROUNDS_SINCE_LAST_ASK.get(self._user_id, MIN_ROUNDS_BETWEEN_ASKS)
        _ROUNDS_SINCE_LAST_ASK[self._user_id] = rounds + 1
        if rounds < MIN_ROUNDS_BETWEEN_ASKS:
            return None

        pending = [
            d for d in self.get_pending_dimensions()
            if not (d["assessment_type"] == "phq9" and d["dimension_index"] == 8)
        ]
        if not pending:
            return None
        chosen = random.choice(pending)
        atype = chosen["assessment_type"]
        idx = chosen["dimension_index"]
        dims = PHQ9_DIMENSIONS if atype == "phq9" else GAD7_DIMENSIONS
        name = dims[idx]["name"] if idx < len(dims) else ""
        hint = dims[idx]["hint"] if idx < len(dims) else ""
        _ROUNDS_SINCE_LAST_ASK[self._user_id] = 0
        return {
            "assessment_type": atype,
            "dimension_index": idx,
            "dimension_name": name,
            "hint": hint,
        }

    def _reset_expired(self) -> None:
        """将过期维度重置为UNASKED。"""
        cutoff = utcnow() - timedelta(days=DIMENSION_EXPIRY_DAYS)
        with session_scope(self._engine) as s:
            rows = (
                s.query(AssessmentDimension)
                .filter(AssessmentDimension.user_id == self._user_id)
                .filter(AssessmentDimension.status.in_(["asked", "answered"]))
                .all()
            )
            for r in rows:
                if r.asked_at and r.asked_at < cutoff:
                    r.status = "unasked"
                    r.asked_at = None

    def reset_cycle(self) -> None:
        """手动重置所有维度(开始新评估周期)。"""
        with session_scope(self._engine) as s:
            rows = (
                s.query(AssessmentDimension)
                .filter(AssessmentDimension.user_id == self._user_id)
                .all()
            )
            for r in rows:
                r.status = "unasked"
                r.score = None
                r.asked_at = None
                r.scored_at = None
                r.source_conversation = None


def _get_dimension(s: Session, user_id: int, assessment_type: str, dimension_index: int) -> AssessmentDimension | None:
    return (
        s.query(AssessmentDimension)
        .filter(AssessmentDimension.user_id == user_id)
        .filter(AssessmentDimension.assessment_type == assessment_type)
        .filter(AssessmentDimension.dimension_index == dimension_index)
        .one_or_none()
    )


def _row_to_dim_dict(r: AssessmentDimension) -> dict[str, Any]:
    return {
        "id": r.id,
        "assessment_type": r.assessment_type,
        "dimension_index": r.dimension_index,
        "status": r.status,
        "score": r.score,
        "asked_at": r.asked_at.isoformat() if r.asked_at else None,
        "scored_at": r.scored_at.isoformat() if r.scored_at else None,
    }


def _score_level_phq9(total: int) -> str:
    if total <= 4:
        return "极轻微"
    elif total <= 9:
        return "轻度"
    elif total <= 14:
        return "中度"
    elif total <= 19:
        return "中重度"
    return "重度"


def _score_level_gad7(total: int) -> str:
    if total <= 4:
        return "极轻微"
    elif total <= 9:
        return "轻度"
    elif total <= 14:
        return "中度"
    return "重度"


class InMemoryAssessmentTracker:
    """访客模式的评估追踪:同接口,仅存内存,进程结束即失。"""

    def __init__(self) -> None:
        self._dims: dict[tuple[str, int], dict[str, Any]] = {}
        self._rounds_since_ask = MIN_ROUNDS_BETWEEN_ASKS
        self.ensure_dimensions()

    def ensure_dimensions(self) -> None:
        for atype, dims in [("phq9", PHQ9_DIMENSIONS), ("gad7", GAD7_DIMENSIONS)]:
            for i in range(len(dims)):
                self._dims.setdefault(
                    (atype, i),
                    {
                        "assessment_type": atype,
                        "dimension_index": i,
                        "status": "unasked",
                        "score": None,
                        "asked_at": None,
                        "scored_at": None,
                    },
                )

    def get_pending_dimensions(self) -> list[dict]:
        return [dict(d) for d in self._dims.values() if d["status"] == "unasked"]

    def get_asked_dimensions(self) -> list[dict]:
        return [dict(d) for d in self._dims.values() if d["status"] == "asked"]

    def mark_asked(self, assessment_type: str, dimension_index: int) -> None:
        dim = self._dims.get((assessment_type, dimension_index))
        if dim:
            dim["status"] = "asked"
            dim["asked_at"] = utcnow()

    def record_score(self, assessment_type: str, dimension_index: int, score: int, source_conv: str | None = None) -> None:
        dim = self._dims.get((assessment_type, dimension_index))
        if dim:
            dim["status"] = "scored"
            dim["score"] = score
            dim["scored_at"] = utcnow()

    def pick_next_dimension(self) -> dict | None:
        rounds = self._rounds_since_ask
        self._rounds_since_ask += 1
        if rounds < MIN_ROUNDS_BETWEEN_ASKS:
            return None
        pending = [
            d for d in self.get_pending_dimensions()
            if not (d["assessment_type"] == "phq9" and d["dimension_index"] == 8)
        ]
        if not pending:
            return None
        chosen = random.choice(pending)
        atype = chosen["assessment_type"]
        idx = chosen["dimension_index"]
        dims = PHQ9_DIMENSIONS if atype == "phq9" else GAD7_DIMENSIONS
        self._rounds_since_ask = 0
        return {
            "assessment_type": atype,
            "dimension_index": idx,
            "dimension_name": dims[idx]["name"],
            "hint": dims[idx]["hint"],
        }

    def reset_cycle(self) -> None:
        self._dims.clear()
        self.ensure_dimensions()


# 访客单例(本地单浏览器场景,一个进程一个访客上下文)
_GUEST_TRACKER: InMemoryAssessmentTracker | None = None


def get_guest_tracker() -> InMemoryAssessmentTracker:
    global _GUEST_TRACKER
    if _GUEST_TRACKER is None:
        _GUEST_TRACKER = InMemoryAssessmentTracker()
    return _GUEST_TRACKER
