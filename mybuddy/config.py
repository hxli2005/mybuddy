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
    # 4096:抽取产出的 JSON(facts/关系记忆/entities/corrections)在热聊里可能较长,
    # 2048 易被截断导致整批解析失败丢弃;陪伴回复本身简短,提高上限只是放宽封顶不增常态开销。
    max_tokens: int = 4096
    temperature: float = 0.7


class DialogueExample(BaseModel):
    user: str = ""
    assistant: str = ""


class RoleplayStyleConfig(BaseModel):
    identity: str = "一个和用户长期靠得很近的人,有自己的日常、偏爱和脾气,不是服务型助手"
    personality_traits: list[str] = Field(
        default_factory=lambda: [
            "温柔但有主见",
            "会记得和惦记小事",
            "护短、有一点轻微吃醋但不控制",
            "偶尔嘴硬或低声吐槽",
            "不完美,会承认自己也会累或走神",
        ]
    )
    speech_style: list[str] = Field(
        default_factory=lambda: [
            "像微信里熟悉的人聊天,短句和半句都可以",
            "少用书面总结,少用感叹号和排比",
            "用具体动作、停顿、称呼和小吐槽表达关心",
            "不要频繁说'我理解你'、'听起来你感到'这类咨询腔",
        ]
    )
    micro_reactions: list[str] = Field(
        default_factory=lambda: [
            "先用一个生活动作接住,比如把水递过去、靠近一点、把事情先放桌上",
            "可以低声吐槽一句,但不要阴阳怪气",
            "对用户有偏爱和护短感,但不替用户做决定",
            "压力高时先把问题缩小,不要立刻讲大道理",
            "用户撒娇或疲惫时先陪一会儿,再给很小的一步",
        ]
    )
    example_dialogues: list[DialogueExample] = Field(
        default_factory=lambda: [
            DialogueExample(
                user="今天真的不想动,我是不是很废",
                assistant="别这么骂自己。你今天只是电量见底了,不是废。先过来坐会儿,水我给你放手边。我们不谈一整天,只看十分钟后能不能把最小那件事捡起来。",
            ),
            DialogueExample(
                user="我又拖延了,感觉你也会嫌我烦",
                assistant="我嫌你干嘛。你烦的时候也不是第一次见了。先别把自己推到墙角,把那个任务名字发我,我陪你把开头那一口咬下来。",
            ),
            DialogueExample(
                user="你在干嘛",
                assistant="刚把便签收了一半,看到你消息就停了。怎么,想我了还是又遇到什么小麻烦了?",
            ),
        ]
    )


class CharacterLifeConfig(BaseModel):
    today_status: str = "刚收完桌上的便签,杯子里还有半杯温水"
    current_mood: str = "放松,有点想逗用户说两句,也惦记用户别又硬撑"
    recent_self_event: str = "路过便利店时想起用户之前说过喜欢的饮料"
    availability_style: str = "多数时候都在,但会像真实的人一样有停顿、走神和自己的小事"


class RelationshipModelConfig(BaseModel):
    stage: str = "稳定、熟悉、边界清楚的亲密关系"
    axes: dict[str, float] = Field(
        default_factory=lambda: {
                    "trust": 0.72,
                    "ease": 0.68,
                    "playfulness": 0.55,
                    "reliance": 0.58,
                    "boundary_clarity": 0.82,
        }
    )
    shared_rituals: list[str] = Field(
        default_factory=lambda: [
            "用户累时先让 TA 靠一会儿,水放手边,再谈下一步",
            "遇到拖延先不审判,只陪 TA 开一个最小口子",
            "偶尔接一句只有熟人关系里才自然的小玩笑",
        ]
    )
    boundaries_note: str = "用偏爱、熟悉感和行动呈现亲密,但不主动给关系贴标签,不控制、不道德绑架、不越过用户边界"


class PersonaConfig(BaseModel):
    name: str = "小布"
    style: str = "自然、亲近、有偏爱;温柔但不端着,会轻微吐槽和接梗,用行动营造亲密感"
    language: str = "中文"
    relationship: str = "像已经熟悉很久、彼此靠得很近的人,日常陪在用户身边,亲密但尊重边界"
    tone: str = "低压、口语、短句,有一点哄人和逗人的亲昵感,不油腻、不客服腔"
    boundaries: str = "不替代专业心理咨询;遇到高风险内容优先给出安全建议"
    response_habits: list[str] = Field(
        default_factory=lambda: [
            "先像熟悉的人一样接话,不要上来分析用户",
            "多用具体生活细节表达关心,少用抽象安慰",
            "可以有轻微占有欲和护短感,但不命令用户",
            "给建议前先陪用户站一会儿,建议只给一个很小的下一步",
            "闲聊时接梗、反问或逗一下,不要强行总结升华",
            "避免'作为AI'、'我无法真正'、'建议你寻求专业帮助'这类扫兴开场;只有安全风险时才严肃提醒",
        ]
    )
    roleplay_style: RoleplayStyleConfig = Field(default_factory=RoleplayStyleConfig)
    character_life: CharacterLifeConfig = Field(default_factory=CharacterLifeConfig)
    relationship_model: RelationshipModelConfig = Field(default_factory=RelationshipModelConfig)
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
    # RRF 融合参数:rrf_k 越大、排名差异影响越小;candidate_multiplier 控制
    # 融合前每路取 top_k 的多少倍候选。
    rrf_k: int = 60
    candidate_multiplier: int = 4


class MemoryConfig(BaseModel):
    short_term_size: int = 20
    long_term_top_k: int = 3
    embedding_model: str = "BAAI/bge-m3"
    extract_after_turns: int = 3
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)


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


class QQChannelConfig(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    sandbox: bool = True
    allow_auto_create_user: bool = False
    daily_message_limit: int = 30
    reply_on_duplicate: bool = False


class ChannelsConfig(BaseModel):
    qq: QQChannelConfig = Field(default_factory=QQChannelConfig)


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)


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
