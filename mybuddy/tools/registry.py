"""工具注册表:decorator 注册 + 自动 JSON Schema 生成。

借鉴 OpenManus 的 @tool 装饰器风格。工具函数用 python 类型注解声明参数,
装饰器内省签名生成 JSON Schema,暴露给 LLM。执行时把 LLM 返回的 arguments
字典当关键字参数调进原函数。

用法:

    @tool(name="weather", description="查询某地天气")
    def weather(city: str, days: int = 1) -> dict:
        ...

    ToolRegistry.default().execute("weather", {"city": "北京"})
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, get_args, get_origin, get_type_hints

from mybuddy.llm import ToolSpec

ToolFn = Callable[..., Any] | Callable[..., Awaitable[Any]]


@dataclass
class ToolEntry:
    name: str
    description: str
    fn: ToolFn
    spec: ToolSpec
    is_async: bool


class ToolRegistry:
    """工具注册表。默认提供全局单例(`ToolRegistry.default()`),
    也可以显式构造隔离实例用于测试。"""

    _default: ToolRegistry | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    @classmethod
    def default(cls) -> ToolRegistry:
        if cls._default is None:
            cls._default = cls()
        return cls._default

    def register(self, fn: ToolFn, *, name: str, description: str) -> ToolEntry:
        spec = _build_tool_spec(fn, name=name, description=description)
        entry = ToolEntry(
            name=name,
            description=description,
            fn=fn,
            spec=spec,
            is_async=inspect.iscoroutinefunction(fn),
        )
        self._tools[name] = entry
        return entry

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def specs(self) -> list[ToolSpec]:
        return [e.spec for e in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """执行工具调用,总是返回字符串(LLM 侧统一吃文本 tool_result)。"""
        entry = self._tools.get(name)
        if entry is None:
            return _error(f"unknown tool: {name}")
        try:
            result = entry.fn(**arguments) if not entry.is_async else await entry.fn(**arguments)
            if inspect.isawaitable(result):
                result = await result
        except TypeError as e:
            return _error(f"invalid arguments for {name}: {e}")
        except Exception as e:  # noqa: BLE001 — 工具里出错也要反馈给 LLM
            return _error(f"{type(e).__name__}: {e}")
        return _stringify(result)


def tool(
    *,
    name: str | None = None,
    description: str | None = None,
    registry: ToolRegistry | None = None,
) -> Callable[[ToolFn], ToolFn]:
    """把一个普通函数注册为工具。

    - name 缺省用函数名
    - description 缺省用函数 docstring 首行
    - registry 缺省用全局单例
    """

    def decorator(fn: ToolFn) -> ToolFn:
        reg = registry or ToolRegistry.default()
        fn_name = name or fn.__name__
        fn_desc = description or (inspect.getdoc(fn) or "").split("\n", 1)[0].strip()
        if not fn_desc:
            fn_desc = fn_name
        reg.register(fn, name=fn_name, description=fn_desc)
        return fn

    return decorator


# ---- JSON Schema 生成 ----

_PRIMITIVE_SCHEMA: dict[type, dict[str, Any]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    dict: {"type": "object"},
    list: {"type": "array"},
}


def _type_to_schema(tp: Any) -> dict[str, Any]:
    if tp in _PRIMITIVE_SCHEMA:
        return dict(_PRIMITIVE_SCHEMA[tp])
    origin = get_origin(tp)
    if origin in (list, tuple, set, frozenset):
        args = get_args(tp)
        item_schema = _type_to_schema(args[0]) if args else {}
        return {"type": "array", "items": item_schema}
    if origin is dict:
        return {"type": "object"}
    # Optional / Union[X, None]: 取非 None 的那一支
    if origin is type(None):
        return {"type": "null"}
    args = get_args(tp)
    if args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _type_to_schema(non_none[0])
    return {}


def _build_tool_spec(fn: ToolFn, *, name: str, description: str) -> ToolSpec:
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:  # noqa: BLE001
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        tp = hints.get(param_name, str)
        schema = _type_to_schema(tp)
        properties[param_name] = schema
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required

    return ToolSpec(name=name, description=description, parameters=parameters)


# ---- helpers ----


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _error(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)
