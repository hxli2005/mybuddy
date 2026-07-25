from pathlib import Path

import pytest

from mybuddy.cli import _process_is_running, _single_writer


def test_single_writer_rejects_second_instance(tmp_path: Path) -> None:
    data_dir = tmp_path / "agent-state"

    with _single_writer(data_dir):
        with pytest.raises(SystemExit, match="另一个 MyBuddy 实例"):
            with _single_writer(data_dir):
                pytest.fail("the second writer must not acquire the lock")


def test_invalid_process_id_is_not_running() -> None:
    assert _process_is_running(0) is False
    assert _process_is_running(-1) is False
