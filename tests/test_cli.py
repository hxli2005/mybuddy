import os

import pytest

from mybuddy.cli import _process_is_running, _single_writer


def test_data_directory_rejects_a_second_writer(tmp_path) -> None:  # noqa: ANN001
    with _single_writer(tmp_path):
        with pytest.raises(SystemExit, match="另一个 MyBuddy"):
            with _single_writer(tmp_path):
                pytest.fail("第二个写者不得进入")


def test_parent_process_liveness_is_observable() -> None:
    assert _process_is_running(os.getpid()) is True
    assert _process_is_running(2_000_000_000) is False
