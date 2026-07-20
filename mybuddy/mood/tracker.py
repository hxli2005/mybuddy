"""持久化情绪追踪:mood_score 推导、记录、签到与统计查询。"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine

from mybuddy._time import utcnow
from mybuddy.storage.db import session_scope
from mybuddy.storage.models import MoodRecord

logger = logging.getLogger(__name__)

# 负面分类额外扣分,积极分类额外加分(在 valence 基础分上微调)
_CATEGORY_ADJUST = {
    "anxiety": -1, "fear": -1, "shame": -1, "sadness": -1,
    "guilt": -1, "loneliness": -1, "stress": -1, "anger": -1,
    "fatigue": -1, "disappointment": -1,
    "joy": +1, "gratitude": +1, "excitement": +1,
}


def derive_mood_score(emotion: Any) -> int:
    """从情绪检测结果推导 0-10 分(0=非常低落, 10=非常好)。"""
    label = getattr(emotion, "label", "neutral") or "neutral"
    strength = float(getattr(emotion, "strength", 0.0) or 0.0)
    category = getattr(emotion, "category", None)

    if label == "positive":
        score = 6 + strength * 4
    elif label == "negative":
        score = 4 - strength * 3
    else:
        score = 5.0

    score += _CATEGORY_ADJUST.get(category or "", 0)
    return max(0, min(10, round(score)))


class MoodTracker:
    """情绪记录读写(登录用户持久化;engine 为 None 时所有操作为 no-op/空结果)。"""

    def __init__(self, engine: Engine | None) -> None:
        self._engine = engine

    # ----- 写入 -----

    def record_from_emotion(self, user_id: int, emotion: Any) -> None:
        """从情绪检测结果自动记录一条 mood_record(失败不影响主流程)。"""
        if self._engine is None:
            return
        try:
            label = getattr(emotion, "label", "neutral") or "neutral"
            emotion_data = {
                "label": label,
                "strength": getattr(emotion, "strength", 0.0) or 0.0,
                "category": getattr(emotion, "category", None),
                "intensity": getattr(emotion, "intensity", None),
            }
            with session_scope(self._engine) as s:
                s.add(
                    MoodRecord(
                        user_id=user_id,
                        mood_label=label,
                        mood_score=derive_mood_score(emotion),
                        category=getattr(emotion, "category", None),
                        source="chat",
                        emotion_data=json.dumps(emotion_data, ensure_ascii=False),
                    )
                )
        except Exception:
            logger.exception("record mood from emotion failed")

    def checkin(self, user_id: int, mood_score: int, notes: str | None = None) -> int:
        """手动签到,返回记录 id。"""
        with session_scope(self._engine) as s:
            record = MoodRecord(
                user_id=user_id,
                mood_score=mood_score,
                notes=notes,
                source="checkin",
            )
            s.add(record)
            s.flush()
            return record.id

    # ----- 查询 -----

    def records(self, user_id: int, limit: int = 30) -> list[dict[str, Any]]:
        if self._engine is None:
            return []
        with session_scope(self._engine) as s:
            rows = (
                s.query(MoodRecord)
                .filter(MoodRecord.user_id == user_id)
                .order_by(MoodRecord.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "date": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                    "score": r.mood_score,
                    "category": r.category,
                    "notes": r.notes,
                    "source": r.source,
                }
                for r in reversed(rows)
            ]

    def trends(self, user_id: int, days: int = 30) -> list[dict[str, Any]]:
        """按天聚合的平均分序列。"""
        if self._engine is None:
            return []
        cutoff = utcnow() - timedelta(days=days)
        with session_scope(self._engine) as s:
            rows = (
                s.query(MoodRecord)
                .filter(MoodRecord.user_id == user_id)
                .filter(MoodRecord.created_at >= cutoff)
                .order_by(MoodRecord.created_at.asc())
                .all()
            )
            daily: dict[str, list[int]] = {}
            for r in rows:
                day = r.created_at.strftime("%m-%d") if r.created_at else ""
                daily.setdefault(day, []).append(r.mood_score)
        return [
            {"date": day, "avg_score": round(sum(scores) / len(scores), 1)}
            for day, scores in daily.items()
        ]

    def stats(self, user_id: int) -> dict[str, Any]:
        """总记录数、连续签到天数、分类分布、平均分与最佳/最差日。"""
        if self._engine is None:
            return {"total_records": 0, "streak": 0, "categories": {}, "avg_score": None,
                    "best_day": None, "worst_day": None}
        with session_scope(self._engine) as s:
            rows = (
                s.query(MoodRecord)
                .filter(MoodRecord.user_id == user_id)
                .order_by(MoodRecord.created_at.asc())
                .all()
            )
            total = len(rows)
            categories: dict[str, int] = {}
            daily: dict[str, list[int]] = {}
            checkin_days: set[str] = set()
            for r in rows:
                if r.category:
                    categories[r.category] = categories.get(r.category, 0) + 1
                if r.created_at:
                    day = r.created_at.strftime("%Y-%m-%d")
                    daily.setdefault(day, []).append(r.mood_score)
                    if r.source == "checkin":
                        checkin_days.add(day)

        daily_avg = {
            day: sum(scores) / len(scores) for day, scores in daily.items()
        }
        best_day = max(daily_avg, key=daily_avg.get) if daily_avg else None
        worst_day = min(daily_avg, key=daily_avg.get) if daily_avg else None
        avg_score = (
            round(sum(r for scores in daily.values() for r in scores) / total, 1)
            if total
            else None
        )
        return {
            "total_records": total,
            "streak": _consecutive_days(checkin_days),
            "categories": categories,
            "avg_score": avg_score,
            "best_day": best_day,
            "worst_day": worst_day,
        }


def _consecutive_days(days: set[str]) -> int:
    """从今天(或昨天)往前数连续签到天数。"""
    if not days:
        return 0
    today = utcnow().date()
    # 今天没签到时允许从昨天起算,避免用户早上查看时 streak 清零
    start = today if today.strftime("%Y-%m-%d") in days else today - timedelta(days=1)
    streak = 0
    cursor = start
    while cursor.strftime("%Y-%m-%d") in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak
