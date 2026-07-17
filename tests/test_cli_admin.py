"""CLI admin 子命令测试(profile / skills)。

不触发 LLM,只验证 typer 子命令走通 SQLite 和 skill 文件落盘/更新。
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mybuddy.cli_admin import profile_app, skills_app
from mybuddy.learning import SkillRegistry
from mybuddy.storage import init_db

runner = CliRunner()


# =============================================================================
# 公用 fixture:准备一份带 db 的 config.yaml + 切换 cwd
# =============================================================================


@pytest.fixture
def admin_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    db_file = tmp_path / "a.db"
    chroma_dir = tmp_path / "chroma"
    skills_dir = tmp_path / "skills"
    chroma_dir.mkdir()
    skills_dir.mkdir()
    trajectories_dir = tmp_path / "traj"

    cfg_path.write_text(
        f"""
llm:
  provider: anthropic
  api_key: dummy
memory:
  embedding_model: "BAAI/bge-m3"
paths:
  data_dir: "{tmp_path.as_posix()}"
  db_file: "{db_file.as_posix()}"
  chroma_dir: "{chroma_dir.as_posix()}"
  skills_dir: "{skills_dir.as_posix()}"
  trajectories_dir: "{trajectories_dir.as_posix()}"
""",
        encoding="utf-8",
    )

    engine = init_db(str(db_file))
    return {
        "cfg_path": str(cfg_path),
        "engine": engine,
        "skills_dir": skills_dir,
    }


# =============================================================================
# profile
# =============================================================================


def test_profile_set_show_unset(admin_env) -> None:
    cfg = admin_env["cfg_path"]

    r1 = runner.invoke(profile_app, ["set", "名字", "小明", "--config", cfg])
    assert r1.exit_code == 0, r1.stdout
    assert "已设置" in r1.stdout

    r2 = runner.invoke(profile_app, ["show", "--config", cfg])
    assert r2.exit_code == 0, r2.stdout
    assert "小明" in r2.stdout

    r3 = runner.invoke(profile_app, ["unset", "名字", "--config", cfg])
    assert r3.exit_code == 0
    assert "已删除" in r3.stdout

    r4 = runner.invoke(profile_app, ["unset", "不存在", "--config", cfg])
    assert r4.exit_code == 0
    assert "不存在" in r4.stdout


# =============================================================================
# skills
# =============================================================================


def test_skills_list_show_archive_unarchive(admin_env) -> None:
    cfg = admin_env["cfg_path"]
    skills_dir = admin_env["skills_dir"]

    reg = SkillRegistry(skills_dir)
    reg.create(name="早安问候", triggers=["早上好"], steps=["温柔回应"], confidence=0.7)
    reg.create(name="低分技能", triggers=["x"], steps=["y"], confidence=0.3)

    # list 默认只显示 active
    r = runner.invoke(skills_app, ["list", "--config", cfg])
    assert r.exit_code == 0, r.stdout
    assert "早安问候" in r.stdout
    assert "低分技能" in r.stdout  # 未归档

    # show
    r_show = runner.invoke(skills_app, ["show", "早安问候", "--config", cfg])
    assert r_show.exit_code == 0
    assert "温柔回应" in r_show.stdout

    r_miss = runner.invoke(skills_app, ["show", "不存在", "--config", cfg])
    assert r_miss.exit_code == 0
    assert "不存在" in r_miss.stdout

    # archive / unarchive
    r_arch = runner.invoke(skills_app, ["archive", "早安问候", "--config", cfg])
    assert r_arch.exit_code == 0
    assert "已归档" in r_arch.stdout

    reg2 = SkillRegistry.load_all(skills_dir)
    assert reg2.get("早安问候").archived is True

    # 默认 list 不显示归档的
    r_list = runner.invoke(skills_app, ["list", "--config", cfg])
    assert "早安问候" not in r_list.stdout
    assert "低分技能" in r_list.stdout

    r_listall = runner.invoke(skills_app, ["list", "--all", "--config", cfg])
    assert "早安问候" in r_listall.stdout

    r_un = runner.invoke(skills_app, ["unarchive", "早安问候", "--config", cfg])
    assert r_un.exit_code == 0
    reg3 = SkillRegistry.load_all(skills_dir)
    assert reg3.get("早安问候").archived is False
