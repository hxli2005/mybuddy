"""MyBuddy CLI 入口(typer)。

命令:
  mybuddy version               打印版本
  mybuddy init                  初始化配置 + 数据库
  mybuddy chat                  多轮 ReAct 对话(M2)
  mybuddy dream run             手动触发 Dream Job(M4)
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown

from mybuddy import __version__
from mybuddy.agent import Agent
from mybuddy.cli_admin import register as _register_admin
from mybuddy.config import Config, ensure_dirs, load_config
from mybuddy.emotion import EmotionDetector, EmotionTracker
from mybuddy.learning import (
    DreamJob,
    FeedbackBus,
    FeedbackEvent,
    SkillCurator,
    SkillRegistry,
    TrajectoryLogger,
    detect_implicit_negative,
    make_profile_claim_subscriber,
    make_skill_subscriber,
    make_trajectory_subscriber,
)
from mybuddy.llm import make_provider
from mybuddy.memory import LongTermMemory, MemoryManager, UserProfile
from mybuddy.scheduler import MyBuddyScheduler
from mybuddy.storage import Reminder, drain_pending, init_db, session_scope
from mybuddy.tools import ToolRegistry, set_context, setup_memory_tool, setup_skill_tool

app = typer.Typer(help="MyBuddy — 生活陪伴型 AI 小伙伴", no_args_is_help=True)
dream_app = typer.Typer(help="Dream Job 手动入口")
app.add_typer(dream_app, name="dream")
# profile / reminders / skills 子命令
_register_admin(app)

console = Console()


@app.command()
def version() -> None:
    """打印当前版本。"""
    console.print(f"mybuddy {__version__}")


@app.command()
def init(
    config_path: str = typer.Option("config.yaml", "--config", help="目标配置文件路径"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的 config.yaml"),
) -> None:
    """初始化项目运行目录、配置和数据库。"""
    cfg_path = Path(config_path)
    template = Path("config.example.yaml")

    if cfg_path.exists() and not force:
        console.print(f"[yellow]config 已存在,跳过拷贝:[/yellow] {cfg_path}")
    else:
        if not template.exists():
            console.print(f"[red]找不到模板 {template},无法拷贝配置。[/red]")
            raise typer.Exit(code=1)
        shutil.copyfile(template, cfg_path)
        console.print(f"[green]已生成配置文件:[/green] {cfg_path}")

    cfg = load_config(cfg_path)
    ensure_dirs(cfg)
    console.print(f"[green]目录就绪:[/green] {cfg.paths.data_dir}")
    init_db(cfg.paths.db_file)
    console.print(f"[green]数据库就绪:[/green] {cfg.paths.db_file}")

    console.print(
        "\n[bold]下一步:[/bold]编辑 config.yaml 填入 API key,然后运行 [cyan]mybuddy chat[/cyan]"
    )


@app.command()
def chat(
    config_path: str = typer.Option("config.yaml", "--config", help="配置文件路径"),
    max_steps: int = typer.Option(6, "--max-steps", help="单轮 ReAct 最大步数"),
) -> None:
    """进入与小伙伴的多轮对话。

    内置命令:
      /exit           退出
      /good           给上一轮标 positive
      /bad            给上一轮标 negative
      /fix <修正>     给上一轮标 fix,附带你希望的正确回复
    """
    cfg = load_config(config_path)
    ensure_dirs(cfg)
    engine = init_db(cfg.paths.db_file)

    if not cfg.llm.api_key:
        console.print(
            "[red]未检测到 LLM api_key。请在 config.yaml 或环境变量 "
            "ANTHROPIC_API_KEY 中填入后重试。[/red]"
        )
        raise typer.Exit(code=1)

    provider = make_provider(cfg.llm)
    registry = ToolRegistry.default()
    logger = TrajectoryLogger(cfg.paths.trajectories_dir)

    # M3:初始化分层记忆系统
    ltm = LongTermMemory(
        persist_dir=cfg.paths.chroma_dir,
        embedding_model=cfg.memory.embedding_model,
    )
    memory = MemoryManager(
        engine=engine,
        config=cfg,
        ltm=ltm,
        provider=provider,
    )
    setup_memory_tool(ltm)

    # M5:情绪 + 反馈
    emotion_detector = EmotionDetector(provider, cfg.llm.small_model)
    emotion_tracker = EmotionTracker(window=5)
    profile = UserProfile(engine, ltm)
    feedback_bus = FeedbackBus()
    feedback_bus.subscribe(make_trajectory_subscriber(logger))
    feedback_bus.subscribe(make_profile_claim_subscriber(profile))

    # M6:skills
    skill_registry = SkillRegistry.load_all(cfg.paths.skills_dir)
    skill_curator = SkillCurator(provider, skill_registry, model=cfg.llm.small_model)
    feedback_bus.subscribe(make_skill_subscriber(skill_registry))
    setup_skill_tool(skill_registry)

    agent = Agent(
        provider=provider,
        config=cfg,
        registry=registry,
        memory=memory,
        trajectory_logger=logger,
        max_steps=max_steps,
        emotion_detector=emotion_detector,
        emotion_tracker=emotion_tracker,
        engine=engine,
        skill_registry=skill_registry,
        skill_curator=skill_curator,
    )

    # M4:调度器
    scheduler: MyBuddyScheduler | None = None
    if cfg.scheduler.enabled:
        scheduler = MyBuddyScheduler(cfg)
        scheduler.start()
        _restore_reminders(scheduler, engine)
        scheduler.schedule_daily_greeting(cfg.scheduler.daily_greeting)
        scheduler.schedule_dream_job(cfg.scheduler.dream_job, config_path=config_path)

    set_context(
        engine=engine,
        config=cfg,
        scheduler=scheduler,
        provider=provider,
        long_term=ltm,
    )

    _print_banner(cfg, registry, scheduler=scheduler, skill_registry=skill_registry)

    try:
        asyncio.run(_chat_loop(agent, engine, feedback_bus))
    finally:
        if scheduler is not None:
            scheduler.shutdown()


@app.command()
def web(
    config_path: str = typer.Option("config.yaml", "--config", help="配置文件路径"),
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    port: int = typer.Option(8000, "--port", help="监听端口"),
    max_steps: int = typer.Option(6, "--max-steps", help="单轮 ReAct 最大步数"),
) -> None:
    """启动演示前端 + 本地 JSON 后端。"""
    from mybuddy.web import serve

    console.print(f"[green]MyBuddy Web:[/green] http://{host}:{port}")
    serve(config_path=config_path, host=host, port=port, max_steps=max_steps)


@dream_app.command("run")
def dream_run(
    config_path: str = typer.Option("config.yaml", "--config", help="配置文件路径"),
) -> None:
    """立即执行 Dream Job 五件事(开发期手动触发)。"""
    cfg = load_config(config_path)
    ensure_dirs(cfg)
    engine = init_db(cfg.paths.db_file)

    if not cfg.llm.api_key:
        console.print("[red]未检测到 LLM api_key,Dream Job 需要 LLM 支持。[/red]")
        raise typer.Exit(code=1)

    provider = make_provider(cfg.llm)
    ltm = LongTermMemory(
        persist_dir=cfg.paths.chroma_dir,
        embedding_model=cfg.memory.embedding_model,
    )
    profile = UserProfile(engine, ltm)
    job = DreamJob(
        engine=engine,
        config=cfg,
        provider=provider,
        ltm=ltm,
        profile=profile,
    )

    console.print("[dim]Dream Job 开始...[/dim]")
    report = asyncio.run(job.run())
    console.print(f"[green]{report.summary()}[/green]")
    if report.errors:
        console.print("[yellow]部分步骤失败:[/yellow]")
        for e in report.errors:
            console.print(f"  · {e}")


# ---------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------

def _restore_reminders(scheduler: MyBuddyScheduler, engine) -> None:
    """CLI 启动时把 pending 状态的 reminders 全部重新注册到调度器。

    SQLAlchemyJobStore 本身会恢复已有 job,这里是兜底 —— 即便 job 丢失
    (例如 DB 被清空过),仍能从 reminders 表重建。幂等(replace_existing)。
    """
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


async def _chat_loop(agent: Agent, engine, feedback_bus: FeedbackBus) -> None:
    last_turn_id: str | None = None
    last_related_claim_ids: list[int] = []
    last_triggered_skills: list[str] = []

    # 启动时先 drain 一次(可能有离线期间触发的提醒/早安/nudge)
    _drain_pending_to_console(engine)

    while True:
        try:
            user_input = console.input("[bold cyan]你[/bold cyan] > ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见~[/dim]")
            return

        if not user_input:
            _drain_pending_to_console(engine)
            continue

        if user_input == "/exit":
            console.print("[dim]再见~[/dim]")
            return

        if user_input.startswith(("/good", "/bad", "/fix")):
            if last_turn_id is None:
                console.print("[yellow]没有可标注的上一轮。[/yellow]")
                continue
            label = _parse_label(user_input)
            feedback_bus.publish(
                FeedbackEvent(
                    turn_id=last_turn_id,
                    label=label,
                    related_claim_ids=last_related_claim_ids,
                    meta={"triggered_skills": list(last_triggered_skills)},
                )
            )
            console.print(f"[dim]已记录反馈:{label}[/dim]")
            continue

        # 隐式反馈:若本条消息含纠错关键词,给上一轮补打 implicit:negative
        if last_turn_id is not None and detect_implicit_negative(user_input):
            feedback_bus.publish(
                FeedbackEvent(
                    turn_id=last_turn_id,
                    label="implicit:negative",
                    related_claim_ids=last_related_claim_ids,
                    meta={
                        "triggered_skills": list(last_triggered_skills),
                        "trigger_text": user_input[:40],
                    },
                )
            )

        # 每轮对话开始前 drain 一次
        _drain_pending_to_console(engine)

        try:
            result = await agent.run(user_input)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]出错了:{type(e).__name__}: {e}[/red]")
            continue

        last_turn_id = result.trajectory.turn_id
        last_related_claim_ids = list(result.related_claim_ids)
        last_triggered_skills = list(result.triggered_skills)
        _render_response(
            result.text,
            tool_count=len(result.tool_calls),
            steps=result.steps,
            emotion=_format_emotion(result),
            skills=last_triggered_skills,
        )


def _format_emotion(result) -> str:
    if result.emotion is None:
        return ""
    e = result.emotion
    if e.label == "neutral" and e.strength < 0.3:
        return ""
    return f"{e.label} ({e.strength:.1f})"


def _drain_pending_to_console(engine) -> None:
    items = drain_pending(engine)
    if not items:
        return
    for it in items:
        tag = {
            "reminder": "⏰ 提醒",
            "greeting": "🌅 问候",
            "nudge": "💭 捎个话",
        }.get(it["source"], it["source"])
        console.print(f"[bold yellow]{tag}[/bold yellow] {it['content']}")


def _render_response(
    text: str,
    *,
    tool_count: int,
    steps: int,
    emotion: str = "",
    skills: list[str] | None = None,
) -> None:
    if text:
        console.print("[bold magenta]小伙伴[/bold magenta] >", end=" ")
        console.print(Markdown(text))
    else:
        console.print("[dim](无文本响应)[/dim]")
    tail: list[str] = []
    if tool_count:
        tail.append(f"使用了 {tool_count} 次工具,共 {steps} 步")
    if emotion:
        tail.append(f"情绪 {emotion}")
    if skills:
        tail.append(f"参考 skill {', '.join(skills)}")
    if tail:
        console.print(f"[dim]· {' · '.join(tail)}[/dim]")


def _parse_label(cmd: str) -> str:
    if cmd.startswith("/good"):
        return "good"
    if cmd.startswith("/bad"):
        return "bad"
    rest = cmd[len("/fix"):].strip()
    return f"fix:{rest}" if rest else "fix"


def _print_banner(
    cfg: Config,
    registry: ToolRegistry,
    *,
    scheduler: MyBuddyScheduler | None = None,
    skill_registry: SkillRegistry | None = None,
) -> None:
    persona = cfg.persona
    console.print(
        f"[bold green]MyBuddy v{__version__}[/bold green] · 小伙伴 "
        f"[italic]{persona.name}[/italic] 已上线"
    )
    extra_lines: list[str] = []
    if scheduler is not None:
        extra_lines.append(f"调度器 {len(scheduler.list_jobs())} 任务")
    if skill_registry is not None:
        active = skill_registry.all()
        total = len(skill_registry.all(include_archived=True))
        extra_lines.append(f"skills {len(active)}/{total}")
    sched_line = (" · " + " · ".join(extra_lines)) if extra_lines else ""
    console.print(
        f"[dim]模型 {cfg.llm.model} · 工具 {', '.join(registry.names()) or '无'}{sched_line}[/dim]"
    )
    console.print("[dim]/exit 退出 · /good /bad /fix <修正> 反馈上一轮[/dim]\n")


if __name__ == "__main__":
    app()
