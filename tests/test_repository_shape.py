import json
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


def test_user_profile_is_an_on_demand_view_not_a_fifth_authority() -> None:
    bridge = (ROOT / "mybuddy" / "body_api.py").read_text(encoding="utf-8")
    files = (ROOT / "mybuddy" / "mind.py").read_text(encoding="utf-8")
    shell = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "buddyshell/Bridge/BridgeModels.cs",
            "buddyshell/MainWindow.xaml",
            "buddyshell/ProfileWindow.xaml",
        )
    )

    assert "include_user_profile" in bridge
    assert "read_user_profile" in files
    assert "她记得的你" in shell
    assert "查看原话来源" in shell
    assert "user_profile.json" not in "\n".join((bridge, files, shell))


def test_mentor_demo_uses_a_complete_clean_synthetic_memory() -> None:
    fixture = ROOT / "scripts" / "mentor_demo_fixture"
    marker = (fixture / "DEMO_DATA.md").read_text(encoding="utf-8")
    memories = json.loads((fixture / "memories.json").read_text(encoding="utf-8"))
    history = [
        json.loads(line)
        for line in (fixture / "history.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sources = {
        item["id"]: item["content"]
        for item in history
        if item.get("type") == "user_experience"
    }
    facts = [item for item in memories["items"] if item.get("kind") == "user_fact"]
    recorded = {
        item["memory_id"]
        for item in history
        if item.get("type") == "memory_operation" and item.get("action") == "record"
    }
    acknowledged = {
        evidence_id
        for item in history
        if item.get("type") == "shared_expression"
        for evidence_id in item.get("expression_evidence_ids", [])
    }

    assert "不是真实用户历史" in marker
    assert len(facts) == 3
    assert {item["id"] for item in facts} <= recorded
    assert all(sources[item["source_id"]] == item["quote"] for item in facts)
    assert all(item["source_id"] in acknowledged for item in facts)
    assert any("具体例子" in item["quote"] for item in facts)
    assert any("更小、更直接" in item["quote"] for item in facts)
    assert any("陌生文明" in item["quote"] for item in facts)
    assert {item["profile_dimension"] for item in facts} == {
        "communication_preference",
        "decision_preference",
        "content_interest",
    }
    assert all("老建筑" not in item["quote"] for item in facts)

    script = (ROOT / "scripts" / "mentor_demo.ps1").read_text(encoding="utf-8")
    web = (ROOT / "mybuddy" / "mentor_demo.html").read_text(encoding="utf-8")
    assert "mentor_demo_fixture" in script
    assert "mentor-demo-runs" in script
    assert "/mentor-demo" in script
    assert "?auto=1" in script
    assert "ValidateOnly" in script
    assert "data\\mini" not in script
    assert "profile_dimension" in web
    assert "散步时我喜欢看老建筑" in web
    assert "尚未形成" in web


def test_edge_life_is_read_only_and_uses_a_non_speech_cue() -> None:
    window = (ROOT / "buddyshell" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    xaml = (ROOT / "buddyshell" / "MainWindow.xaml").read_text(encoding="utf-8")

    assert 'response.Activity is { Type: "read" } edgeRead' in window
    assert 'Reason = "edge_cue_finished"' in window
    assert 'x:Name="EdgeLifeCue"' in xaml
    assert 'Text="读"' in xaml


def test_shell_immediately_discards_ambient_blocked_at_presentation_time() -> None:
    window = (ROOT / "buddyshell" / "MainWindow.xaml.cs").read_text(encoding="utf-8")

    assert "Presence.MustDiscardAmbient(response.Expression, presence)" in window
    assert "await DiscardAmbientAsync(blockedPresence)" in window
    assert "Presence = presence" in window


def test_shell_body_step_has_one_model_length_timeout() -> None:
    bridge = (ROOT / "buddyshell" / "Bridge" / "BridgeClient.cs").read_text(encoding="utf-8")

    timeout = re.search(r"RequestTimeout = TimeSpan\.FromSeconds\((\d+)\)", bridge)
    assert timeout is not None
    assert int(timeout.group(1)) >= 120
    assert "Timeout = Timeout.InfiniteTimeSpan" in bridge
    assert bridge.count("CancelAfter(") == 1
    assert "CancelAfter(RequestTimeout)" in bridge


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
