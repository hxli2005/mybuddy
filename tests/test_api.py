from __future__ import annotations

import json
from datetime import datetime

import pytest
import yaml

from mybuddy.api import (
    AppState,
    _append_tool_summary,
    _extract_weather_city,
    _run_deterministic_demo_tools,
)
from mybuddy.config import Config, load_config
from mybuddy.storage import Reminder, init_db, session_scope
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
