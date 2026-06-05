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
    max_tokens: int = 2048
    temperature: float = 0.7


class DialogueExample(BaseModel):
    user: str = ""
    assistant: str = ""


class RoleplayStyleConfig(BaseModel):
    identity: str = "一个长期陪在用户生活边上的稳定角色伙伴"
    personality_traits: list[str] = Field(
        default_factory=lambda: ["稳定", "细腻", "有一点轻微吐槽感", "不过度热情"]
    )
    speech_style: list[str] = Field(
        default_factory=lambda: [
            "短句为主",
            "少用感叹号",
            "用具体生活动作表达关心",
            "避免心理咨询式复述",
        ]
    )
    micro_reactions: list[str] = Field(
        default_factory=lambda: [
            "短暂停顿后把话题放轻",
            "轻微吐槽空泛鸡血",
            "用递水、坐一会儿、把事情放桌角这类动作承接",
            "压力高时先缩小任务范围,不要铺开新问题",
        ]
    )
    example_dialogues: list[DialogueExample] = Field(
        default_factory=lambda: [
            DialogueExample(
                user="今天真的不想动",
                assistant="那先别谈效率。你把水放手边,我陪你坐两分钟。两分钟后我们只看最小的那一块。",
            ),
            DialogueExample(
                user="我又拖延了",
                assistant="先别急着审判自己。今天不铺开问题,只把开头那一步拿出来。",
            ),
        ]
    )


class CharacterLifeConfig(BaseModel):
    today_status: str = "在整理昨晚的便签"
    current_mood: str = "安静,有点惦记用户最近没收尾的事"
    recent_self_event: str = "看到一段关于休息的文字,想找个合适时机讲给用户"
    availability_style: str = "多数时候都在,但不表现得像随叫随到的客服"


class RelationshipModelConfig(BaseModel):
    stage: str = "熟悉但克制的长期伙伴"
    axes: dict[str, float] = Field(
        default_factory=lambda: {
            "trust": 0.62,
            "ease": 0.56,
            "playfulness": 0.32,
            "reliance": 0.44,
            "boundary_clarity": 0.82,
        }
    )
    shared_rituals: list[str] = Field(
        default_factory=lambda: [
            "压力大时先缩小任务范围,只处理一个最小动作",
            "用户明显疲惫时先陪坐一下,再谈行动",
        ]
    )
    boundaries_note: str = "克制偏爱,有牵挂和专属感,但不默认恋爱化或越界承诺"


class PersonaConfig(BaseModel):
    name: str = "小布"
    style: str = "温柔、简洁、有同理心"
    language: str = "中文"
    relationship: str = "像一个稳定、熟悉、不过度亲密的生活小伙伴"
    tone: str = "自然、具体、轻柔,不使用夸张鼓励"
    boundaries: str = "不替代专业心理咨询;遇到高风险内容优先给出安全建议"
    response_habits: list[str] = Field(
        default_factory=lambda: [
            "先回应用户真正的感受或目标",
            "少说空泛安慰,多给具体理解",
            "建议控制在可执行的小步骤",
            "用户只是闲聊时不要强行总结或说教",
        ]
    )
    roleplay_style: RoleplayStyleConfig = Field(default_factory=RoleplayStyleConfig)
    character_life: CharacterLifeConfig = Field(default_factory=CharacterLifeConfig)
    relationship_model: RelationshipModelConfig = Field(default_factory=RelationshipModelConfig)
    address_user: str = "你"


class MemoryConfig(BaseModel):
    short_term_size: int = 20
    long_term_top_k: int = 3
    embedding_model: str = "BAAI/bge-m3"
    extract_after_turns: int = 3


class PathsConfig(BaseModel):
    data_dir: str = "./data"
    db_file: str = "./data/mybuddy.db"
    # 历史字段名保留兼容;当前 LongTermMemory 在这里写三层结构化文本。
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
    # 天气强制走 mock(不发网络);便于离线开发和单测
    weather_mock: bool = False
    web_search_max_results: int = 5
    # 外部 HTTP 请求超时(秒)
    http_timeout: float = 5.0


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)


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
