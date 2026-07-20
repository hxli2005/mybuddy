"""认证管理器:注册、登录、Cookie 会话管理。"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timedelta

import bcrypt
from sqlalchemy import Engine

from mybuddy._time import utcnow
from mybuddy.storage.db import session_scope
from mybuddy.storage.models import User

SESSION_SECRET_FILE = "data/.session_secret"
COOKIE_NAME = "mybuddy_session"
COOKIE_MAX_AGE = timedelta(days=30)


def _get_or_create_secret() -> bytes:
    """获取或生成会话签名密钥。"""
    secret_path = SESSION_SECRET_FILE
    if os.path.exists(secret_path):
        with open(secret_path, "rb") as f:
            return f.read()
    secret = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(secret_path), exist_ok=True)
    with open(secret_path, "wb") as f:
        f.write(secret)
    return secret


def _sign(data: str) -> str:
    """HMAC-SHA256 签名 + Base64 编码。"""
    secret = _get_or_create_secret()
    sig = hmac.new(secret, data.encode(), hashlib.sha256).digest()
    return urlsafe_b64encode(sig).rstrip(b"=").decode()


def _verify(data: str, signature: str) -> bool:
    """验证签名。"""
    return hmac.compare_digest(_sign(data), signature)


def _make_cookie_value(user_id: int) -> str:
    """生成 cookie 值:base64url 编码的 user_id + 过期时间 + 签名。"""
    expires = (utcnow() + COOKIE_MAX_AGE).isoformat()
    payload = json.dumps({"user_id": user_id, "expires": expires}, sort_keys=True)
    payload_b64 = urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    sig = _sign(payload_b64)
    return f"{payload_b64}.{sig}"


def _parse_cookie_value(value: str) -> dict | None:
    """解析并验证 cookie 值。"""
    parts = value.rsplit(".", 1)
    if len(parts) != 2:
        return None
    payload_b64, sig = parts
    if not _verify(payload_b64, sig):
        return None
    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    try:
        expires = datetime.fromisoformat(payload["expires"])
    except (ValueError, KeyError):
        return None
    if utcnow() > expires:
        return None
    return payload


def get_user_id_from_cookie(cookie_header: str | None) -> int | None:
    """从 Cookie header 中提取已验证的 user_id。"""
    if not cookie_header:
        return None
    cookies = {}
    for item in cookie_header.split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        cookies[k.strip()] = v.strip()
    value = cookies.get(COOKIE_NAME)
    if not value:
        return None
    payload = _parse_cookie_value(value)
    if not payload:
        return None
    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        return None
    return user_id


class AuthManager:
    """认证管理器:注册、登录、登出。"""

    def __init__(self, engine: Engine):
        self._engine = engine

    def register(self, username: str, password: str) -> dict:
        """注册新用户。返回 user_id 和 cookie。"""
        clean_name = username.strip()
        if len(clean_name) < 2 or len(clean_name) > 20:
            raise ValueError("用户名需要 2-20 个字符")
        if len(password) < 4:
            raise ValueError("密码至少需要 4 个字符")

        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        with session_scope(self._engine) as s:
            existing = s.query(User).filter(User.display_name == clean_name).first()
            if existing is not None:
                raise ValueError("用户名已被使用")

            user = User(
                display_name=clean_name,
                password_hash=password_hash,
                status="active",
            )
            s.add(user)
            s.flush()
            cookie = _make_cookie_value(user.id)
            return {"user_id": user.id, "username": clean_name, "cookie": cookie}

    def login(self, username: str, password: str) -> dict:
        """登录。验证密码后返回 cookie。"""
        clean_name = username.strip()

        with session_scope(self._engine) as s:
            user = s.query(User).filter(User.display_name == clean_name).first()
            if user is None:
                raise ValueError("用户名或密码错误")
            if user.status == "disabled":
                raise ValueError("账户已被禁用")
            if not user.password_hash:
                raise ValueError("该账户未设置密码，请重新注册")

            if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
                raise ValueError("用户名或密码错误")

            cookie = _make_cookie_value(user.id)
            return {"user_id": user.id, "username": user.display_name, "cookie": cookie}

    @staticmethod
    def make_cookie(user_id: int) -> str:
        """生成登录 cookie 字符串。"""
        return f"{COOKIE_NAME}={_make_cookie_value(user_id)}; Path=/; HttpOnly; SameSite=Lax; Max-Age={int(COOKIE_MAX_AGE.total_seconds())}"

    @staticmethod
    def clear_cookie() -> str:
        """生成清除 cookie 的 Set-Cookie 头。"""
        return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"

    def get_user(self, user_id: int) -> dict | None:
        """获取用户信息。"""
        with session_scope(self._engine) as s:
            user = s.get(User, user_id)
            if user is None or user.status == "disabled":
                return None
            return {"user_id": user.id, "username": user.display_name}
