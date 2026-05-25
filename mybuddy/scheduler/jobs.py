"""调度器顶层 job 函数。

SQLAlchemyJobStore 用 pickle 序列化 job,因此这些函数必须是模块顶层(不能是 lambda
或类方法),且参数只能是基本类型(str / int / dict)。job 在触发时自己重建 engine /
config / provider,避免持有跨进程不可序列化的对象。

三类 job:
  - fire_reminder(reminder_id, db_file):到期提醒 → 写 pending_messages + 更新 Reminder.status
  - fire_daily_greeting(db_file, persona_name):每日早安 → 写 pending_messages
  - run_dream_job(db_file, config_path):夜间 Dream Job(五件事)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from mybuddy._time import utcnow
from mybuddy.storage import Reminder, enqueue, init_db, session_scope

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def fire_reminder(reminder_id: int, db_file: str) -> None:
    """到期提醒触发:写主动消息队列,更新 reminders.status=fired。"""
    engine = init_db(db_file)
    with session_scope(engine) as s:
        r = s.get(Reminder, reminder_id)
        if r is None:
            logger.warning("reminder %s 不存在,跳过", reminder_id)
            return
        if r.status != "pending":
            logger.info("reminder %s 状态为 %s,跳过", reminder_id, r.status)
            return
        content = r.content
        r.status = "fired"
        r.fired_at = utcnow()

    enqueue(
        engine,
        source="reminder",
        content=f"⏰ 提醒:{content}",
        meta={"reminder_id": reminder_id},
    )
    logger.info("reminder %s 已入队播放", reminder_id)


def fire_daily_greeting(db_file: str, persona_name: str = "小布") -> None:
    """每日早安:写一条 pending_messages。MVP 里是固定话术,
    后续可改成 LLM 动态生成(生成逻辑放在 dream.py 更合适)。
    """
    engine = init_db(db_file)
    content = f"早上好呀 ☀️ {persona_name}在这里等你,今天打算做点什么?"
    enqueue(engine, source="greeting", content=content)
    logger.info("daily greeting enqueued")


def run_dream_job(db_file: str, config_path: str) -> None:
    """夜间 Dream Job 入口。委托给 learning.dream.DreamJob 执行五件事。

    由于 APScheduler 在自己的线程池跑 job,这里用 asyncio.run 起新 event loop。
    """
    # 延迟 import 避免循环依赖
    from mybuddy.config import load_config
    from mybuddy.learning.dream import DreamJob
    from mybuddy.llm import make_provider
    from mybuddy.memory import LongTermMemory, UserProfile

    cfg = load_config(config_path)
    engine = init_db(db_file)
    provider = make_provider(cfg.llm)
    ltm = LongTermMemory(
        persist_dir=cfg.paths.chroma_dir,
        embedding_model=cfg.memory.embedding_model,
    )
    profile = UserProfile(engine, ltm)
    job = DreamJob(
        engine=engine,
        config=cfg,
        provider=provider,
        ltm=ltm,
        profile=profile,
    )
    asyncio.run(job.run())
    logger.info("dream job finished")
