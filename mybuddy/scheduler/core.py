"""MyBuddyScheduler:APScheduler + SQLAlchemyJobStore 薄封装。

Job store 持久化到 mybuddy.db 的 `apscheduler_jobs` 表(APScheduler 自建),
CLI 重启后已调度任务自动继续;错过执行时间的任务由 APScheduler 的 misfire
机制处理(默认宽限 1 小时内触发一次)。

提供的调度入口:
  - schedule_daily_greeting(hh_mm):每日早安
  - schedule_dream_job(hh_mm):夜间记忆整理
  - schedule_silence_followup / schedule_cowork_break:一次性关怀检查

所有 Job 函数都走 `mybuddy.scheduler.jobs.*`,必须是顶层函数才能 pickle。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from mybuddy._time import time_offset_minutes
from mybuddy.scheduler.jobs import (
    fire_cowork_break,
    fire_daily_greeting,
    fire_silence_followup,
    run_dream_job,
)

if TYPE_CHECKING:
    from mybuddy.config import Config

logger = logging.getLogger(__name__)


SILENCE_FOLLOWUP_JOB_PREFIX = "silence_followup_"
DAILY_GREETING_JOB_ID = "daily_greeting"
DREAM_JOB_ID = "dream_job"
COWORK_JOB_PREFIX = "vpet_cowork:"


class MyBuddyScheduler:
    """AsyncIOScheduler 封装,生命周期与 CLI 进程绑定。"""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._db_file = config.paths.db_file

        jobstore = SQLAlchemyJobStore(url=f"sqlite:///{self._db_file}")
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": jobstore},
            # 默认时区跟随 tzlocal,让 cron 表达式以本地时间解释(早安/Dream Job)
        )

    # ---- 生命周期 ----

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("scheduler started; jobstore=%s", self._db_file)

    def shutdown(self, *, wait: bool = False) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("scheduler stopped")

    @property
    def running(self) -> bool:
        return self._scheduler.running

    # ---- 主动关怀 ----

    def schedule_silence_followup(
        self,
        *,
        session_id: str,
        user_message_id: int,
        user_text: str,
        run_at: datetime,
    ) -> None:
        """注册一次会话沉默检查,同一 session 只保留最新检查。"""
        settings = self._config.scheduler
        job_id = f"{SILENCE_FOLLOWUP_JOB_PREFIX}{session_id}"
        self._scheduler.add_job(
            fire_silence_followup,
            trigger=DateTrigger(run_date=_scheduler_run_date(run_at)),
            args=[
                self._db_file,
                session_id,
                user_message_id,
                user_text,
                self._config.persona.name,
                settings.silence_followup_min_gap_hours,
                settings.silence_followup_cooldown_hours,
                settings.silence_followup_max_per_day,
                settings.quiet_hours.start,
                settings.quiet_hours.end,
            ],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=900,
        )
        logger.info("scheduled silence followup check for session %s @ %s", session_id, run_at)

    def schedule_cowork_break(self, *, session_id: str, run_at: datetime) -> None:
        """注册持久共处休息提醒;同 session 幂等替换。"""
        self._scheduler.add_job(
            fire_cowork_break,
            trigger=DateTrigger(run_date=_scheduler_run_date(run_at)),
            args=[self._db_file, session_id],
            id=f"{COWORK_JOB_PREFIX}{session_id}",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info("scheduled cowork break for %s @ %s", session_id, run_at)

    def cancel_cowork_break(self, session_id: str) -> bool:
        try:
            self._scheduler.remove_job(f"{COWORK_JOB_PREFIX}{session_id}")
            return True
        except Exception:
            return False

    # ---- 周期任务 ----

    def schedule_daily_greeting(self, hh_mm: str) -> None:
        """每日早安。hh_mm 形如 '09:17',按本地时间解释。"""
        hour, minute = _parse_hh_mm(hh_mm)
        hour, minute = _scheduler_cron_time(hour, minute)
        self._scheduler.add_job(
            fire_daily_greeting,
            trigger=CronTrigger(hour=hour, minute=minute),
            args=[self._db_file, self._config.persona.name],
            id=DAILY_GREETING_JOB_ID,
            replace_existing=True,
        )
        logger.info("scheduled daily greeting @ %02d:%02d", hour, minute)

    def schedule_dream_job(self, hh_mm: str, *, config_path: str = "config.yaml") -> None:
        hour, minute = _parse_hh_mm(hh_mm)
        hour, minute = _scheduler_cron_time(hour, minute)
        self._scheduler.add_job(
            run_dream_job,
            trigger=CronTrigger(hour=hour, minute=minute),
            args=[self._db_file, config_path],
            id=DREAM_JOB_ID,
            replace_existing=True,
        )
        logger.info("scheduled dream job @ %02d:%02d", hour, minute)

    # ---- 调试 ----

    def list_jobs(self) -> list[dict]:
        # 调度器未 start 时 get_jobs() 会返回 pending 作业,这些 Job 尚未计算
        # next_run_time(apscheduler 用 __slots__,未赋值会抛 AttributeError),
        # 故用 getattr 兜底,保证未启动状态下列表/banner 也能安全展示。
        jobs = []
        for j in self._scheduler.get_jobs():
            next_run = getattr(j, "next_run_time", None)
            jobs.append(
                {
                    "id": j.id,
                    "next_run": next_run.isoformat() if next_run else None,
                    "trigger": str(j.trigger),
                }
            )
        return jobs


def _scheduler_run_date(value: datetime) -> datetime:
    """把模拟 UTC 截止时间还原为 APScheduler 使用的真实 UTC 时刻。"""
    simulated_utc = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return simulated_utc - timedelta(minutes=time_offset_minutes())


def _scheduler_cron_time(hour: int, minute: int) -> tuple[int, int]:
    """把模拟本地钟点换成真实本地 cron 钟点。"""
    anchor = datetime(2000, 1, 2, hour, minute) - timedelta(minutes=time_offset_minutes())
    return anchor.hour, anchor.minute


def _parse_hh_mm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)
