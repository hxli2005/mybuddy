"""MyBuddy mini 的唯一运行配置：一次模型连接。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    provider: Literal["openrouter", "deepseek"] = "openrouter"
    model: str = "deepseek/deepseek-v3.2"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.7


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)


_ENV = re.compile(r"\$\{([^}]+)\}")


def _expand(value: object) -> object:
    if isinstance(value, str):
        return _ENV.sub(lambda match: os.environ.get(match.group(1), ""), value)
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    return value


def load_config(path: str | Path = "config.yaml") -> Config:
    source = Path(path)
    if not source.exists():
        return Config()
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return Config.model_validate(_expand(raw))
