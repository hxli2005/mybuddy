from __future__ import annotations

import json
from datetime import datetime

import pytest
import yaml

from mybuddy.api import (
    AppState,
    _append_tool_summary,
    _extract_weather_city,
    _integrate_pending_messages,
    _run_deterministic_demo_tools,
)
from mybuddy.config import Config, PersonaConfig, load_config
from mybuddy.learning import SkillRegistry
from mybuddy.memory import LongTermMemory, UserProfile
from mybuddy.storage import (
    Message,
    Note,
    ProfileField,
    Reminder,
    enqueue,
    increment_usage,
    init_db,
    session_scope,
)
from mybuddy.tools import set_context


def test_extract_weather_city() -> None:
    assert _extract_weather_city("北京天气怎么样?") == "北京"
    assert _extract_weather_city("请问上海今天气温") == "上海"


@pytest.mark.asyncio
async def test_deterministic_weather_tool_fallback(monkeypatch) -> None:
    cfg = Config()
    cfg.tools.weather_mock = True
    set_context(config=cfg)

    state = AppState(config_path="config.yaml")
    calls = await _run_deterministic_demo_tools("北京天气怎么样", [], state)

    assert len(calls) == 1
    assert calls[0]["name"] == "weather"
    assert calls[0]["arguments"] == {"city": "北京"}
    assert calls[0]["source"] == "backend_intent_fallback"


@pytest.mark.asyncio
async def test_deterministic_weather_tool_skips_existing_call() -> None:
    state = AppState(config_path="config.yaml")
    calls = await _run_deterministic_demo_tools(
        "北京天气怎么样",
        [{"name": "weather", "arguments": {"city": "北京"}}],
        state,
    )

    assert calls == []


def test_append_weather_summary() -> None:
    text = _append_tool_summary(
        "我帮你查了一下。",
        [
            {
                "name": "weather",
                "result": '{"city":"北京","condition":"晴","temperature_c":22,"humidity":45,"wind_kph":8}',
            }
        ],
    )

    assert "北京当前晴" in text


def test_update_persona_payload_persists_only_persona_section(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """# demo config
llm:
  provider: openrouter
  model: test-model
  api_key: ${OPENROUTER_API_KEY}

# ============ 人设 ============
persona:
  name: "旧名字"
  style: "旧风格"
  language: "中文"

# ============ 记忆系统 ============
memory:
  short_term_size: 12
""",
        encoding="utf-8",
    )
    state = AppState(config_path=str(config_path))
    state.cfg = load_config(config_path)

    payload = state.update_persona_payload(
        {
            "name": "新名字",
            "relationship": "像长期合作的项目伙伴",
            "response_habits": ["先确认目标", "", "给一个下一步"],
        }
    )

    assert payload["persona"]["name"] == "新名字"
    assert payload["persona"]["response_habits"] == ["先确认目标", "给一个下一步"]
    saved = config_path.read_text(encoding="utf-8")
    assert "${OPENROUTER_API_KEY}" in saved
    assert "# ============ 记忆系统 ============" in saved
    raw = yaml.safe_load(saved)
    assert raw["llm"]["api_key"] == "${OPENROUTER_API_KEY}"
    assert raw["persona"]["relationship"] == "像长期合作的项目伙伴"


def test_notes_payload_create_and_list_syncs_archive(tmp_path) -> None:
    engine = init_db(str(tmp_path / "notes.db"))
    ltm = LongTermMemory(persist_dir=tmp_path / "memory")
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.ltm = ltm

    created = state.create_note_payload(
        content="今天重构 MyBuddy 前端控制台",
        title="前端重构",
        tags=["mybuddy", "frontend"],
    )

    assert created["note"]["title"] == "前端重构"
    assert "frontend" in created["note"]["tags"]
    listed = state.notes_payload()
    assert listed["notes"][0]["content"] == "今天重构 MyBuddy 前端控制台"
    with session_scope(engine) as s:
        row = s.query(Note).one()
        assert row.title == "前端重构"
    hits = ltm.search("控制台", mem_type="note")
    assert hits


