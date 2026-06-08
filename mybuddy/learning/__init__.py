"""自学习子系统(借鉴 Hermes Agent)。

M2 落地 trajectory 采集;M4 新增 dream job;M5 加入 feedback 总线;M6 加入 skills + curator。
"""

from .dream import DreamJob, DreamReport
from .feedback import (
    FeedbackBus,
    FeedbackEvent,
    detect_implicit_negative,
    make_skill_subscriber,
    make_trajectory_subscriber,
)
from .skill_curator import SkillCurator
from .skills import Skill, SkillRegistry
from .trajectory import Trajectory, TrajectoryLogger, TrajectoryStep

__all__ = [
    "DreamJob",
    "DreamReport",
    "FeedbackBus",
    "FeedbackEvent",
    "Skill",
    "SkillCurator",
    "SkillRegistry",
    "Trajectory",
    "TrajectoryLogger",
    "TrajectoryStep",
    "detect_implicit_negative",
    "make_skill_subscriber",
    "make_trajectory_subscriber",
]
