"""set_reminder 工具:解析时间字符串,写入 reminders 表。

支持 ISO / `YYYY-MM-DD HH:MM`,也支持常见中文相对时间:
`明天下午三点`、`后天上午10点半`、`今天晚上8点`。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from dateutil import parser as date_parser

from mybuddy.storage import Reminder, session_scope

from .context import get_engine, get_scheduler
from .registry import tool

DATE_OFFSETS = {
    "今天": 0,
    "明天": 1,
    "后天": 2,
    "大后天": 3,
}

CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


@tool(
    name="set_reminder",
    description=(
        "为用户设置一个提醒。time 支持 ISO、'YYYY-MM-DD HH:MM',"
        "也支持中文相对时间如“明天下午三点”。"
    ),
)
def set_reminder(content: str, time: str) -> dict:
    """为用户设置一个提醒。

    参数:
      content: 提醒的事项文本
      time: 触发时间,ISO 8601 或 'YYYY-MM-DD HH:MM'
    """
    try:
        trigger = parse_reminder_time(time)
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": f"时间解析失败: {e}"}

    if trigger.tzinfo is not None:
        # 简化:统一按无时区的本地时间存(APScheduler M4 才接入,先持久化就好)
        trigger = trigger.replace(tzinfo=None)

    engine = get_engine()
    with session_scope(engine) as s:
        r = Reminder(content=content, trigger_at=trigger, status="pending")
        s.add(r)
        s.flush()
        reminder_id = r.id

    # 如果调度器可用,立即注册到期 job;没有调度器(测试/dream 场景)就只持久化
    scheduler = get_scheduler()
    scheduled = False
    if scheduler is not None and scheduler.running:
        try:
            scheduler.schedule_reminder(reminder_id, trigger)
            scheduled = True
        except Exception as e:  # noqa: BLE001
            return {
                "ok": True,
                "id": reminder_id,
                "content": content,
                "trigger_at": trigger.isoformat(timespec="minutes"),
                "scheduled": False,
                "warn": f"调度失败: {e}",
            }

    return {
        "ok": True,
        "id": reminder_id,
        "content": content,
        "trigger_at": trigger.isoformat(timespec="minutes"),
        "scheduled": scheduled,
    }


def parse_reminder_time(value: str, *, now: datetime | None = None) -> datetime:
    """解析提醒时间。中文相对时间优先,失败后走 dateutil。"""
    text = (value or "").strip()
    if not text:
        raise ValueError("时间为空")

    base = now or _local_now()
    cn = _parse_chinese_relative_time(text, base)
    if cn is not None:
        return cn

    parsed = date_parser.parse(text)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    if parsed <= base and not _has_explicit_date(text):
        parsed = parsed + timedelta(days=1)
    return parsed


def _parse_chinese_relative_time(text: str, base: datetime) -> datetime | None:
    date_offset: int | None = None
    # 大后天必须在后天前匹配
    for word in ("大后天", "后天", "明天", "今天"):
        if word in text:
            date_offset = DATE_OFFSETS[word]
            break

    hm = _extract_hour_minute(text)
    if hm is None:
        return None
    hour, minute = hm

    if any(p in text for p in ("下午", "晚上", "傍晚")) and 1 <= hour < 12:
        hour += 12
    elif "中午" in text and 1 <= hour < 11:
        hour += 12
    elif any(p in text for p in ("凌晨", "早上", "上午")) and hour == 12:
        hour = 0

    target_date = base.date() + timedelta(days=date_offset or 0)
    trigger = datetime.combine(target_date, datetime.min.time()).replace(
        hour=hour,
        minute=minute,
    )
    if date_offset is None and trigger <= base:
        trigger += timedelta(days=1)
    return trigger


def _extract_hour_minute(text: str) -> tuple[int, int] | None:
    colon = re.search(r"([0-9]{1,2})\s*[:：]\s*([0-9]{1,2})", text)
    if colon:
        return int(colon.group(1)), int(colon.group(2))

    match = re.search(r"([零〇一二两三四五六七八九十0-9]{1,3})\s*点\s*(半|[零〇一二两三四五六七八九十0-9]{1,3}分?)?", text)
    if not match:
        return None
    hour = _parse_cn_number(match.group(1))
    tail = match.group(2) or ""
    if tail == "半":
        minute = 30
    elif tail:
        minute = _parse_cn_number(tail.removesuffix("分"))
    else:
        minute = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"时间超出范围: {text}")
    return hour, minute


def _parse_cn_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = CHINESE_DIGITS.get(left, 1) if left else 1
        ones = CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    total = 0
    for ch in value:
        if ch not in CHINESE_DIGITS:
            raise ValueError(f"无法解析数字: {value}")
        total = total * 10 + CHINESE_DIGITS[ch]
    return total


def _has_explicit_date(text: str) -> bool:
    return bool(re.search(r"\d{4}[-/年]\d{1,2}|今天|明天|后天|大后天", text))


def _local_now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)