def test_note_update_and_delete_payload_syncs_archive(tmp_path) -> None:
    engine = init_db(str(tmp_path / "notes_update.db"))
    ltm = LongTermMemory(persist_dir=tmp_path / "memory")
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.ltm = ltm
    created = state.create_note_payload(content="明天整理汇报材料", title="旧标题", tags=["工作"])
    note_id = created["note"]["id"]

    updated = state.update_note_payload(
        note_id,
        content="明天整理汇报材料,先写开场",
        title="汇报准备",
        tags=["工作", "项目"],
    )

    assert updated["note"]["title"] == "汇报准备"
    assert updated["note"]["tags"] == ["工作", "项目"]
    hits = ltm.search("开场", mem_type="note")
    assert hits and hits[0]["metadata"]["title"] == "汇报准备"

    deleted = state.delete_note_payload(note_id)

    assert deleted["ok"] is True
    assert state.notes_payload()["notes"] == []
    assert ltm.list_all(mem_type="note") == []


def test_memory_update_and_delete_note_syncs_sqlite(tmp_path) -> None:
    engine = init_db(str(tmp_path / "notes_sync.db"))
    ltm = LongTermMemory(persist_dir=tmp_path / "memory")
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.ltm = ltm
    created = state.create_note_payload(content="周五要汇报项目", title="项目汇报")
    memory_id = f"note_{created['note']['id']}"

    state.update_memory_payload(memory_id, content="周五要汇报项目,先练开头三十秒")

    assert state.notes_payload()["notes"][0]["content"] == "周五要汇报项目,先练开头三十秒"

    deleted = state.delete_memory_payload(memory_id)

    assert deleted["ok"] is True
    assert state.notes_payload()["notes"] == []
    assert ltm.list_all(mem_type="note") == []


def test_profile_field_update_and_delete_payload(tmp_path) -> None:
    engine = init_db(str(tmp_path / "profile_fields.db"))
    state = AppState(config_path="config.yaml")
    state.profile = UserProfile(engine)

    updated = state.update_profile_field_payload("称呼", "小林")

    assert updated["field"] == {"key": "称呼", "value": "小林"}
    with session_scope(engine) as s:
        assert s.query(ProfileField).filter_by(key="称呼").one().value == "小林"

    deleted = state.delete_profile_field_payload("称呼")

    assert deleted["ok"] is True
    with session_scope(engine) as s:
        assert s.query(ProfileField).filter_by(key="称呼").one_or_none() is None


def test_messages_payload_returns_raw_chat_log(tmp_path) -> None:
    engine = init_db(str(tmp_path / "messages.db"))
    state = AppState(config_path="config.yaml")
    state.engine = engine
    with session_scope(engine) as s:
        s.add(
            Message(
                session_id="s1",
                role="user",
                content="你好",
                meta_json='{"turn_id":"t1"}',
            )
        )
        s.add(
            Message(
                session_id="s1",
                role="assistant",
                content="我在。",
                meta_json='{"turn_id":"t1"}',
            )
        )

    payload = state.messages_payload(limit=1)

    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["role"] == "assistant"
    assert payload["messages"][0]["content"] == "我在。"
    assert payload["messages"][0]["meta"]["turn_id"] == "t1"


def test_users_payload_create_bind_update_and_usage(tmp_path) -> None:
    engine = init_db(str(tmp_path / "users.db"))
    state = AppState(config_path="config.yaml")
    state.engine = engine

    assert state.users_payload() == {"users": []}

    created = state.create_user_payload(display_name="测试用户", daily_message_limit=12)
    user_id = created["user"]["id"]
    assert created["user"]["display_name"] == "测试用户"
    assert created["user"]["status"] == "active"
    assert created["user"]["daily_message_limit"] == 12

    bound = state.bind_user_qq_payload(
        user_id,
        external_id="qq-openid",
        display_name="QQ名",
    )
    assert bound["user"]["external_accounts"] == [
        {
            "provider": "qq",
            "external_id": "qq-openid",
            "display_name": "QQ名",
        }
    ]

    updated = state.update_user_payload(user_id, status="disabled", daily_message_limit=3)
    assert updated["user"]["status"] == "disabled"
    assert updated["user"]["daily_message_limit"] == 3

    increment_usage(engine, user_id=user_id, source="qq", amount=2)
    listed = state.users_payload()["users"]

    assert listed[0]["id"] == user_id
    assert listed[0]["usage_today"] == {"qq": 2}
    assert listed[0]["usage_total_today"] == 2


