from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from mybuddy._time import utcnow
from mybuddy.storage import (
    begin_inbound_event,
    bind_external_account,
    create_user,
    finish_inbound_event,
    get_or_create_external_user,
    init_db,
    resolve_external_account,
    session_scope,
)
from mybuddy.storage.models import InboundEvent


def _engine(tmp_path: Path):
    return init_db(str(tmp_path / "users.db"))


def test_bind_external_account_rejects_reassigning_to_other_user(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    a = create_user(engine, display_name="A")
    b = create_user(engine, display_name="B")
    bind_external_account(engine, user_id=a.id, provider="qq", external_id="qq-1")

    # 改绑到别的用户必须报错,不能静默把账号从 A 偷给 B。
    with pytest.raises(ValueError):
        bind_external_account(engine, user_id=b.id, provider="qq", external_id="qq-1")

    still = resolve_external_account(engine, provider="qq", external_id="qq-1")
    assert still is not None and still.id == a.id


def test_bind_external_account_same_user_is_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    a = create_user(engine, display_name="A")
    bind_external_account(engine, user_id=a.id, provider="qq", external_id="qq-1")
    # 同一用户重复绑定仅更新显示名,不报错。
    bind_external_account(engine, user_id=a.id, provider="qq", external_id="qq-1", display_name="新名字")
    resolved = resolve_external_account(engine, provider="qq", external_id="qq-1")
    assert resolved is not None and resolved.id == a.id


def test_get_or_create_leaves_no_orphan_and_is_stable(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    first = get_or_create_external_user(engine, provider="qq", external_id="qq-x", display_name="X")
    second = get_or_create_external_user(engine, provider="qq", external_id="qq-x", display_name="X")
    assert first is not None and second is not None
    assert first.id == second.id
    # 每个外部账号只对应一个用户,没有重复/孤儿用户。
    with session_scope(engine) as s:
        from mybuddy.storage.models import ExternalAccount, User

        assert s.query(User).count() == 1
        assert s.query(ExternalAccount).count() == 1


def test_begin_inbound_event_dedupes_processed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert begin_inbound_event(engine, provider="qq", event_id="e1") is True
    finish_inbound_event(engine, provider="qq", event_id="e1", status="processed")
    # 已成功处理的事件重投应被去重。
    assert begin_inbound_event(engine, provider="qq", event_id="e1") is False


def test_begin_inbound_event_reclaims_rejected_and_error(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    # rejected(如未加白名单)后又被加入名单,重投应能重新处理。
    begin_inbound_event(engine, provider="qq", event_id="r1")
    finish_inbound_event(engine, provider="qq", event_id="r1", status="rejected")
    assert begin_inbound_event(engine, provider="qq", event_id="r1") is True

    # error(处理中途报错)后重投应能重试。
    finish_inbound_event(engine, provider="qq", event_id="r1", status="error")
    assert begin_inbound_event(engine, provider="qq", event_id="r1") is True


def test_begin_inbound_event_blocks_active_processing_but_reclaims_stale(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert begin_inbound_event(engine, provider="qq", event_id="p1") is True
    # 正在处理中(processing 且未超时)的重投应被丢弃,避免并发重复处理。
    assert begin_inbound_event(engine, provider="qq", event_id="p1") is False

    # 把 processing 行的开始时间改到很久以前,模拟处理流程崩溃留下的卡死行 -> 允许重试。
    with session_scope(engine) as s:
        row = s.query(InboundEvent).filter(InboundEvent.event_id == "p1").one()
        row.created_at = utcnow() - timedelta(seconds=10_000)
    assert begin_inbound_event(engine, provider="qq", event_id="p1") is True
