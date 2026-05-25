"""Skills 库:自生长的程序性记忆。

借鉴 Hermes Agent `~/.hermes/skills/` 的 Markdown + YAML frontmatter 设计。
每个 skill 对应 `data/skills/<slug>.md`,前置 frontmatter 存元数据,正文存步骤。

用法:
    registry = SkillRegistry.load_all("data/skills")
    hits = registry.match(user_input="我好难过", emotion_label="negative", consecutive_negative=True)
    # ...注入 system prompt...
    registry.record_success("情绪安抚流程")  # /good 反馈时
    registry.record_failure("情绪安抚流程") # /bad 反馈时
    registry.create(name="新技能", triggers=[...], steps=[...], confidence=0.3)

设计取舍:
  - 高频字段(counts / confidence)也写文件,单 CLI 进程足够,多进程场景再改 SQL。
  - 匹配先用子串/关键词,低置信度(<0.5)不注入 system prompt,归档后不参与匹配。
  - confidence 用 Laplace 平滑 `success / (success + fail + 1)`,避免 0/0。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mybuddy._time import utcnow

logger = logging.getLogger(__name__)


CONFIDENCE_INJECT_FLOOR = 0.5     # 低于此阈值不注入 system prompt
CONFIDENCE_ARCHIVE_FLOOR = 0.2    # 低于此阈值且样本足够 → archive
ARCHIVE_MIN_SAMPLES = 3           # 少于此样本数不归档(信息不足)
DEFAULT_TOP_N = 3


@dataclass
class Skill:
    """一个 skill = frontmatter + 正文。"""

    name: str
    triggers: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    success_count: int = 0
    fail_count: int = 0
    confidence: float = 0.3
    archived: bool = False
    created_at: str = ""
    updated_at: str = ""
    file_path: str = ""

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_markdown(self) -> str:
        """序列化为 frontmatter + Markdown 正文。"""
        fm = {
            "name": self.name,
            "triggers": list(self.triggers),
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "confidence": round(self.confidence, 3),
            "archived": self.archived,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        yaml_text = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).rstrip()
        body_lines = ["步骤:"] + [f"{i + 1}. {s}" for i, s in enumerate(self.steps)]
        body = "\n".join(body_lines)
        return f"---\n{yaml_text}\n---\n{body}\n"

    @classmethod
    def from_markdown(cls, text: str, *, file_path: str = "") -> Skill:
        fm, body = _split_frontmatter(text)
        steps = _parse_steps(body)
        return cls(
            name=fm.get("name") or Path(file_path).stem,
            triggers=list(fm.get("triggers") or []),
            steps=steps,
            success_count=int(fm.get("success_count", 0) or 0),
            fail_count=int(fm.get("fail_count", 0) or 0),
            confidence=float(fm.get("confidence", 0.3) or 0.3),
            archived=bool(fm.get("archived", False)),
            created_at=str(fm.get("created_at", "") or ""),
            updated_at=str(fm.get("updated_at", "") or ""),
            file_path=file_path,
        )

    # ------------------------------------------------------------------
    # 计数更新
    # ------------------------------------------------------------------

    def record_success(self) -> None:
        self.success_count += 1
        self._recompute()

    def record_failure(self) -> None:
        self.fail_count += 1
        self._recompute()

    def _recompute(self) -> None:
        total = self.success_count + self.fail_count
        # Laplace 平滑:+1 先验失败,避免 1-0 直接算 100%
        self.confidence = self.success_count / (total + 1)
        self.updated_at = utcnow().isoformat()
        if (
            not self.archived
            and total >= ARCHIVE_MIN_SAMPLES
            and self.confidence < CONFIDENCE_ARCHIVE_FLOOR
        ):
            self.archived = True

    # ------------------------------------------------------------------
    # 匹配
    # ------------------------------------------------------------------

    def matches(
        self,
        user_input: str,
        *,
        emotion_label: str | None = None,
        consecutive_negative: bool = False,
    ) -> bool:
        """triggers 任一命中即候选。"""
        if self.archived:
            return False
        haystack = user_input or ""
        flags = []
        if emotion_label:
            flags.append(f"情绪={emotion_label}")
            flags.append(emotion_label)
        if consecutive_negative:
            flags.append("持续>2轮")
            flags.append("连续负面")
        for trig in self.triggers:
            t = (trig or "").strip()
            if not t:
                continue
            if t in haystack:
                return True
            for f in flags:
                if t in f or f in t:
                    return True
        return False


class SkillRegistry:
    """skill 的加载、匹配、计数更新。按 file_path 为主键持有。"""

    def __init__(self, skills_dir: str | Path) -> None:
        self._dir = Path(skills_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, Skill] = {}  # key = name

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------

    @classmethod
    def load_all(cls, skills_dir: str | Path) -> SkillRegistry:
        reg = cls(skills_dir)
        reg.reload()
        return reg

    def reload(self) -> None:
        self._skills.clear()
        for md in sorted(self._dir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8")
                skill = Skill.from_markdown(text, file_path=str(md))
                self._skills[skill.name] = skill
            except Exception:  # noqa: BLE001
                logger.exception("skills: 加载失败 %s", md)

    def save(self, skill: Skill) -> None:
        """写回文件。file_path 为空则按 name 生成。"""
        if not skill.file_path:
            skill.file_path = str(self._dir / f"{_slugify(skill.name)}.md")
        if not skill.created_at:
            skill.created_at = utcnow().isoformat()
        if not skill.updated_at:
            skill.updated_at = skill.created_at
        Path(skill.file_path).write_text(skill.to_markdown(), encoding="utf-8")

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def all(self, *, include_archived: bool = False) -> list[Skill]:
        return [
            s for s in self._skills.values() if include_archived or not s.archived
        ]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def match(
        self,
        user_input: str,
        *,
        emotion_label: str | None = None,
        consecutive_negative: bool = False,
        top_n: int = DEFAULT_TOP_N,
        min_confidence: float = CONFIDENCE_INJECT_FLOOR,
    ) -> list[Skill]:
        """返回命中 triggers 且置信度 ≥ min_confidence 的 top-N(按 confidence 降序)。"""
        hits = [
            s
            for s in self._skills.values()
            if s.matches(
                user_input,
                emotion_label=emotion_label,
                consecutive_negative=consecutive_negative,
            )
            and s.confidence >= min_confidence
        ]
        hits.sort(key=lambda s: s.confidence, reverse=True)
        return hits[:top_n]

    # ------------------------------------------------------------------
    # 计数更新(被 FeedbackBus 订阅者调用)
    # ------------------------------------------------------------------

    def record_success(self, name: str) -> bool:
        skill = self._skills.get(name)
        if skill is None:
            return False
        skill.record_success()
        self.save(skill)
        return True

    def record_failure(self, name: str) -> bool:
        skill = self._skills.get(name)
        if skill is None:
            return False
        skill.record_failure()
        self.save(skill)
        return True

    # ------------------------------------------------------------------
    # 创建(手工 or curator)
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        triggers: list[str],
        steps: list[str],
        confidence: float = 0.3,
    ) -> Skill:
        """新建一条 skill 并写盘。若同名已存在则合并(保留已有计数,覆盖 steps/triggers)。"""
        now = utcnow().isoformat()
        existing = self._skills.get(name)
        if existing is not None:
            existing.triggers = triggers or existing.triggers
            existing.steps = steps or existing.steps
            existing.updated_at = now
            self.save(existing)
            return existing
        skill = Skill(
            name=name,
            triggers=list(triggers),
            steps=list(steps),
            confidence=confidence,
            created_at=now,
            updated_at=now,
        )
        self.save(skill)
        self._skills[skill.name] = skill
        return skill


# ---------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """返回 (frontmatter_dict, body)。没有 frontmatter 时返回 ({}, 原文)。"""
    m = _FRONTMATTER_RE.match(text.lstrip())
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, m.group(2)


_STEP_LINE_RE = re.compile(r"^\s*(?:\d+[.、)]|[-*])\s*(.+?)\s*$")


def _parse_steps(body: str) -> list[str]:
    """从正文提取形如 `1. xxx` / `- xxx` 的步骤行。忽略"步骤:"这类标题行。"""
    steps: list[str] = []
    for line in body.splitlines():
        m = _STEP_LINE_RE.match(line)
        if m:
            steps.append(m.group(1).strip())
    return steps


_SLUG_RE = re.compile(r"[^\w一-鿿-]+")


def _slugify(name: str) -> str:
    """文件名安全化:保留中英文和数字,其余替换成连字符。"""
    s = _SLUG_RE.sub("-", name).strip("-")
    return s or "skill"
