"""M6 Skills 子系统测试。

覆盖:
  - Skill frontmatter 读写往返
  - 步骤解析(数字列表/破折号列表)
  - 匹配:子串命中 / 情绪标签命中 / 连续负面命中 / archived 被排除 / 置信度门槛
  - 计数更新 + 归档阈值
  - Registry.create 幂等
  - FeedbackBus 订阅者(success / failure / 未命中 skill 忽略)
"""

from __future__ import annotations

import pytest

from mybuddy.learning import FeedbackEvent, SkillCurator, make_skill_subscriber
from mybuddy.learning.skills import (
    ARCHIVE_MIN_SAMPLES,
    CONFIDENCE_ARCHIVE_FLOOR,
    Skill,
    SkillRegistry,
)
from mybuddy.learning.trajectory import Trajectory, TrajectoryStep

# =============================================================================
# Skill 序列化
# =============================================================================


def test_skill_roundtrip_frontmatter(tmp_path) -> None:
    skill = Skill(
        name="情绪安抚流程",
        triggers=["情绪=消极", "持续>2轮"],
        steps=["先共情不给方案", "询问具体发生了什么", "回忆类似情景"],
        success_count=3,
        fail_count=1,
        confidence=0.6,
        archived=False,
        created_at="2026-05-09T14:22:00",
        updated_at="2026-05-09T15:10:00",
    )
    md = skill.to_markdown()

    # frontmatter + 步骤都在
    assert md.startswith("---\n")
    assert "情绪安抚流程" in md
    assert "1. 先共情不给方案" in md

    parsed = Skill.from_markdown(md, file_path=str(tmp_path / "x.md"))
    assert parsed.name == "情绪安抚流程"
    assert parsed.triggers == ["情绪=消极", "持续>2轮"]
    assert parsed.steps == ["先共情不给方案", "询问具体发生了什么", "回忆类似情景"]
    assert parsed.success_count == 3
    assert parsed.fail_count == 1
    assert abs(parsed.confidence - 0.6) < 1e-6


def test_skill_parse_dash_steps() -> None:
    md = """---
name: test
triggers: []
---
步骤:
- 第一步
- 第二步
"""
    s = Skill.from_markdown(md)
    assert s.steps == ["第一步", "第二步"]


def test_skill_parse_missing_frontmatter() -> None:
    """没有 frontmatter 时从 body 里解析步骤,name 回落到文件名。"""
    s = Skill.from_markdown("步骤:\n1. A\n2. B\n", file_path="/x/hello.md")
    assert s.name == "hello"
    assert s.steps == ["A", "B"]


# =============================================================================
# 匹配
# =============================================================================


def _mk_skill(**overrides) -> Skill:
    base = dict(name="s1", triggers=["早上好"], steps=["打招呼"], confidence=0.7)
    base.update(overrides)
    return Skill(**base)


def test_match_substring_trigger() -> None:
    s = _mk_skill()
    assert s.matches("早上好啊")
    assert not s.matches("晚安")


def test_match_emotion_trigger() -> None:
    s = _mk_skill(triggers=["情绪=消极"])
    assert s.matches("随便一句", emotion_label="消极")
    assert not s.matches("随便一句", emotion_label="positive")


def test_match_consecutive_negative() -> None:
    s = _mk_skill(triggers=["持续>2轮"])
    assert s.matches("都快死了", consecutive_negative=True)
    assert not s.matches("都快死了", consecutive_negative=False)


def test_archived_skill_never_matches() -> None:
    s = _mk_skill(archived=True)
    assert not s.matches("早上好啊")


# =============================================================================
# 计数 + 归档
# =============================================================================


def test_record_success_raises_confidence() -> None:
    s = _mk_skill(success_count=0, fail_count=0, confidence=0.3)
    for _ in range(5):
        s.record_success()
    # 5 成功 0 失败 Laplace → 5/(5+0+1) = 0.833
    assert s.confidence > 0.7
    assert not s.archived


def test_record_failure_archives_when_below_floor() -> None:
    s = _mk_skill(success_count=0, fail_count=0, confidence=0.5)
    for _ in range(ARCHIVE_MIN_SAMPLES):
        s.record_failure()
    assert s.confidence < CONFIDENCE_ARCHIVE_FLOOR
    assert s.archived


