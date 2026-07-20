"""配置加载:从 YAML 文件读取,支持环境变量引用。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    provider: Literal["anthropic", "openai", "openrouter", "deepseek"] = "anthropic"
    model: str = "claude-sonnet-4-5"
    small_model: str | None = None
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.7


class PersonaConfig(BaseModel):
    """AI 助手的基本身份配置。"""
    name: str = "小布"
    language: str = "中文"
    tone: str = "低压、口语、短句,温和但不冷漠,有温度但不越界"
    boundaries: str = (
        "你是心理健康陪伴者,不是治疗师。你可以提供心理教育和情绪支持。"
        "你必须拒绝诊断、开药和替代治疗。遇到危机信号时优先安全,适当提醒专业资源可用。"
    )
    address_user: str = "你"


class EmbeddingConfig(BaseModel):
    """可选的语义召回(API embedding + 旁路向量索引)。

    默认关闭:关闭时整条链路零开销、零额外依赖,检索仍是纯词法、纯离线。
    开启需配置一个 OpenAI 兼容的 embeddings 端点(可与主对话 LLM 不同)。
    """

    enabled: bool = False
    model: str = "text-embedding-3-small"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    timeout: float = 10.0
    batch_size: int = 64
    rrf_k: int = 60
    candidate_multiplier: int = 4


class TranscriptionConfig(BaseModel):
    """本地语音转文字(openai-whisper),完全离线运行。"""

    enabled: bool = False
    model: Literal["tiny", "base", "small", "medium", "large-v3"] = "base"
    language: str = "zh"


class MemoryConfig(BaseModel):
    short_term_size: int = 20
    long_term_top_k: int = 3
    embedding_model: str = "BAAI/bge-m3"
    extract_after_turns: int = 3
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)


class PathsConfig(BaseModel):
    data_dir: str = "./data"
    db_file: str = "./data/mybuddy.db"
    chroma_dir: str = "./data/memory"
    skills_dir: str = "./data/skills"
    trajectories_dir: str = "./data/trajectories"


class QuietHours(BaseModel):
    start: str = "23:00"
    end: str = "08:00"


class SchedulerConfig(BaseModel):
    enabled: bool = True
    daily_greeting: str = "09:17"
    dream_job: str = "02:23"
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    silence_followup_enabled: bool = True
    silence_followup_delay_minutes: int = 45
    silence_followup_min_gap_hours: int = 6
    silence_followup_cooldown_hours: int = 48
    silence_followup_max_per_day: int = 1


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "./data/mybuddy.log"


class ToolsConfig(BaseModel):
    weather_mock: bool = False
    web_search_max_results: int = 5
    http_timeout: float = 5.0


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)


_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env_vars(value: object) -> object:
    """递归展开字符串中的 ${VAR} 引用。"""
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def load_config(path: str | Path = "config.yaml") -> Config:
    """从 YAML 文件加载配置。文件不存在时返回默认配置(用于测试)。"""
    p = Path(path)
    if not p.exists():
        return Config()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    expanded = _expand_env_vars(raw)
    return Config.model_validate(expanded)


def ensure_dirs(cfg: Config) -> None:
    """确保所有运行时目录存在。"""
    for path_str in [
        cfg.paths.data_dir,
        cfg.paths.chroma_dir,
        cfg.paths.skills_dir,
        cfg.paths.trajectories_dir,
    ]:
        Path(path_str).mkdir(parents=True, exist_ok=True)
