"""工具注册表 + 内置工具测试。"""

from __future__ import annotations

from datetime import datetime

import pytest

from mybuddy.config import Config
from mybuddy.storage import Reminder, init_db, session_scope
from mybuddy.tools import ToolRegistry, set_context, tool
from mybuddy.tools.registry import _build_tool_spec


def test_build_tool_spec_primitives() -> None:
    def fn(city: str, days: int = 1, active: bool = True) -> dict:
        """query weather."""
        return {}

    spec = _build_tool_spec(fn, name="fn", description="d")
    props = spec.parameters["properties"]
    assert props["city"] == {"type": "string"}
    assert props["days"] == {"type": "integer"}
    assert props["active"] == {"type": "boolean"}
    assert spec.parameters["required"] == ["city"]


def test_parse_chinese_reminder_time() -> None:
    from mybuddy.tools.reminder import parse_reminder_time

    now = datetime(2026, 5, 18, 10, 0)
    assert parse_reminder_time("明天下午三点", now=now) == datetime(2026, 5, 19, 15, 0)
    assert parse_reminder_time("后天上午10点半", now=now) == datetime(2026, 5, 20, 10, 30)
    assert parse_reminder_time("今天晚上8点", now=now) == datetime(2026, 5, 18, 20, 0)


def test_tool_decorator_registers_to_isolated_registry() -> None:
    reg = ToolRegistry()

    @tool(name="echo", description="echo back", registry=reg)
    def echo(msg: str) -> str:
        return msg

    assert "echo" in reg.names()
    spec = reg.get("echo").spec
    assert spec.name == "echo"
    assert "msg" in spec.parameters["properties"]


@pytest.mark.asyncio
async def test_registry_execute_unknown_tool_returns_error() -> None:
    reg = ToolRegistry()
    out = await reg.execute("nope", {})
    assert "unknown tool" in out


@pytest.mark.asyncio
async def test_registry_execute_invalid_arguments() -> None:
    reg = ToolRegistry()

    @tool(name="sum2", description="sum", registry=reg)
    def sum2(a: int, b: int) -> int:
        return a + b

    out = await reg.execute("sum2", {"a": 1})
    assert "invalid arguments" in out


@pytest.mark.asyncio
async def test_weather_builtin_returns_mock() -> None:
    # 触发默认注册
    from mybuddy.tools.weather import weather  # noqa: F401

    cfg = Config()
    cfg.tools.weather_mock = True  # 关闭真实网络请求
    set_context(config=cfg)

    reg = ToolRegistry.default()
    out = await reg.execute("weather", {"city": "北京"})
    assert "北京" in out
    assert "mock 模式" in out


@pytest.mark.asyncio
async def test_set_reminder_writes_db(tmp_path) -> None:
    # 触发默认注册
    from mybuddy.tools.reminder import set_reminder  # noqa: F401

    cfg = Config()
    db_file = str(tmp_path / "test.db")
    engine = init_db(db_file)
    set_context(engine=engine, config=cfg)

    reg = ToolRegistry.default()
    out = await reg.execute(
        "set_reminder",
        {"content": "开会", "time": "2030-01-01 09:00"},
    )
    assert '"ok": true' in out

    with session_scope(engine) as s:
        rows = s.query(Reminder).all()
        assert len(rows) == 1
        assert rows[0].content == "开会"
        assert rows[0].status == "pending"