def test_user_update_rejects_invalid_status(tmp_path) -> None:
    engine = init_db(str(tmp_path / "users_invalid_status.db"))
    state = AppState(config_path="config.yaml")
    state.engine = engine
    created = state.create_user_payload(display_name="测试用户")

    with pytest.raises(RuntimeError, match="active 或 disabled"):
        state.update_user_payload(created["user"]["id"], status="pending")


def test_user_persona_payload_update_and_reset(tmp_path) -> None:
    engine = init_db(str(tmp_path / "user_persona.db"))
    state = AppState(config_path="config.yaml")
    state.engine = engine
    state.cfg = Config(persona=PersonaConfig(name="默认小布", style="默认风格"))
    created = state.create_user_payload(display_name="测试用户")
    user_id = created["user"]["id"]

    inherited = state.user_persona_payload(user_id)

    assert inherited["inherits_default"] is True
    assert inherited["persona"]["name"] == "默认小布"

    updated = state.update_user_persona_payload(
        user_id,
        {
            "name": "用户专属",
            "style": "更简洁",
            "response_habits": ["先给结论"],
        },
    )

    assert updated["inherits_default"] is False
    assert updated["persona"]["name"] == "用户专属"
    assert updated["persona"]["response_habits"] == ["先给结论"]
    assert state.users_payload()["users"][0]["has_custom_persona"] is True

    reset = state.delete_user_persona_payload(user_id)

    assert reset["inherits_default"] is True
    assert reset["persona"]["name"] == "默认小布"
    assert state.users_payload()["users"][0]["has_custom_persona"] is False


def test_integrate_pending_nudge_as_assistant_message(tmp_path) -> None:
    engine = init_db(str(tmp_path / "pending_nudge.db"))
    pending_id = enqueue(
        engine,
        source="nudge",
        content="刚才那件事,要不要接着放到桌上?",
        meta={"origin": "silence_followup"},
    )
    seen = []

    integrated = _integrate_pending_messages(
        engine,
        session_id="s1",
        items=[
            {
                "id": pending_id,
                "source": "nudge",
                "content": "刚才那件事,要不要接着放到桌上?",
                "scheduled_at": "2026-06-05T10:00:00",
                "meta": {"origin": "silence_followup"},
            }
        ],
        add_to_short_term=seen.append,
    )

    assert integrated[0]["role"] == "assistant"
    assert isinstance(integrated[0]["message_id"], int)
    assert seen[0].role.value == "assistant"
    assert seen[0].content == "刚才那件事,要不要接着放到桌上?"
    with session_scope(engine) as s:
        row = s.query(Message).one()
        assert row.session_id == "s1"
        assert row.role == "assistant"
        assert row.content == "刚才那件事,要不要接着放到桌上?"
        meta = json.loads(row.meta_json)
        assert meta["source"] == "pending_message"
        assert meta["pending_source"] == "nudge"


def test_integrate_pending_reminder_stays_system_side_effect(tmp_path) -> None:
    engine = init_db(str(tmp_path / "pending_reminder.db"))
    seen = []

    integrated = _integrate_pending_messages(
        engine,
        session_id="s1",
        items=[
            {
                "id": 1,
                "source": "reminder",
                "content": "提醒:开会",
                "scheduled_at": "2026-06-05T10:00:00",
                "meta": {"reminder_id": 1},
            }
        ],
        add_to_short_term=seen.append,
    )

    assert integrated[0]["role"] == "system"
    assert seen == []
    with session_scope(engine) as s:
        assert s.query(Message).count() == 0


