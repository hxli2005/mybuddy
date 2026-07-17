"""pending_messages 队列测试。"""

from __future__ import annotations

from datetime import timedelta

from mybuddy._time import utcnow
from mybuddy.storage import drain_pending, enqueue, init_db, list_undelivered


def test_enqueue_and_drain(tmp_path) -> None:
    engine = init_db(str(tmp_path / "q.db"))
    a = enqueue(engine, source="body_murmur", content="有点困", meta={"kind": "energy"})
    b = enqueue(engine, source="nudge", content="你好吗")
    assert a != b

    pending = list_undelivered(engine)
    assert len(pending) == 2

    drained = drain_pending(engine)
    assert len(drained) == 2
    # 按 scheduled_at 升序
    assert drained[0]["content"] == "有点困"
    assert drained[0]["meta"]["kind"] == "energy"
    assert drained[1]["source"] == "nudge"

    # 再 drain 为空(已标记 delivered)
    assert drain_pending(engine) == []


def test_drain_respects_future_scheduled_at(tmp_path) -> None:
    """scheduled_at 在未来的消息不应被 drain。"""
    engine = init_db(str(tmp_path / "q.db"))
    future = utcnow() + timedelta(hours=1)
    enqueue(engine, source="greeting", content="未来问候", scheduled_at=future)
    enqueue(engine, source="nudge", content="现在关心一句")

    drained = drain_pending(engine)
    assert len(drained) == 1
    assert drained[0]["content"] == "现在关心一句"

    remaining = list_undelivered(engine)
    assert len(remaining) == 1
    assert remaining[0]["content"] == "未来问候"
