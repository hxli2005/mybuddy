"""动态角色生活状态合成器测试(离线、确定性)。"""

from __future__ import annotations

from datetime import timedelta

from mybuddy._time import utcnow
from mybuddy.agent.context import build_system_prompt
from mybuddy.agent.living_state import synthesize_living_state
from mybuddy.config import CharacterLifeConfig, PersonaConfig
from mybuddy.storage.db import init_db, session_scope
from mybuddy.storage.models import Message


def _seed(engine, role: str, content: str, ago_minutes: float) -> None:
    with session_scope(engine) as s:
        s.add(
            Message(
                session_id="s1",
                role=role,
                content=content,
                created_at=utcnow() - timedelta(minutes=ago_minutes),
            )
        )


def test_no_engine_returns_static() -> None:
    p = PersonaConfig()
    assert synthesize_living_state(p, engine=None) is p.character_life


def test_no_messages_returns_static(tmp_path) -> None:
    engine = init_db(str(tmp_path / "t.db"))
    p = PersonaConfig()
    out = synthesize_living_state(p, engine=engine, session_id="s1")
    assert out.today_status == p.character_life.today_status


def test_recent_gap_is_relaxed_and_weaves_topic(tmp_path) -> None:
    engine = init_db(str(tmp_path / "t.db"))
    _seed(engine, "user", "论文 baseline 复现对不上", ago_minutes=5)
    out = synthesize_living_state(PersonaConfig(), engine=engine, session_id="s1")
    assert "松快" in out.current_mood or "逗你" in out.current_mood
    assert "论文" in out.recent_self_event  # 接真记忆:用上次真话题


def test_long_gap_says_misses_you(tmp_path) -> None:
    engine = init_db(str(tmp_path / "t.db"))
    _seed(engine, "user", "这几天去爬山了", ago_minutes=60 * 24 * 6)  # 6 天前
    out = synthesize_living_state(PersonaConfig(), engine=engine, session_id="s1")
    assert "想你" in out.current_mood


def test_picks_substantive_topic_not_closer(tmp_path) -> None:
    engine = init_db(str(tmp_path / "t.db"))
    _seed(engine, "user", "今天组会又被问住了,有点烦", ago_minutes=20)
    _seed(engine, "assistant", "先别急着自责", ago_minutes=19)
    _seed(engine, "user", "好 谢谢你", ago_minutes=18)  # 收尾语,不该被当话题
    out = synthesize_living_state(PersonaConfig(), engine=engine, session_id="s1")
    assert "组会" in out.recent_self_event
    assert "谢谢" not in out.recent_self_event


def test_greeting_not_used_as_topic(tmp_path) -> None:
    engine = init_db(str(tmp_path / "t.db"))
    _seed(engine, "user", "在吗", ago_minutes=5)
    p = PersonaConfig()
    out = synthesize_living_state(p, engine=engine, session_id="s1")
    assert out.recent_self_event == p.character_life.recent_self_event  # 回退,不造空洞句


def test_prompt_uses_dynamic_life() -> None:
    dyn = CharacterLifeConfig(
        today_status="夜里留着灯等你",
        current_mood="有点想你",
        recent_self_event="刚还在想你提的论文",
        availability_style="你来我才在",
    )
    out = build_system_prompt(PersonaConfig(), life=dyn)
    assert "夜里留着灯等你" in out
    assert "有点想你" in out
    # 不传 life 时回退静态配置
    static = build_system_prompt(PersonaConfig())
    assert "夜里留着灯等你" not in static