def test_memory_update_and_delete_payload(tmp_path) -> None:
    ltm = LongTermMemory(persist_dir=tmp_path / "memory")
    memory_id = ltm.add("上次我们把任务缩到一个最小动作", mem_type="shared_moment")
    state = AppState(config_path="config.yaml")
    state.ltm = ltm

    updated = state.update_memory_payload(
        memory_id,
        content="上次我们把任务缩到一个最小动作,先处理最容易开始的部分",
    )

    assert updated["memory"]["content"] == "上次我们把任务缩到一个最小动作,先处理最容易开始的部分"
    assert ltm.search("最小动作", mem_type="shared_moment")

    deleted = state.delete_memory_payload(memory_id)

    assert deleted["ok"] is True
    assert ltm.list_all() == []


def test_update_reminder_payload_cancels_pending(tmp_path) -> None:
    engine = init_db(str(tmp_path / "reminders.db"))
    with session_scope(engine) as s:
        row = Reminder(content="开会", trigger_at=datetime(2026, 6, 3, 9, 0), status="pending")
        s.add(row)
        s.flush()
        rid = row.id

    state = AppState(config_path="config.yaml")
    state.engine = engine
    payload = state.update_reminder_payload(rid, "cancelled")

    assert payload["reminder"]["status"] == "cancelled"
    with session_scope(engine) as s:
        assert s.query(Reminder).filter(Reminder.id == rid).one().status == "cancelled"


def test_update_skill_payload_toggles_archive(tmp_path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.create(name="晨间整理", triggers=["早上"], steps=["看提醒"], confidence=0.7)
    state = AppState(config_path="config.yaml")
    state.skill_registry = registry

    archived = state.update_skill_payload("晨间整理", True)
    assert archived["skill"]["archived"] is True
    assert registry.get("晨间整理").archived is True

    restored = state.update_skill_payload("晨间整理", False)
    assert restored["skill"]["archived"] is False
    assert registry.get("晨间整理").archived is False


@pytest.mark.asyncio
async def test_deterministic_reminder_fallback_creates_correct_time(tmp_path, monkeypatch) -> None:
    from mybuddy.tools import reminder as reminder_mod

    monkeypatch.setattr(
        reminder_mod,
        "_local_now",
        lambda: datetime(2026, 5, 18, 10, 0),
    )
    state = AppState(config_path="config.yaml")
    state.engine = init_db(str(tmp_path / "api.db"))

    calls = await _run_deterministic_demo_tools("明天下午三点提醒我练习项目汇报", [], state)

    assert len(calls) == 1
    assert calls[0]["name"] == "set_reminder"
    assert calls[0]["arguments"]["content"] == "练习项目汇报"
    assert calls[0]["arguments"]["time"] == "2026-05-19T15:00"
    with session_scope(state.engine) as s:
        row = s.query(Reminder).one()
        assert row.trigger_at == datetime(2026, 5, 19, 15, 0)


@pytest.mark.asyncio
async def test_deterministic_reminder_repairs_wrong_model_time(tmp_path, monkeypatch) -> None:
    from mybuddy.tools import reminder as reminder_mod

    monkeypatch.setattr(
        reminder_mod,
        "_local_now",
        lambda: datetime(2026, 5, 18, 10, 0),
    )
    engine = init_db(str(tmp_path / "repair.db"))
    with session_scope(engine) as s:
        row = Reminder(
            content="练习项目汇报",
            trigger_at=datetime(2026, 5, 18, 15, 0),
            status="pending",
        )
        s.add(row)
        s.flush()
        rid = row.id

    state = AppState(config_path="config.yaml")
    state.engine = engine
    existing = [
        {
            "id": "call_1",
            "name": "set_reminder",
            "arguments": {"content": "练习项目汇报", "time": "2026-05-18T15:00"},
            "result": json.dumps(
                {
                    "ok": True,
                    "id": rid,
                    "content": "练习项目汇报",
                    "trigger_at": "2026-05-18T15:00",
                },
                ensure_ascii=False,
            ),
        }
    ]

    calls = await _run_deterministic_demo_tools(
        "明天下午三点提醒我练习项目汇报",
        existing,
        state,
    )

    assert calls == []
    assert existing[0]["source"] == "backend_time_correction"
    assert existing[0]["arguments"]["time"] == "2026-05-19T15:00"
    with session_scope(engine) as s:
        repaired = s.query(Reminder).filter(Reminder.id == rid).one()
        assert repaired.trigger_at == datetime(2026, 5, 19, 15, 0)
