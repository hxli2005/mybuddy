"""FastAPI 后端 + 静态前端入口。

这是演示用单用户后端:复用现有 Agent、Memory、Tools、FeedbackBus 装配,
并把 `frontend/` 里的静态页面托管出来。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

from mybuddy.agent import Agent
from mybuddy.config import Config, PersonaConfig, ensure_dirs, load_config
from mybuddy.emotion import EmotionDetector, EmotionTracker
from mybuddy.learning import (
    FeedbackBus,
    FeedbackEvent,
    SkillCurator,
    SkillRegistry,
    TrajectoryLogger,
    make_profile_claim_subscriber,
    make_skill_subscriber,
    make_trajectory_subscriber,
)
from mybuddy.llm import make_provider
from mybuddy.memory import LongTermMemory, MemoryManager, UserProfile
from mybuddy.scheduler import MyBuddyScheduler
from mybuddy.storage import Reminder, drain_pending, init_db, list_undelivered, session_scope
from mybuddy.tools import ToolRegistry, set_context, setup_memory_tool, setup_skill_tool
from mybuddy.tools.reminder import parse_reminder_time

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.llm import BaseLLMProvider


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class FeedbackRequest(BaseModel):
    label: str
    turn_id: str | None = None


class PersonaUpdateRequest(BaseModel):
    name: str | None = None
    style: str | None = None
    language: str | None = None
    relationship: str | None = None
    tone: str | None = None
    boundaries: str | None = None
    response_habits: list[str] | None = None
    address_user: str | None = None


@dataclass
class AppState:
    config_path: str
    max_steps: int = 6
    enable_scheduler: bool = True
    cfg: Config | None = None
    engine: Engine | None = None
    provider: BaseLLMProvider | None = None
    ltm: LongTermMemory | None = None
    profile: UserProfile | None = None
    skill_registry: SkillRegistry | None = None
    scheduler: MyBuddyScheduler | None = None
    agent: Agent | None = None
    feedback_bus: FeedbackBus | None = None
    last_turn_id: str | None = None
    last_related_claim_ids: list[int] = field(default_factory=list)
    last_triggered_skills: list[str] = field(default_factory=list)

    def startup(self) -> None:
        cfg = load_config(self.config_path)
        ensure_dirs(cfg)
        engine = init_db(cfg.paths.db_file)
        ltm = LongTermMemory(
            persist_dir=cfg.paths.chroma_dir,
            embedding_model=cfg.memory.embedding_model,
        )
        provider = make_provider(cfg.llm) if cfg.llm.api_key else None
        logger = TrajectoryLogger(cfg.paths.trajectories_dir)
        profile = UserProfile(engine, ltm)
        skill_registry = SkillRegistry.load_all(cfg.paths.skills_dir)

        scheduler: MyBuddyScheduler | None = None
        if cfg.scheduler.enabled and self.enable_scheduler:
            scheduler = MyBuddyScheduler(cfg)
            scheduler.start()
            _restore_reminders(scheduler, engine)
            scheduler.schedule_daily_greeting(cfg.scheduler.daily_greeting)
            scheduler.schedule_dream_job(cfg.scheduler.dream_job, config_path=self.config_path)

        agent: Agent | None = None
        feedback_bus: FeedbackBus | None = None
        if provider is not None:
            registry = ToolRegistry.default()
            memory = MemoryManager(engine=engine, config=cfg, ltm=ltm, provider=provider)
            setup_memory_tool(ltm)
            setup_skill_tool(skill_registry)
            feedback_bus = FeedbackBus()
            feedback_bus.subscribe(make_trajectory_subscriber(logger))
            feedback_bus.subscribe(make_profile_claim_subscriber(profile))
            feedback_bus.subscribe(make_skill_subscriber(skill_registry))
            agent = Agent(
                provider=provider,
                config=cfg,
                registry=registry,
                memory=memory,
                trajectory_logger=logger,
                max_steps=self.max_steps,
                emotion_detector=EmotionDetector(provider, cfg.llm.small_model),
                emotion_tracker=EmotionTracker(window=5),
                engine=engine,
                skill_registry=skill_registry,
                skill_curator=SkillCurator(provider, skill_registry, model=cfg.llm.small_model),
            )
            set_context(
                engine=engine,
                config=cfg,
                scheduler=scheduler,
                provider=provider,
                long_term=ltm,
            )
        else:
            set_context(engine=engine, config=cfg, scheduler=scheduler, long_term=ltm)

        self.cfg = cfg
        self.engine = engine
        self.provider = provider
        self.ltm = ltm
        self.profile = profile
        self.skill_registry = skill_registry
        self.scheduler = scheduler
        self.agent = agent
        self.feedback_bus = feedback_bus

    def shutdown(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown()

    def status_payload(self) -> dict[str, Any]:
        cfg = _require(self.cfg)
        scheduler_jobs = self.scheduler.list_jobs() if self.scheduler is not None else []
        return {
            "configured": bool(cfg.llm.api_key),
            "persona": cfg.persona.model_dump(),
            "model": cfg.llm.model,
            "tools": ToolRegistry.default().names(),
            "scheduler_jobs": scheduler_jobs,
            "memory_dir": cfg.paths.chroma_dir,
        }

    def persona_payload(self) -> dict[str, Any]:
        cfg = _require(self.cfg)
        return {"persona": cfg.persona.model_dump()}

    def update_persona_payload(self, updates: dict[str, Any]) -> dict[str, Any]:
        cfg = _require(self.cfg)
        merged = cfg.persona.model_dump()
        merged.update(_clean_persona_updates(updates))
        persona = PersonaConfig.model_validate(merged)
        _write_persona_config(self.config_path, persona)

        updated_cfg = load_config(self.config_path)
        self._sync_config(updated_cfg)
        return {"persona": updated_cfg.persona.model_dump()}

    def _sync_config(self, cfg: Config) -> None:
        self.cfg = cfg
        if self.agent is not None:
            self.agent._config = cfg
            self.agent._memory._config = cfg
        if self.scheduler is not None:
            self.scheduler._config = cfg
            if self.scheduler.running:
                self.scheduler.schedule_daily_greeting(cfg.scheduler.daily_greeting)
        set_context(
            engine=self.engine,
            config=cfg,
            scheduler=self.scheduler,
            provider=self.provider,
            long_term=self.ltm,
        )

    async def chat_payload(self, message: str) -> dict[str, Any]:
        if self.agent is None:
            raise RuntimeError("LLM api_key 未配置,无法对话")
        engine = _require(self.engine)
        pending_before = drain_pending(engine)
        result = await self.agent.run(message.strip())
        result_text = result.text
        tool_calls = list(result.tool_calls)
        deterministic_tools = await _run_deterministic_demo_tools(message, tool_calls, self)
        if deterministic_tools:
            tool_calls.extend(deterministic_tools)
        result_text = _append_tool_summary(result_text, tool_calls)
        pending_after = drain_pending(engine)
        self.last_turn_id = result.trajectory.turn_id
        self.last_related_claim_ids = list(result.related_claim_ids)
        self.last_triggered_skills = list(result.triggered_skills)
        return {
            "text": result_text,
            "turn_id": result.trajectory.turn_id,
            "steps": result.steps,
            "finish_reason": result.finish_reason,
            "tool_calls": tool_calls,
            "emotion": result.emotion.to_dict() if result.emotion else None,
            "emotional_support": result.emotional_support,
            "related_claim_ids": result.related_claim_ids,
            "triggered_skills": result.triggered_skills,
            "pending_messages": pending_before + pending_after,
        }

    def feedback_payload(self, label: str, turn_id: str | None = None) -> dict[str, Any]:
        if self.feedback_bus is None:
            raise RuntimeError("反馈总线未初始化")
        tid = turn_id or self.last_turn_id
        if not tid:
            raise RuntimeError("没有可反馈的对话轮次")
        clean_label = label.strip()
        self.feedback_bus.publish(
            FeedbackEvent(
                turn_id=tid,
                label=clean_label,
                related_claim_ids=list(self.last_related_claim_ids),
                meta={"triggered_skills": list(self.last_triggered_skills)},
            )
        )
        return {"ok": True, "turn_id": tid, "label": clean_label}

    def profile_payload(self) -> dict[str, Any]:
        p = _require(self.profile)
        return {
            "fields": p.get_all_fields(),
            "claims": p.get_all_claims(min_confidence=0.0)[:20],
        }

    def memory_payload(self) -> dict[str, Any]:
        cfg = _require(self.cfg)
        ltm = _require(self.ltm)
        base = Path(cfg.paths.chroma_dir)
        return {
            "archive": ltm.list_all()[:50],
            "conversations": _read_jsonl_tail(base / "conversations", limit=20),
            "raw": _read_jsonl_tail(base / "raw", limit=20),
        }

    def reminders_payload(self) -> dict[str, Any]:
        engine = _require(self.engine)
        with session_scope(engine) as s:
            rows = s.query(Reminder).order_by(Reminder.trigger_at.asc()).limit(30).all()
            items = [
                {
                    "id": r.id,
                    "content": r.content,
                    "trigger_at": r.trigger_at.isoformat(timespec="minutes"),
                    "status": r.status,
                }
                for r in rows
            ]
        return {"reminders": items, "pending_messages": list_undelivered(engine)}

    def skills_payload(self) -> dict[str, Any]:
        registry = _require(self.skill_registry)
        return {
            "skills": [
                {
                    "name": s.name,
                    "triggers": s.triggers,
                    "confidence": s.confidence,
                    "success_count": s.success_count,
                    "fail_count": s.fail_count,
                    "archived": s.archived,
                }
                for s in registry.all(include_archived=True)
            ]
        }


def create_app(config_path: str = "config.yaml", max_steps: int = 6):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
    except ModuleNotFoundError as e:  # pragma: no cover - 只有未安装 api extra 时触发
        raise RuntimeError("缺少 API 依赖,请运行: uv sync --extra api") from e

    state = AppState(config_path=config_path, max_steps=max_steps)
    app = FastAPI(title="MyBuddy Demo API")
    app.state.mybuddy = state

    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    if frontend_dir.exists():
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.on_event("startup")
    async def _startup() -> None:
        state.startup()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        state.shutdown()

    @app.get("/")
    async def index():
        path = frontend_dir / "index.html"
        if not path.exists():
            raise HTTPException(status_code=404, detail="frontend/index.html not found")
        return FileResponse(path)

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return state.status_payload()

    @app.get("/api/persona")
    async def persona() -> dict[str, Any]:
        return state.persona_payload()

    @app.put("/api/persona")
    async def update_persona(req: PersonaUpdateRequest) -> dict[str, Any]:
        return state.update_persona_payload(req.model_dump(exclude_none=True))

    @app.post("/api/persona")
    async def update_persona_post(req: PersonaUpdateRequest) -> dict[str, Any]:
        return state.update_persona_payload(req.model_dump(exclude_none=True))

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> dict[str, Any]:
        if state.agent is None:
            raise HTTPException(status_code=400, detail="LLM api_key 未配置,无法对话")
        return await state.chat_payload(req.message)

    @app.post("/api/feedback")
    async def feedback(req: FeedbackRequest) -> dict[str, Any]:
        try:
            return state.feedback_payload(req.label, req.turn_id)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/profile")
    async def profile() -> dict[str, Any]:
        return state.profile_payload()

    @app.get("/api/memory")
    async def memory() -> dict[str, Any]:
        return state.memory_payload()

    @app.get("/api/reminders")
    async def reminders() -> dict[str, Any]:
        return state.reminders_payload()

    @app.get("/api/skills")
    async def skills() -> dict[str, Any]:
        return state.skills_payload()

    return app


def _restore_reminders(scheduler: MyBuddyScheduler, engine: Engine) -> None:
    from mybuddy._time import utcnow

    now = utcnow()
    with session_scope(engine) as s:
        rows = (
            s.query(Reminder)
            .filter(Reminder.status == "pending")
            .filter(Reminder.trigger_at > now)
            .all()
        )
        pending = [(r.id, r.trigger_at) for r in rows]
    for rid, trigger in pending:
        scheduler.schedule_reminder(rid, trigger)


def _clean_persona_updates(updates: dict[str, Any]) -> dict[str, Any]:
    allowed = set(PersonaConfig.model_fields)
    clean: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in allowed or value is None:
            continue
        if key == "response_habits":
            if isinstance(value, list):
                clean[key] = [str(item).strip() for item in value if str(item).strip()]
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        clean[key] = value
    return clean


def _write_persona_config(config_path: str, persona: PersonaConfig) -> None:
    path = Path(config_path)
    replacement = yaml.safe_dump(
        {"persona": persona.model_dump()},
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    if not path.exists():
        path.write_text(replacement + "\n", encoding="utf-8")
        return

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.startswith("persona:")), None)
    if start is None:
        suffix = "" if text.endswith("\n") else "\n"
        path.write_text(text + suffix + "\n" + replacement + "\n", encoding="utf-8")
        return

    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.startswith("#") or (line.strip() and not line.startswith((" ", "\t"))):
            break
        if line.strip() == "":
            break
        end += 1

    new_text = "".join(lines[:start]) + replacement + "\n" + "".join(lines[end:])
    path.write_text(new_text, encoding="utf-8")


WEATHER_INTENT_RE = re.compile(r"(天气|气温|下雨|降雨|温度|weather)", re.I)


async def _run_deterministic_demo_tools(
    message: str,
    existing_tool_calls: list[dict[str, Any]],
    state: AppState,
) -> list[dict[str, Any]]:
    """演示稳定性补偿:明显工具意图但模型未调用/传错参数时,后端校正。"""
    reminder_calls = _repair_or_run_reminder(message, existing_tool_calls, state)
    weather_calls = await _run_weather_fallback(message, existing_tool_calls)
    return reminder_calls + weather_calls


async def _run_weather_fallback(
    message: str,
    existing_tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if any(call.get("name") == "weather" for call in existing_tool_calls):
        return []
    if not WEATHER_INTENT_RE.search(message):
        return []
    city = _extract_weather_city(message)
    if not city:
        return []
    result_text = await ToolRegistry.default().execute("weather", {"city": city})
    return [
        {
            "id": "det_weather",
            "name": "weather",
            "arguments": {"city": city},
            "result": result_text,
            "source": "backend_intent_fallback",
        }
    ]


REMINDER_INTENT_RE = re.compile(r"(提醒我|提醒一下|记得提醒|叫我|闹钟|remind)", re.I)


def _repair_or_run_reminder(
    message: str,
    existing_tool_calls: list[dict[str, Any]],
    state: AppState,
) -> list[dict[str, Any]]:
    parsed = _parse_reminder_request(message)
    if parsed is None:
        return []
    content, trigger = parsed
    trigger_iso = trigger.isoformat(timespec="minutes")
    existing = [c for c in existing_tool_calls if c.get("name") == "set_reminder"]
    if existing:
        for call in existing:
            result = _safe_json(call.get("result"))
            reminder_id = result.get("id")
            old_time = result.get("trigger_at") or call.get("arguments", {}).get("time")
            if old_time == trigger_iso:
                continue
            if isinstance(reminder_id, int):
                _update_reminder_time(state, reminder_id, trigger)
            result.update(
                {
                    "ok": True,
                    "id": reminder_id,
                    "content": result.get("content") or content,
                    "trigger_at": trigger_iso,
                    "scheduled": result.get("scheduled", False),
                    "corrected": True,
                }
            )
            call["arguments"] = {"content": result["content"], "time": trigger_iso}
            call["result"] = json.dumps(result, ensure_ascii=False)
            call["source"] = "backend_time_correction"
        return []

    reminder_id = _create_reminder(state, content, trigger)
    return [
        {
            "id": "det_reminder",
            "name": "set_reminder",
            "arguments": {"content": content, "time": trigger_iso},
            "result": json.dumps(
                {
                    "ok": True,
                    "id": reminder_id,
                    "content": content,
                    "trigger_at": trigger_iso,
                    "scheduled": False,
                },
                ensure_ascii=False,
            ),
            "source": "backend_time_fallback",
        }
    ]


def _parse_reminder_request(message: str) -> tuple[str, Any] | None:
    if not REMINDER_INTENT_RE.search(message):
        return None
    try:
        trigger = parse_reminder_time(message)
    except (TypeError, ValueError):
        return None
    content = _extract_reminder_content(message)
    if not content:
        content = "提醒事项"
    return content, trigger


def _extract_reminder_content(message: str) -> str:
    text = message.strip()
    text = re.sub(r".*?提醒我", "", text)
    text = re.sub(r".*?提醒一下", "", text)
    text = re.sub(r".*?叫我", "", text)
    text = re.sub(r"(今天|明天|后天|大后天)?(上午|下午|晚上|早上|中午|凌晨)?[零〇一二两三四五六七八九十0-9]{1,3}\s*点\s*(半|[零〇一二两三四五六七八九十0-9]{1,3}分?)?", "", text)
    text = re.sub(r"[0-9]{1,2}\s*[:：]\s*[0-9]{1,2}", "", text)
    text = text.strip(" ，,。.!！?？")
    return text


def _create_reminder(state: AppState, content: str, trigger: Any) -> int:
    engine = _require(state.engine)
    with session_scope(engine) as s:
        row = Reminder(content=content, trigger_at=trigger, status="pending")
        s.add(row)
        s.flush()
        reminder_id = row.id
    if state.scheduler is not None and state.scheduler.running:
        state.scheduler.schedule_reminder(reminder_id, trigger)
    return reminder_id


def _update_reminder_time(state: AppState, reminder_id: int, trigger: Any) -> None:
    engine = _require(state.engine)
    with session_scope(engine) as s:
        row = s.query(Reminder).filter(Reminder.id == reminder_id).one_or_none()
        if row is not None:
            row.trigger_at = trigger
            row.status = "pending"
    if state.scheduler is not None and state.scheduler.running:
        state.scheduler.schedule_reminder(reminder_id, trigger)


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_weather_city(message: str) -> str:
    text = message.strip()
    text = re.sub(r"[?？!！。,.，]", "", text)
    text = re.sub(r"(请问|帮我|查询|查一下|看一下|现在|今天|当前|的)", "", text)
    text = re.sub(r"(天气怎么样|天气如何|天气|气温|温度|会下雨吗|下雨吗|weather)", "", text, flags=re.I)
    return text.strip() or "北京"


def _append_tool_summary(text: str, tool_calls: list[dict[str, Any]]) -> str:
    reminder_call = next((c for c in tool_calls if c.get("name") == "set_reminder"), None)
    if reminder_call is not None:
        data = _safe_json(reminder_call.get("result"))
        trigger = data.get("trigger_at")
        content = data.get("content")
        if trigger and content:
            summary = f"已设置提醒:{trigger} 提醒你{content}。"
            return f"{text}\n\n{summary}" if text and summary not in text else text or summary

    weather_call = next((c for c in tool_calls if c.get("name") == "weather"), None)
    if weather_call is None:
        return text
    try:
        data = json.loads(weather_call.get("result", "{}"))
    except json.JSONDecodeError:
        return text
    if not isinstance(data, dict):
        return text
    summary = (
        f"{data.get('city', '')}当前{data.get('condition', '天气信息可用')}, "
        f"{data.get('temperature_c', '-')}°C, 湿度 {data.get('humidity', '-')}%, "
        f"风速 {data.get('wind_kph', '-')} km/h。"
    )
    if text and summary in text:
        return text
    return f"{text}\n\n{summary}" if text else summary


def _read_jsonl_tail(directory: Path, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not directory.exists():
        return rows
    for path in sorted(directory.glob("*.jsonl"), reverse=True):
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(rows) >= limit:
                return rows
    return rows


def _require(value: Any) -> Any:
    if value is None:
        raise RuntimeError("application state is not initialized")
    return value
