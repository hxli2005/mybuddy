"""工具运行时上下文。

工具函数想访问 DB/配置/LLM provider/长期记忆时,从这里取。CLI 的单用户
场景仍可通过 `set_context(...)` 注入默认上下文;服务化/多用户场景使用
`use_context(...)` 在单次请求协程内隔离上下文,避免 QQ/Web/App 并发时串用户。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.config import Config
    from mybuddy.learning import SkillRegistry
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
    skill_registry: SkillRegistry | None = None


_default_ctx = ToolContext()
_ctx_var: ContextVar[ToolContext] = ContextVar("mybuddy_tool_context", default=_default_ctx)


def _current() -> ToolContext:
    return _ctx_var.get()


def _merged(
    base: ToolContext,
    *,
    engine: Engine | None = None,
    config: Config | None = None,
    scheduler: MyBuddyScheduler | None = None,
    provider: BaseLLMProvider | None = None,
    long_term: LongTermMemory | None = None,
    skill_registry: SkillRegistry | None = None,
) -> ToolContext:
    return ToolContext(
        engine=engine if engine is not None else base.engine,
        config=config if config is not None else base.config,
        scheduler=scheduler if scheduler is not None else base.scheduler,
        provider=provider if provider is not None else base.provider,
        long_term=long_term if long_term is not None else base.long_term,
        skill_registry=skill_registry if skill_registry is not None else base.skill_registry,
    )


def set_context(
    *,
    engine: Engine | None = None,
    config: Config | None = None,
    scheduler: MyBuddyScheduler | None = None,
    provider: BaseLLMProvider | None = None,
    long_term: LongTermMemory | None = None,
    skill_registry: SkillRegistry | None = None,
) -> None:
    _ctx_var.set(
        _merged(
            _current(),
            engine=engine,
            config=config,
            scheduler=scheduler,
            provider=provider,
            long_term=long_term,
            skill_registry=skill_registry,
        )
    )


@contextmanager
def use_context(
    *,
    engine: Engine | None = None,
    config: Config | None = None,
    scheduler: MyBuddyScheduler | None = None,
    provider: BaseLLMProvider | None = None,
    long_term: LongTermMemory | None = None,
    skill_registry: SkillRegistry | None = None,
) -> Iterator[None]:
    """在当前执行上下文临时覆盖工具上下文。"""
    token = _ctx_var.set(
        _merged(
            _current(),
            engine=engine,
            config=config,
            scheduler=scheduler,
            provider=provider,
            long_term=long_term,
            skill_registry=skill_registry,
        )
    )
    try:
        yield
    finally:
        _ctx_var.reset(token)


def get_engine() -> Engine:
    ctx = _current()
    if ctx.engine is None:
        raise RuntimeError("tool context not initialized: engine is None")
    return ctx.engine


def get_config() -> Config:
    ctx = _current()
    if ctx.config is None:
        raise RuntimeError("tool context not initialized: config is None")
    return ctx.config


def get_scheduler() -> MyBuddyScheduler | None:
    """可选:不是所有运行环境都有 scheduler(测试/dream CLI 子命令)。"""
    return _current().scheduler


def get_provider() -> BaseLLMProvider:
    ctx = _current()
    if ctx.provider is None:
        raise RuntimeError("tool context not initialized: provider is None")
    return ctx.provider


def get_long_term() -> LongTermMemory:
    ctx = _current()
    if ctx.long_term is None:
        raise RuntimeError("tool context not initialized: long_term is None")
    return ctx.long_term


def get_skill_registry() -> SkillRegistry:
    ctx = _current()
    if ctx.skill_registry is None:
        raise RuntimeError("tool context not initialized: skill_registry is None")
    return ctx.skill_registry


def reset() -> None:
    """测试用。"""
    _ctx_var.set(ToolContext())
