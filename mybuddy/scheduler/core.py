"""MyBuddyScheduler:APScheduler + SQLAlchemyJobStore 薄封装。

Job store 持久化到 mybuddy.db 的 `apscheduler_jobs` 表(APScheduler 自建),
CLI 重启后已调度任务自动继续;错过执行时间的任务由 APScheduler 的 misfire
机制处理(默认宽限 1 小时内触发一次)。

提供四类调度入口:
  - schedule_reminder(reminder_id, trigger_at):用户提醒工具调用后注册
  - schedule_daily_greeting(hh_mm):每日早安
  - schedule_dream_job(hh_mm):夜间记忆整理
  - cancel_reminder(reminder_id)

所有 Job 函数都走 `mybuddy.scheduler.jobs.*`,必须是顶层函数才能 pickle。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from mybuddy.scheduler.jobs import (
    fire_daily_greeting,
    fire_reminder,
    run_dream_job,
)

if TYPE_CHECKING:
    from datetime import datetime

    from mybuddy.config import Config

logger = logging.getLogger(__name__)


REMINDER_JOB_PREFIX = "reminder_"
DAILY_GREETING_JOB_ID = "daily_greeting"
DREAM_JOB_ID = "dream_job"


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

    # ---- 提醒 ----

    def schedule_reminder(self, reminder_id: int, trigger_at: datetime) -> None:
        """为一条 reminders 行注册到期 job,幂等(同 id 替换)。"""
        job_id = f"{REMINDER_JOB_PREFIX}{reminder_id}"
        self._scheduler.add_job(
            fire_reminder,
            trigger=DateTrigger(run_date=trigger_at),
            args=[reminder_id, self._db_file],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600,  # 1h 宽限
        )
        logger.info("scheduled reminder %s @ %s", reminder_id, trigger_at)

    def cancel_reminder(self, reminder_id: int) -> bool:
        job_id = f"{REMINDER_JOB_PREFIX}{reminder_id}"
        try:
            self._scheduler.remove_job(job_id)
            return True
        except Exception:
            return False

    # ---- 周期任务 ----

    def schedule_daily_greeting(self, hh_mm: str) -> None:
        """每日早安。hh_mm 形如 '09:17',按本地时间解释。"""
        hour, minute = _parse_hh_mm(hh_mm)
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
        return [
            {
                "id": j.id,
                "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
                "trigger": str(j.trigger),
            }
            for j in self._scheduler.get_jobs()
        ]


def _parse_hh_mm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)