def test_record_failure_not_archived_if_samples_too_few() -> None:
    s = _mk_skill(confidence=0.5)
    s.record_failure()
    # 样本只有 1,不归档
    assert not s.archived


# =============================================================================
# SkillRegistry
# =============================================================================


def test_registry_load_and_match(tmp_path) -> None:
    # 准备两个 skill 文件
    (tmp_path / "a.md").write_text(
        """---
name: 早安问候
triggers: ["早上好", "早安"]
success_count: 3
fail_count: 0
confidence: 0.75
archived: false
---
步骤:
1. 温柔问好
2. 顺带天气
""",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text(
        """---
name: 归档的技能
triggers: ["早上好"]
confidence: 0.1
archived: true
---
步骤:
1. 不会被用到
""",
        encoding="utf-8",
    )

    reg = SkillRegistry.load_all(tmp_path)
    assert len(reg.all()) == 1  # 归档的不出现
    assert len(reg.all(include_archived=True)) == 2

    hits = reg.match("早上好啊")
    assert len(hits) == 1
    assert hits[0].name == "早安问候"


def test_registry_min_confidence_filter(tmp_path) -> None:
    (tmp_path / "low.md").write_text(
        """---
name: 低置信度
triggers: ["早上好"]
confidence: 0.3
---
""",
        encoding="utf-8",
    )
    reg = SkillRegistry.load_all(tmp_path)
    # 默认 min_confidence=0.5,低置信度不出现
    assert reg.match("早上好") == []
    assert len(reg.match("早上好", min_confidence=0.2)) == 1


def test_registry_record_success_writes_back(tmp_path) -> None:
    (tmp_path / "a.md").write_text(
        """---
name: 早安问候
triggers: ["早上好"]
success_count: 2
fail_count: 0
confidence: 0.6
---
步骤:
1. 问好
""",
        encoding="utf-8",
    )
    reg = SkillRegistry.load_all(tmp_path)
    assert reg.record_success("早安问候") is True

    # 重新加载应看到 +1
    reg2 = SkillRegistry.load_all(tmp_path)
    s = reg2.get("早安问候")
    assert s is not None
    assert s.success_count == 3


def test_registry_record_unknown_skill(tmp_path) -> None:
    reg = SkillRegistry(tmp_path / "does-not-matter-xxxxxx")
    assert reg.record_success("不存在") is False
    assert reg.record_failure("不存在") is False


def test_registry_create_and_idempotent(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    s1 = reg.create(
        name="新技能", triggers=["trigger1"], steps=["步骤A"], confidence=0.3
    )
    assert s1.file_path.endswith(".md")
    assert (tmp_path / "新技能.md").exists() or (tmp_path / s1.file_path.split("/")[-1]).exists()

    # 再次 create 同名 → 保留 counts,覆盖 triggers/steps
    reg.record_success("新技能")  # 让它有一次成功
    s2 = reg.create(
        name="新技能",
        triggers=["trigger2"],
        steps=["步骤B"],
        confidence=0.3,
    )
    assert s2.success_count == 1  # counts 保留
    assert s2.triggers == ["trigger2"]
    assert s2.steps == ["步骤B"]


# =============================================================================
# FeedbackBus skill subscriber
# =============================================================================


def test_feedback_subscriber_records_success(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    reg.create(name="技能1", triggers=["x"], steps=["y"], confidence=0.3)

    sub = make_skill_subscriber(reg)
    sub(
        FeedbackEvent(
            turn_id="t1",
            label="good",
            meta={"triggered_skills": ["技能1"]},
        )
    )

    s = reg.get("技能1")
    assert s is not None
    assert s.success_count == 1


def test_feedback_subscriber_records_failure(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    reg.create(name="技能1", triggers=["x"], steps=["y"], confidence=0.3)

    sub = make_skill_subscriber(reg)
    for label in ("bad", "fix:改成别的", "implicit:negative"):
        sub(
            FeedbackEvent(
                turn_id="t1",
                label=label,
                meta={"triggered_skills": ["技能1"]},
            )
        )
    s = reg.get("技能1")
    assert s is not None
    assert s.fail_count == 3


def test_feedback_subscriber_no_triggered_skills_noop(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    reg.create(name="技能1", triggers=["x"], steps=["y"], confidence=0.3)

    sub = make_skill_subscriber(reg)
    sub(FeedbackEvent(turn_id="t1", label="good"))  # meta 里没有 triggered_skills

    s = reg.get("技能1")
    assert s is not None
    assert s.success_count == 0


def test_feedback_subscriber_unknown_skill_is_silent(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    sub = make_skill_subscriber(reg)
    # 不应抛异常
    sub(
        FeedbackEvent(
            turn_id="t1",
            label="good",
            meta={"triggered_skills": ["不存在"]},
        )
    )


# =============================================================================
# SkillCurator
# =============================================================================


class _ScriptedProvider:
    """按次序返回预设 text 的最小 provider(不继承 BaseLLMProvider 也无妨,
    curator 只调 .generate)。"""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls = 0

    async def generate(self, messages, tools=None, **kwargs):  # noqa: ANN001
        self.calls += 1
        from mybuddy.llm import LLMResponse

        text = self._texts.pop(0) if self._texts else ""
        return LLMResponse(text=text, finish_reason="stop")


def _mk_trajectory() -> Trajectory:
    traj = Trajectory(
        turn_id="t1",
        session_id="s",
        started_at="2026-05-09T10:00:00",
        system="...",
        user_input="帮我查北京天气然后设个明早9点起床的提醒",
    )
    traj.steps = [
        TrajectoryStep(
            assistant_text="",
            tool_calls=[{"name": "weather", "arguments": {"city": "北京"}}],
            tool_results=[{"name": "weather", "result": "晴 22°C"}],
        ),
        TrajectoryStep(
            assistant_text="",
            tool_calls=[{"name": "set_reminder", "arguments": {"content": "起床", "time": "2026-05-10 09:00"}}],
            tool_results=[{"name": "set_reminder", "result": "ok"}],
        ),
        TrajectoryStep(assistant_text="天气晴,22 度,提醒已设。"),
    ]
    traj.final_response = "天气晴,22 度,提醒已设。"
    return traj


@pytest.mark.asyncio
async def test_curator_creates_skill_when_llm_says_yes(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    provider = _ScriptedProvider(
        [
            '{"should_create": true, "name": "天气+提醒联动",'
            ' "triggers": ["查天气+提醒"], "steps": ["先查天气", "再设提醒"],'
            ' "reason": "常见组合"}'
        ]
    )
    curator = SkillCurator(provider, reg)

    skill = await curator.maybe_curate(_mk_trajectory())
    assert skill is not None
    assert skill.name == "天气+提醒联动"
    assert skill.steps == ["先查天气", "再设提醒"]
    # 文件应已落盘
    assert reg.get("天气+提醒联动") is not None


@pytest.mark.asyncio
async def test_curator_skips_when_llm_says_no(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    provider = _ScriptedProvider(['{"should_create": false}'])
    curator = SkillCurator(provider, reg)

    skill = await curator.maybe_curate(_mk_trajectory())
    assert skill is None
    assert reg.all() == []


@pytest.mark.asyncio
async def test_curator_handles_garbage_llm_output(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    provider = _ScriptedProvider(["这不是 json"])
    curator = SkillCurator(provider, reg)

    skill = await curator.maybe_curate(_mk_trajectory())
    assert skill is None


@pytest.mark.asyncio
async def test_curator_strips_markdown_fence(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    provider = _ScriptedProvider(
        [
            '```json\n{"should_create": true, "name": "test", '
            '"triggers": ["x"], "steps": ["a","b"]}\n```'
        ]
    )
    curator = SkillCurator(provider, reg)

    skill = await curator.maybe_curate(_mk_trajectory())
    assert skill is not None
    assert skill.name == "test"


@pytest.mark.asyncio
async def test_curator_rejects_incomplete_output(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    # should_create=true 但缺 name/triggers/steps
    provider = _ScriptedProvider(['{"should_create": true, "name": "x"}'])
    curator = SkillCurator(provider, reg)

    skill = await curator.maybe_curate(_mk_trajectory())
    assert skill is None


@pytest.mark.asyncio
async def test_curator_swallows_provider_errors(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)

    class BrokenProvider:
        async def generate(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("LLM 挂了")

    curator = SkillCurator(BrokenProvider(), reg)
    # maybe_curate 应返回 None 而不是抛异常
    skill = await curator.maybe_curate(_mk_trajectory())
    assert skill is None
