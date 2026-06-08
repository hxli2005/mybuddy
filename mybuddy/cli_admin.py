"""MyBuddy 管理子命令:查看/编辑用户画像、提醒、skills。

所有命令都只读本地数据或做 CRUD 写回,不走 LLM,因此启动开销低、不需要 api_key。

  mybuddy profile show / set <k> <v> / unset <k>
  mybuddy reminders list [--all] / cancel <id>
  mybuddy skills list [--all] / show <name> / archive <name> / unarchive <name>
"""

from __future__ import annotations

from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from mybuddy.config import load_config
from mybuddy.learning import SkillRegistry
from mybuddy.memory import UserProfile
from mybuddy.storage import (
    Reminder,
    bind_external_account,
    create_user,
    init_db,
    list_user_summaries,
    session_scope,
    set_user_daily_limit,
    set_user_status,
)

console = Console()

profile_app = typer.Typer(help="查看/编辑用户画像")
reminders_app = typer.Typer(help="查看/取消提醒")
skills_app = typer.Typer(help="查看/归档 skill")
users_app = typer.Typer(help="测试用户与外部账号管理")


# ---------------------------------------------------------------------
# users
# ---------------------------------------------------------------------


@users_app.command("list")
def users_list(
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """列出测试用户和外部账号绑定。"""
    cfg = load_config(config_path)
    engine = init_db(cfg.paths.db_file)
    snapshot = [
        (
            item.user.id,
            item.user.display_name,
            item.user.status,
            item.user.daily_message_limit,
            ", ".join(f"{account.provider}:{account.external_id}" for account in item.external_accounts),
            ", ".join(f"{source}:{count}" for source, count in sorted(item.usage_today.items())),
        )
        for item in list_user_summaries(engine)
    ]
    if not snapshot:
        console.print("[dim]还没有测试用户。[/dim]")
        return
    t = Table(title="测试用户")
    t.add_column("id", justify="right")
    t.add_column("name")
    t.add_column("status")
    t.add_column("daily")
    t.add_column("external")
    t.add_column("today")
    for user_id, name, status, limit, external, usage in snapshot:
        t.add_row(str(user_id), name, status, str(limit), external, usage)
    console.print(t)


@users_app.command("create")
def users_create(
    display_name: str = typer.Argument("", help="显示名"),
    daily_message_limit: int = typer.Option(30, "--daily", help="每日消息额度"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """创建一个测试用户。"""
    cfg = load_config(config_path)
    engine = init_db(cfg.paths.db_file)
    user = create_user(engine, display_name=display_name, daily_message_limit=daily_message_limit)
    console.print(
        f"[green]已创建用户 #{user.id}[/green] "
        f"name={user.display_name or '-'} daily={user.daily_message_limit}"
    )


@users_app.command("bind-qq")
def users_bind_qq(
    user_id: int,
    qq_id: str,
    display_name: str = typer.Option("", "--name", help="QQ 侧显示名"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """把 QQ external_id 绑定到内部用户。"""
    cfg = load_config(config_path)
    engine = init_db(cfg.paths.db_file)
    try:
        bind_external_account(
            engine,
            user_id=user_id,
            provider="qq",
            external_id=qq_id,
            display_name=display_name,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    console.print(f"[green]已绑定用户 #{user_id} -> qq:{qq_id}[/green]")


@users_app.command("enable")
def users_enable(
    user_id: int,
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """启用用户。"""
    cfg = load_config(config_path)
    engine = init_db(cfg.paths.db_file)
    if set_user_status(engine, user_id, "active") is None:
        console.print(f"[yellow]用户不存在:id={user_id}[/yellow]")
        return
    console.print(f"[green]已启用用户 #{user_id}[/green]")


@users_app.command("disable")
def users_disable(
    user_id: int,
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """禁用用户。"""
    cfg = load_config(config_path)
    engine = init_db(cfg.paths.db_file)
    if set_user_status(engine, user_id, "disabled") is None:
        console.print(f"[yellow]用户不存在:id={user_id}[/yellow]")
        return
    console.print(f"[green]已禁用用户 #{user_id}[/green]")


@users_app.command("quota")
def users_quota(
    user_id: int,
    daily_message_limit: int = typer.Option(..., "--daily", help="每日消息额度"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """设置用户每日消息额度。"""
    cfg = load_config(config_path)
    engine = init_db(cfg.paths.db_file)
    user = set_user_daily_limit(engine, user_id, daily_message_limit)
    if user is None:
        console.print(f"[yellow]用户不存在:id={user_id}[/yellow]")
        return
    console.print(f"[green]已设置用户 #{user_id} daily={user.daily_message_limit}[/green]")


# ---------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------

def _load_profile(config_path: str) -> tuple[UserProfile, object]:
    """profile show/set/unset 只涉及 SQLite,不初始化长期档案层。

    管理命令只需要看/改核心字段和命题表,search_claims 走 SQL 降级扫描已足够。
    """
    cfg = load_config(config_path)
    engine = init_db(cfg.paths.db_file)
    return UserProfile(engine, None), engine


@profile_app.command("show")
def profile_show(
    config_path: str = typer.Option("config.yaml", "--config"),
    top_claims: int = typer.Option(10, "--top-claims", help="展示前 N 条命题"),
) -> None:
    """打印核心字段 + 高置信度命题。"""
    profile, _ = _load_profile(config_path)

    fields = profile.get_all_fields()
    if fields:
        ft = Table(title="核心字段", show_header=True)
        ft.add_column("key")
        ft.add_column("value")
        for k, v in fields.items():
            ft.add_row(k, v)
        console.print(ft)
    else:
        console.print("[dim]还没有核心字段。[/dim]")

    claims = profile.get_all_claims(min_confidence=0.3)[:top_claims]
    if claims:
        ct = Table(title=f"动态命题(top-{top_claims}, confidence >= 0.3)")
        ct.add_column("id", justify="right")
        ct.add_column("claim")
        ct.add_column("conf", justify="right")
        for c in claims:
            ct.add_row(str(c["sql_id"]), c["claim"], f"{c['confidence']:.2f}")
        console.print(ct)
    else:
        console.print("[dim]还没有动态命题。[/dim]")


@profile_app.command("set")
def profile_set(
    key: str,
    value: str,
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """写入/更新一个核心字段。"""
    profile, _ = _load_profile(config_path)
    profile.set_field(key, value)
    console.print(f"[green]已设置 {key}={value}[/green]")


@profile_app.command("unset")
def profile_unset(
    key: str,
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """删除一个核心字段。"""
    profile, _ = _load_profile(config_path)
    if profile.delete_field(key):
        console.print(f"[green]已删除 {key}[/green]")
    else:
        console.print(f"[yellow]字段不存在:{key}[/yellow]")


# ---------------------------------------------------------------------
# reminders
# ---------------------------------------------------------------------


@reminders_app.command("list")
def reminders_list(
    show_all: bool = typer.Option(False, "--all", help="包含已触发/已取消"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """列出提醒。"""
    cfg = load_config(config_path)
    engine = init_db(cfg.paths.db_file)

    with session_scope(engine) as s:
        q = s.query(Reminder).order_by(Reminder.trigger_at.asc())
        if not show_all:
            q = q.filter(Reminder.status == "pending")
        rows = q.all()
        snapshot = [
            (r.id, r.trigger_at, r.status, r.content)
            for r in rows
        ]

    if not snapshot:
        console.print("[dim]没有提醒。[/dim]")
        return

    t = Table(title="提醒")
    t.add_column("id", justify="right")
    t.add_column("触发时间")
    t.add_column("状态")
    t.add_column("内容")
    for rid, trigger_at, status, content in snapshot:
        t.add_row(
            str(rid),
            trigger_at.strftime("%Y-%m-%d %H:%M") if isinstance(trigger_at, datetime) else str(trigger_at),
            status,
            content,
        )
    console.print(t)


@reminders_app.command("cancel")
def reminders_cancel(
    reminder_id: int,
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """把一条 pending 提醒改为 cancelled。"""
    cfg = load_config(config_path)
    engine = init_db(cfg.paths.db_file)

    with session_scope(engine) as s:
        row = s.query(Reminder).filter(Reminder.id == reminder_id).one_or_none()
        if row is None:
            console.print(f"[yellow]提醒不存在:id={reminder_id}[/yellow]")
            return
        if row.status != "pending":
            console.print(f"[yellow]状态非 pending,无需取消:{row.status}[/yellow]")
            return
        row.status = "cancelled"

    console.print(f"[green]已取消提醒 #{reminder_id}[/green]")


# ---------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------


def _load_skills(config_path: str) -> SkillRegistry:
    cfg = load_config(config_path)
    return SkillRegistry.load_all(cfg.paths.skills_dir)


@skills_app.command("list")
def skills_list(
    show_all: bool = typer.Option(False, "--all", help="包含已归档"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """列出 skill。"""
    reg = _load_skills(config_path)
    skills = reg.all(include_archived=show_all)
    if not skills:
        console.print("[dim]目前没有 skill。[/dim]")
        return

    t = Table(title=f"Skills ({'全部' if show_all else '活跃'})")
    t.add_column("name")
    t.add_column("triggers")
    t.add_column("成功", justify="right")
    t.add_column("失败", justify="right")
    t.add_column("conf", justify="right")
    t.add_column("archived")
    for s in sorted(skills, key=lambda x: x.confidence, reverse=True):
        t.add_row(
            s.name,
            ", ".join(s.triggers),
            str(s.success_count),
            str(s.fail_count),
            f"{s.confidence:.2f}",
            "是" if s.archived else "",
        )
    console.print(t)


@skills_app.command("show")
def skills_show(
    name: str,
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """打印一个 skill 的完整内容。"""
    reg = _load_skills(config_path)
    s = reg.get(name)
    if s is None:
        console.print(f"[yellow]skill 不存在:{name}[/yellow]")
        return

    console.print(f"[bold]{s.name}[/bold]  [dim]{s.file_path}[/dim]")
    console.print(f"  triggers: {', '.join(s.triggers)}")
    console.print(
        f"  counts: success={s.success_count}  fail={s.fail_count}  "
        f"confidence={s.confidence:.2f}  archived={s.archived}"
    )
    console.print("  steps:")
    for i, step in enumerate(s.steps, 1):
        console.print(f"    {i}. {step}")


@skills_app.command("archive")
def skills_archive(
    name: str,
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """手工归档一个 skill。"""
    reg = _load_skills(config_path)
    s = reg.get(name)
    if s is None:
        console.print(f"[yellow]skill 不存在:{name}[/yellow]")
        return
    s.archived = True
    reg.save(s)
    console.print(f"[green]已归档 {name}[/green]")


@skills_app.command("unarchive")
def skills_unarchive(
    name: str,
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """恢复一个已归档的 skill。"""
    reg = _load_skills(config_path)
    s = reg.get(name)
    if s is None:
        console.print(f"[yellow]skill 不存在:{name}[/yellow]")
        return
    s.archived = False
    reg.save(s)
    console.print(f"[green]已恢复 {name}[/green]")


# ---------------------------------------------------------------------
# 给 cli.py 注册用的打包入口
# ---------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """把三组子命令挂到主 app 下。"""
    app.add_typer(users_app, name="users")
    app.add_typer(profile_app, name="profile")
    app.add_typer(reminders_app, name="reminders")
    app.add_typer(skills_app, name="skills")


__all__ = [
    "register",
    "profile_app",
    "reminders_app",
    "skills_app",
    "users_app",
]
