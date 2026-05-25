"""工具运行时上下文。

工具函数想访问 DB/配置/LLM provider/长期记忆时,从这里取。CLI 启动时
`set_context(engine=..., config=..., provider=..., long_term=..., scheduler=...)`
一次,工具内部 `get_engine() / get_provider() / ...` 取用。

这种显式的"进程级上下文"比给每个工具传参要干净,且便于测试时注入 mock。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.config import Config
    from mybuddy.llm import BaseLLMProvider
    from mybuddy.memory import LongTermMemory
    from mybuddy.scheduler import MyBuddyScheduler


@dataclass
class ToolContext:
    engine: Engine | None = None
    config: Config | None = None
    scheduler: MyBuddyScheduler | None = None
    provider: BaseLLMProvider | None = None
    long_term: LongTermMemory | None = None


_ctx = ToolContext()


def set_context(
    *,
    engine: Engine | None = None,
    config: Config | None = None,
    scheduler: MyBuddyScheduler | None = None,
    provider: BaseLLMProvider | None = None,
    long_term: LongTermMemory | None = None,
) -> None:
    if engine is not None:
        _ctx.engine = engine
    if config is not None:
        _ctx.config = config
    if scheduler is not None:
        _ctx.scheduler = scheduler
    if provider is not None:
        _ctx.provider = provider
    if long_term is not None:
        _ctx.long_term = long_term


def get_engine() -> Engine:
    if _ctx.engine is None:
        raise RuntimeError("tool context not initialized: engine is None")
    return _ctx.engine


def get_config() -> Config:
    if _ctx.config is None:
        raise RuntimeError("tool context not initialized: config is None")
    return _ctx.config


def get_scheduler() -> MyBuddyScheduler | None:
    """可选:不是所有运行环境都有 scheduler(测试/dream CLI 子命令)。"""
    return _ctx.scheduler


def get_provider() -> BaseLLMProvider:
    if _ctx.provider is None:
        raise RuntimeError("tool context not initialized: provider is None")
    return _ctx.provider


def get_long_term() -> LongTermMemory:
    if _ctx.long_term is None:
        raise RuntimeError("tool context not initialized: long_term is None")
    return _ctx.long_term


def reset() -> None:
    """测试用。"""
    _ctx.engine = None
    _ctx.config = None
    _ctx.scheduler = None
    _ctx.provider = None
    _ctx.long_term = None
