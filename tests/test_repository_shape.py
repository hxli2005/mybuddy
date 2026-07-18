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
    assert line_count <= 5000, line_count
