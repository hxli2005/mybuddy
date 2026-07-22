import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_legacy_engine_and_body_paths_are_gone() -> None:
    removed = [
        *(
            ROOT / "mybuddy" / name
            for name in (
                "agent",
                "body",
                "emotion",
                "integrations",
                "learning",
                "memory",
                "scheduler",
                "storage",
                "tools",
            )
        ),
        *(ROOT / "mybuddy" / name for name in ("api.py", "web.py", "cli_admin.py")),
        *(
            ROOT / "buddyshell" / name
            for name in (
                "FoodTray.xaml",
                "FoodTray.xaml.cs",
                "Outbox.cs",
                "SpikeEvidence.cs",
            )
        ),
    ]
    assert [path.relative_to(ROOT).as_posix() for path in removed if path.exists()] == []


def test_body_has_one_wire_path_and_no_legacy_policy_fields() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in (ROOT / "mybuddy", ROOT / "buddyshell")
        for path in directory.rglob("*")
        if path.suffix in {".py", ".cs", ".xaml"}
        and "obj" not in path.parts
        and "bin" not in path.parts
    )
    assert "/api/body/step" in sources
    for legacy in (
        "/api/vpet",
        "PhysioInjection",
        "TouchEscalation",
        "FoodTray",
        "Outbox",
        "VPetEventRequest",
        "pending/drain",
        "day_index",
    ):
        assert legacy not in sources


def test_machine_side_stays_under_owner_limit() -> None:
    files = [
        path
        for directory in (ROOT / "mybuddy", ROOT / "buddyshell")
        for path in directory.rglob("*")
        if path.suffix in {".py", ".cs"} and "obj" not in path.parts and "bin" not in path.parts
    ]
    line_count = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in files)
    assert line_count <= 8000, line_count


def test_share_first_run_matches_deepseek_default() -> None:
    config = (ROOT / "distribution" / "config.default.yaml").read_text(encoding="utf-8")
    first_run = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in ("buddyshell/FirstRunWindow.xaml", "buddyshell/FirstRunWindow.xaml.cs")
    )

    assert "provider: deepseek" in config
    assert "model: deepseek-v4-flash" in config
    assert "base_url: https://api.deepseek.com" in config
    assert "DeepSeek API key" in first_run
    assert "OpenRouter" not in first_run


def test_share_package_has_one_versioned_auditable_candidate() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package = (ROOT / "mybuddy" / "__init__.py").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "build_share.ps1").read_text(encoding="utf-8")

    product_version = re.search(r'(?m)^version = "([^"]+)"$', pyproject)
    package_version = re.search(r'(?m)^__version__ = "([^"]+)"$', package)
    assert product_version is not None
    assert package_version is not None
    assert product_version.group(1) == package_version.group(1)

    for required in (
        '"LICENSE"',
        '"BUILD.txt"',
        "-p:Version=$productVersion",
        "-p:DebugSymbols=false",
        "-p:DebugType=None",
        "-Filter *.pdb",
        '"MyBuddy-$productVersion-win-x64.zip"',
        "Get-FileHash -LiteralPath $archive -Algorithm SHA256",
        '"$archive.sha256"',
        'Join-Path $outputRoot "previous"',
    ):
        assert required in script
    assert '"MyBuddy-win-x64.zip"' not in script
    assert "mybuddy\\reading.txt" in script
    assert "reading.local.txt" not in script


def test_private_reading_is_ignored_and_only_used_by_local_source_start() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    local_start = (ROOT / "scripts" / "start_mybuddy_web.ps1").read_text(encoding="utf-8")
    share_build = (ROOT / "scripts" / "build_share.ps1").read_text(encoding="utf-8")

    assert "/data/" in ignored
    assert 'ReadingFile = "data\\reading.local.txt"' in local_start
    assert '"--reading-file"' in local_start
    assert "reading.local.txt" not in share_build
    assert 'mybuddy\\reading.txt") -Destination (Join-Path $stage "小布读本.txt")' in share_build
