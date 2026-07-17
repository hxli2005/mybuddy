"""MyBuddy 管理子命令:查看/编辑用户画像、skills。

所有命令都只读本地数据或做 CRUD 写回,不走 LLM,因此启动开销低、不需要 api_key。

  mybuddy profile show / set <k> <v> / unset <k>
  mybuddy skills list [--all] / show <name> / archive <name> / unarchive <name>
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mybuddy.config import load_config
from mybuddy.learning import SkillRegistry
from mybuddy.memory import UserProfile
from mybuddy.storage import init_db

console = Console()

profile_app = typer.Typer(help="查看/编辑用户画像")
skills_app = typer.Typer(help="查看/归档 skill")


# ---------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------

def _load_profile(config_path: str) -> tuple[UserProfile, object]:
    """profile show/set/unset 只涉及 SQLite 核心字段,不初始化长期档案层。"""
    cfg = load_config(config_path)
    engine = init_db(cfg.paths.db_file)
    return UserProfile(engine, None), engine


@profile_app.command("show")
def profile_show(
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """打印核心字段。"""
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
    """把两组子命令挂到主 app 下。"""
    app.add_typer(profile_app, name="profile")
    app.add_typer(skills_app, name="skills")


__all__ = [
    "register",
    "profile_app",
    "skills_app",
]
