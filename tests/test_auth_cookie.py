"""会话 cookie 生成/解析的往返测试。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from mybuddy._time import utcnow
from mybuddy.auth import manager
from mybuddy.auth.manager import (
    AuthManager,
    COOKIE_NAME,
    _make_cookie_value,
    get_user_id_from_cookie,
)


@pytest.fixture(autouse=True)
def _isolated_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "SESSION_SECRET_FILE", str(tmp_path / ".session_secret"))


def test_cookie_roundtrip() -> None:
    header = AuthManager.make_cookie(42)
    cookie_pair = header.split(";", 1)[0]
    assert cookie_pair.startswith(f"{COOKIE_NAME}=")
    assert get_user_id_from_cookie(cookie_pair) == 42


def test_cookie_value_is_rfc6265_safe() -> None:
    value = _make_cookie_value(7)
    # RFC 6265 禁止的字符会导致浏览器丢弃或改写 cookie
    for ch in '"\\ ,;{}':
        assert ch not in value, f"cookie 值含非法字符: {ch!r}"


def test_cookie_header_has_name_prefix_and_attributes() -> None:
    header = AuthManager.make_cookie(1)
    assert header.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in header
    assert "Path=/" in header


def test_tampered_signature_rejected() -> None:
    value = _make_cookie_value(1)
    payload, sig = value.rsplit(".", 1)
    tampered = f"{payload}.{'A' * len(sig)}"
    assert get_user_id_from_cookie(f"{COOKIE_NAME}={tampered}") is None


def test_expired_cookie_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        manager, "utcnow", lambda: utcnow() - manager.COOKIE_MAX_AGE - timedelta(days=1)
    )
    value = _make_cookie_value(1)
    monkeypatch.undo()
    assert get_user_id_from_cookie(f"{COOKIE_NAME}={value}") is None


def test_missing_or_garbage_cookie_rejected() -> None:
    assert get_user_id_from_cookie(None) is None
    assert get_user_id_from_cookie("") is None
    assert get_user_id_from_cookie("other=1; foo=bar") is None
    assert get_user_id_from_cookie(f"{COOKIE_NAME}=not-base64.badsig") is None
