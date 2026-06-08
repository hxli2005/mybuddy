"""用户、外部账号、入站事件与额度存储辅助。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from mybuddy._time import utcnow
from mybuddy.config import PersonaConfig

from .db import session_scope
from .models import ExternalAccount, InboundEvent, User, UserPersona, UserUsage

DEFAULT_LOCAL_EXTERNAL_ID = "local"

# processing 状态的入站事件超过该时长仍未收尾,视为处理流程中途崩溃,允许重投重试。
INBOUND_EVENT_STALE_SECONDS = 300


@dataclass(frozen=True)
class UserRecord:
    id: int
    display_name: str
    status: str
    daily_message_limit: int

    @property
    def is_active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class ExternalAccountRecord:
    provider: str
    external_id: str
    display_name: str


@dataclass(frozen=True)
class UserSummaryRecord:
    user: UserRecord
    external_accounts: tuple[ExternalAccountRecord, ...]
    usage_today: dict[str, int]
    has_custom_persona: bool = False


@dataclass(frozen=True)
class UserPersonaRecord:
    user_id: int
    persona: PersonaConfig
    updated_at: datetime

    @property
    def version(self) -> str:
        return self.updated_at.isoformat()


@dataclass(frozen=True)
class ResolvedPersonaRecord:
    persona: PersonaConfig
    version: str
    inherits_default: bool


def _user_record(row: User) -> UserRecord:
    return UserRecord(
        id=row.id,
        display_name=row.display_name,
        status=row.status,
        daily_message_limit=row.daily_message_limit,
    )


def _external_account_record(row: ExternalAccount) -> ExternalAccountRecord:
    return ExternalAccountRecord(
        provider=row.provider,
        external_id=row.external_id,
        display_name=row.display_name,
    )


def list_user_summaries(engine: Engine) -> list[UserSummaryRecord]:
    """列出测试用户、外部账号绑定和今天各渠道用量。"""
    day = utcnow().date().isoformat()
    with session_scope(engine) as s:
        users = s.query(User).order_by(User.id.asc()).all()
        user_ids = [row.id for row in users]
        if not user_ids:
            return []

        account_rows = (
            s.query(ExternalAccount)
            .filter(ExternalAccount.user_id.in_(user_ids))
            .order_by(ExternalAccount.provider.asc(), ExternalAccount.external_id.asc())
            .all()
        )
        usage_rows = (
            s.query(UserUsage)
            .filter(UserUsage.user_id.in_(user_ids))
            .filter(UserUsage.day == day)
            .all()
        )
        persona_rows = s.query(UserPersona.user_id).filter(UserPersona.user_id.in_(user_ids)).all()
        custom_persona_user_ids = {int(row[0]) for row in persona_rows}

        accounts: dict[int, list[ExternalAccountRecord]] = {user_id: [] for user_id in user_ids}
        usage: dict[int, dict[str, int]] = {user_id: {} for user_id in user_ids}
        for row in account_rows:
            accounts.setdefault(row.user_id, []).append(_external_account_record(row))
        for row in usage_rows:
            usage.setdefault(row.user_id, {})[row.source] = int(row.message_count)

        return [
            UserSummaryRecord(
                user=_user_record(row),
                external_accounts=tuple(accounts.get(row.id, ())),
                usage_today=usage.get(row.id, {}),
                has_custom_persona=row.id in custom_persona_user_ids,
            )
            for row in users
        ]


def create_user(
    engine: Engine,
    *,
    display_name: str = "",
    daily_message_limit: int = 30,
    status: str = "active",
) -> UserRecord:
    clean_name = display_name.strip()
    clean_status = status.strip() or "active"
    limit = max(0, int(daily_message_limit))
    with session_scope(engine) as s:
        row = User(
            display_name=clean_name,
            status=clean_status,
            daily_message_limit=limit,
        )
        s.add(row)
        s.flush()
        return _user_record(row)


def get_user(engine: Engine, user_id: int) -> UserRecord | None:
    with session_scope(engine) as s:
        row = s.get(User, user_id)
        return _user_record(row) if row is not None else None


def set_user_status(engine: Engine, user_id: int, status: str) -> UserRecord | None:
    clean = status.strip() or "active"
    with session_scope(engine) as s:
        row = s.get(User, user_id)
        if row is None:
            return None
        row.status = clean
        s.flush()
        return _user_record(row)


def set_user_daily_limit(engine: Engine, user_id: int, daily_message_limit: int) -> UserRecord | None:
    limit = max(0, int(daily_message_limit))
    with session_scope(engine) as s:
        row = s.get(User, user_id)
        if row is None:
            return None
        row.daily_message_limit = limit
        s.flush()
        return _user_record(row)


def get_user_persona(engine: Engine, user_id: int) -> UserPersonaRecord | None:
    with session_scope(engine) as s:
        row = s.query(UserPersona).filter(UserPersona.user_id == user_id).one_or_none()
        return _user_persona_record(row) if row is not None else None


def resolve_user_persona(
    engine: Engine,
    *,
    user_id: int,
    default_persona: PersonaConfig,
) -> ResolvedPersonaRecord:
    record = get_user_persona(engine, user_id)
    if record is None:
        return ResolvedPersonaRecord(
            persona=default_persona.model_copy(deep=True),
            version="default",
            inherits_default=True,
        )
    return ResolvedPersonaRecord(
        persona=record.persona,
        version=record.version,
        inherits_default=False,
    )


def set_user_persona(
    engine: Engine,
    *,
    user_id: int,
    persona: PersonaConfig,
) -> UserPersonaRecord:
    with session_scope(engine) as s:
        user = s.get(User, user_id)
        if user is None:
            raise ValueError(f"user not found: {user_id}")
        row = s.query(UserPersona).filter(UserPersona.user_id == user_id).one_or_none()
        now = utcnow()
        persona_json = json.dumps(persona.model_dump(), ensure_ascii=False, sort_keys=True)
        if row is None:
            row = UserPersona(
                user_id=user_id,
                persona_json=persona_json,
                created_at=now,
                updated_at=now,
            )
            s.add(row)
        else:
            row.persona_json = persona_json
            row.updated_at = now
        s.flush()
        return _user_persona_record(row)


def delete_user_persona(engine: Engine, user_id: int) -> bool:
    with session_scope(engine) as s:
        row = s.query(UserPersona).filter(UserPersona.user_id == user_id).one_or_none()
        if row is None:
            return False
        s.delete(row)
        return True


def bind_external_account(
    engine: Engine,
    *,
    user_id: int,
    provider: str,
    external_id: str,
    display_name: str = "",
) -> UserRecord:
    clean_provider = _clean_provider(provider)
    clean_external_id = _clean_external_id(external_id)
    clean_name = display_name.strip()
    with session_scope(engine) as s:
        user = s.get(User, user_id)
        if user is None:
            raise ValueError(f"user not found: {user_id}")
        row = (
            s.query(ExternalAccount)
            .filter(ExternalAccount.provider == clean_provider)
            .filter(ExternalAccount.external_id == clean_external_id)
            .one_or_none()
        )
        if row is None:
            row = ExternalAccount(
                user_id=user_id,
                provider=clean_provider,
                external_id=clean_external_id,
                display_name=clean_name,
            )
            s.add(row)
        else:
            if row.user_id != user_id:
                raise ValueError(
                    f"{clean_provider}:{clean_external_id} 已绑定到用户 #{row.user_id},"
                    f"不能直接改绑到用户 #{user_id};请先解绑。"
                )
            if clean_name:
                row.display_name = clean_name
        s.flush()
        return _user_record(user)


def resolve_external_account(
    engine: Engine,
    *,
    provider: str,
    external_id: str,
) -> UserRecord | None:
    clean_provider = _clean_provider(provider)
    clean_external_id = _clean_external_id(external_id)
    with session_scope(engine) as s:
        row = (
            s.query(ExternalAccount)
            .filter(ExternalAccount.provider == clean_provider)
            .filter(ExternalAccount.external_id == clean_external_id)
            .one_or_none()
        )
        if row is None:
            return None
        user = s.get(User, row.user_id)
        return _user_record(user) if user is not None else None


def get_or_create_external_user(
    engine: Engine,
    *,
    provider: str,
    external_id: str,
    display_name: str = "",
    daily_message_limit: int = 30,
    allow_create: bool = True,
) -> UserRecord | None:
    found = resolve_external_account(engine, provider=provider, external_id=external_id)
    if found is not None or not allow_create:
        return found
    clean_provider = _clean_provider(provider)
    clean_external_id = _clean_external_id(external_id)
    clean_name = display_name.strip()
    limit = max(0, int(daily_message_limit))
    # 用户与外部账号绑定放在同一事务里创建:任一步失败都整体回滚,不会留下没有绑定的
    # 孤儿用户。并发下若另一处理流程抢先绑定,唯一约束触发 IntegrityError,此处回退到
    # 重新解析,返回赢家用户而不是抛错。
    try:
        with session_scope(engine) as s:
            user = User(
                display_name=clean_name,
                status="active",
                daily_message_limit=limit,
            )
            s.add(user)
            s.flush()
            s.add(
                ExternalAccount(
                    user_id=user.id,
                    provider=clean_provider,
                    external_id=clean_external_id,
                    display_name=clean_name,
                )
            )
            s.flush()
            return _user_record(user)
    except IntegrityError:
        resolved = resolve_external_account(engine, provider=provider, external_id=external_id)
        if resolved is None:
            raise
        return resolved


def ensure_local_user(engine: Engine) -> UserRecord:
    user = get_or_create_external_user(
        engine,
        provider="local",
        external_id=DEFAULT_LOCAL_EXTERNAL_ID,
        display_name="local",
        allow_create=True,
    )
    if user is None:  # pragma: no cover - allow_create=True 时不会发生
        raise RuntimeError("failed to create local user")
    return user


def begin_inbound_event(
    engine: Engine,
    *,
    provider: str,
    event_id: str,
    user_id: int | None = None,
    stale_after_seconds: int = INBOUND_EVENT_STALE_SECONDS,
) -> bool:
    """登记入站事件并占用处理权。

    返回 False 表示该事件已成功处理(processed),或正在被另一处理流程处理中
    (processing 且尚未超时);此时调用方应直接丢弃这条重投消息。

    返回 True 表示获得处理权:对于全新事件会插入 processing 行;对于
    rejected/error 或已经卡死超时的 processing 行,会复位为 processing 允许重新处理
    ——否则用户被拉黑后又加入名单、或处理中途报错时,同一 event_id 的重投会被永久
    误判为重复而静默丢弃。
    """
    clean_provider = _clean_provider(provider)
    clean_event_id = _clean_external_id(event_id)
    try:
        with session_scope(engine) as s:
            row = (
                s.query(InboundEvent)
                .filter(InboundEvent.provider == clean_provider)
                .filter(InboundEvent.event_id == clean_event_id)
                .one_or_none()
            )
            if row is not None:
                if row.status == "processed":
                    return False
                if row.status == "processing" and not _inbound_event_is_stale(
                    row, stale_after_seconds
                ):
                    return False
                # rejected / error / 超时 processing:重新占用处理权。
                row.status = "processing"
                row.user_id = user_id
                row.response_text = None
                row.processed_at = None
                row.created_at = utcnow()
                return True
            s.add(
                InboundEvent(
                    provider=clean_provider,
                    event_id=clean_event_id,
                    user_id=user_id,
                    status="processing",
                )
            )
            return True
    except IntegrityError:
        # 并发下另一处理流程已抢先登记同一事件(唯一约束),视为重复丢弃。
        return False


def _inbound_event_is_stale(row: InboundEvent, stale_after_seconds: int) -> bool:
    started = row.created_at
    if started is None:
        return True
    return (utcnow() - started) >= timedelta(seconds=max(0, stale_after_seconds))


def finish_inbound_event(
    engine: Engine,
    *,
    provider: str,
    event_id: str,
    user_id: int | None = None,
    status: str = "processed",
    response_text: str | None = None,
) -> None:
    clean_provider = _clean_provider(provider)
    clean_event_id = _clean_external_id(event_id)
    with session_scope(engine) as s:
        row = (
            s.query(InboundEvent)
            .filter(InboundEvent.provider == clean_provider)
            .filter(InboundEvent.event_id == clean_event_id)
            .one_or_none()
        )
        if row is None:
            row = InboundEvent(provider=clean_provider, event_id=clean_event_id)
            s.add(row)
        row.user_id = user_id
        row.status = status.strip() or "processed"
        row.response_text = response_text
        row.processed_at = utcnow()


def usage_count_today(engine: Engine, *, user_id: int, source: str = "chat") -> int:
    day = utcnow().date().isoformat()
    clean_source = source.strip() or "chat"
    with session_scope(engine) as s:
        row = (
            s.query(UserUsage)
            .filter(UserUsage.user_id == user_id)
            .filter(UserUsage.day == day)
            .filter(UserUsage.source == clean_source)
            .one_or_none()
        )
        return int(row.message_count) if row is not None else 0


def increment_usage(engine: Engine, *, user_id: int, source: str = "chat", amount: int = 1) -> int:
    day = utcnow().date().isoformat()
    clean_source = source.strip() or "chat"
    delta = max(0, int(amount))
    with session_scope(engine) as s:
        row = (
            s.query(UserUsage)
            .filter(UserUsage.user_id == user_id)
            .filter(UserUsage.day == day)
            .filter(UserUsage.source == clean_source)
            .one_or_none()
        )
        if row is None:
            row = UserUsage(
                user_id=user_id,
                day=day,
                source=clean_source,
                message_count=0,
            )
            s.add(row)
        row.message_count += delta
        s.flush()
        return int(row.message_count)


def _clean_provider(value: str) -> str:
    clean = (value or "").strip().lower()
    if not clean:
        raise ValueError("provider is required")
    return clean


def _clean_external_id(value: str) -> str:
    clean = (value or "").strip()
    if not clean:
        raise ValueError("external_id/event_id is required")
    return clean


def _user_persona_record(row: UserPersona) -> UserPersonaRecord:
    try:
        payload = json.loads(row.persona_json)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"user persona json is invalid:user_id={row.user_id}") from e
    return UserPersonaRecord(
        user_id=row.user_id,
        persona=PersonaConfig.model_validate(payload),
        updated_at=row.updated_at,
    )
