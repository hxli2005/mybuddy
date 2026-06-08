"""QQ 官方机器人渠道适配。

核心约束:
  - QQ 只做 channel adapter,不直接操作 Agent/Memory/Profile。
  - 所有聊天统一进入 ChatService。
  - 入站事件先去重,再创建/解析用户,最后调用服务层。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mybuddy.config import Config, load_config
from mybuddy.services import ChannelCommandService, ChatService, RequestContext
from mybuddy.storage import (
    begin_inbound_event,
    finish_inbound_event,
    get_or_create_external_user,
)

logger = logging.getLogger(__name__)

QQ_PROVIDER = "qq"


ReplyFunc = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class QQInboundMessage:
    event_id: str
    external_user_id: str
    content: str
    display_name: str = ""
    raw: Any = None


class QQBotAdapter:
    def __init__(
        self,
        *,
        chat_service: ChatService,
        allow_auto_create_user: bool,
        daily_message_limit: int = 30,
        reply_on_duplicate: bool = False,
        command_service: ChannelCommandService | None = None,
    ) -> None:
        self.chat_service = chat_service
        self.command_service = command_service or ChannelCommandService(chat_service)
        self.allow_auto_create_user = allow_auto_create_user
        self.daily_message_limit = daily_message_limit
        self.reply_on_duplicate = reply_on_duplicate

    async def handle_message(
        self,
        message: QQInboundMessage,
        reply: ReplyFunc,
    ) -> str | None:
        self.chat_service.ensure_started()
        engine = self.chat_service.engine
        if engine is None:
            raise RuntimeError("chat service is not initialized")

        if not begin_inbound_event(
            engine,
            provider=QQ_PROVIDER,
            event_id=message.event_id,
        ):
            if self.reply_on_duplicate:
                await reply("这条消息已经处理过了。")
                return "duplicate"
            return None

        # 一旦占用了处理权,无论用户解析、对话还是回复哪一步失败,都必须给入站事件落一个
        # 终态(processed/rejected/error)。否则行会卡在 processing,后续重投会被去重永久
        # 丢弃。回复也放在 try 内:回复失败应记为 error 让消息可被重试,而不是当作已处理。
        user_id: int | None = None
        status = "processed"
        text = ""
        try:
            user = get_or_create_external_user(
                engine,
                provider=QQ_PROVIDER,
                external_id=message.external_user_id,
                display_name=message.display_name,
                daily_message_limit=self.daily_message_limit,
                allow_create=self.allow_auto_create_user,
            )
            if user is None:
                status = "rejected"
                text = "你还没有加入 MyBuddy 测试名单。请联系管理员开通。"
            else:
                user_id = user.id
                ctx = RequestContext(
                    user_id=user.id,
                    source=QQ_PROVIDER,
                    external_id=message.external_user_id,
                )
                command = self.command_service.handle(ctx, message.content)
                if command.handled:
                    text = command.text
                else:
                    response = await self.chat_service.chat(ctx, message.content)
                    text = response.text
            await reply(text)
        except Exception as e:  # noqa: BLE001
            logger.exception("QQ message handling failed")
            status = "error"
            text = f"我这边刚才卡住了:{type(e).__name__}。稍后再试一下。"
            try:
                await reply(text)
            except Exception:  # noqa: BLE001
                logger.exception("QQ error reply failed")
        finally:
            finish_inbound_event(
                engine,
                provider=QQ_PROVIDER,
                event_id=message.event_id,
                user_id=user_id,
                status=status,
                response_text=text,
            )
        return text


class QQBotRunner:
    """基于 qq-botpy 的常驻 QQ bot 运行器。

    botpy 作为可选依赖导入。没有安装时,CLI 会给出明确安装提示。
    """

    def __init__(
        self,
        *,
        config_path: str = "config.yaml",
        max_steps: int = 6,
        chat_service: ChatService | None = None,
    ) -> None:
        self.config_path = config_path
        self.max_steps = max_steps
        self.cfg: Config = load_config(config_path)
        self.chat_service = chat_service or ChatService(
            config_path=config_path,
            max_steps=max_steps,
        )
        qq = self.cfg.channels.qq
        self.adapter = QQBotAdapter(
            chat_service=self.chat_service,
            allow_auto_create_user=qq.allow_auto_create_user,
            daily_message_limit=qq.daily_message_limit,
            reply_on_duplicate=qq.reply_on_duplicate,
        )

    def run(self) -> None:
        qq = self.cfg.channels.qq
        if not qq.enabled:
            raise RuntimeError("channels.qq.enabled=false,请先在配置中启用 QQ 渠道")
        if not qq.app_id or not qq.app_secret:
            raise RuntimeError("QQ app_id/app_secret 未配置")
        self.chat_service.startup()
        try:
            client = self._make_botpy_client()
            client.run(appid=qq.app_id, secret=qq.app_secret)
        finally:
            self.chat_service.shutdown()

    def _make_botpy_client(self):
        try:
            import botpy  # type: ignore[import-not-found]
        except ModuleNotFoundError as e:
            raise RuntimeError("缺少 QQ 依赖,请运行: uv sync --extra qq") from e

        adapter = self.adapter

        class MyBuddyQQClient(botpy.Client):  # type: ignore[misc]
            async def on_c2c_message_create(self, message):  # noqa: ANN001
                await _handle_botpy_message(adapter, self, message)

            async def on_direct_message_create(self, message):  # noqa: ANN001
                await _handle_botpy_message(adapter, self, message)

            async def on_at_message_create(self, message):  # noqa: ANN001
                await _handle_botpy_message(adapter, self, message)

            async def on_group_at_message_create(self, message):  # noqa: ANN001
                await _handle_botpy_message(adapter, self, message)

        intents = _make_botpy_intents(botpy)
        return MyBuddyQQClient(intents=intents, is_sandbox=self.cfg.channels.qq.sandbox)


async def _handle_botpy_message(adapter: QQBotAdapter, client: Any, message: Any) -> None:
    inbound = QQInboundMessage(
        event_id=_extract_event_id(message),
        external_user_id=_extract_external_user_id(message),
        display_name=_extract_display_name(message),
        content=_clean_content(_extract_content(message)),
        raw=message,
    )
    if not inbound.content:
        return

    async def reply(text: str) -> None:
        await _reply_botpy_message(client, message, text)

    await adapter.handle_message(inbound, reply)


def _make_botpy_intents(botpy: Any) -> Any:
    intents = getattr(botpy, "Intents", None)
    if intents is None:
        return None
    try:
        return intents(
            public_messages=True,
            public_guild_messages=True,
            direct_message=True,
        )
    except TypeError:
        try:
            return intents(public_messages=True, public_guild_messages=True)
        except TypeError:
            return intents()


async def _reply_botpy_message(client: Any, message: Any, text: str) -> None:
    if hasattr(message, "reply"):
        result = message.reply(content=text)
        if asyncio.iscoroutine(result):
            await result
        return

    api = getattr(client, "api", None)
    msg_id = getattr(message, "id", None)
    channel_id = getattr(message, "channel_id", None)
    if api is not None and channel_id and hasattr(api, "post_message"):
        await api.post_message(channel_id=channel_id, content=text, msg_id=msg_id)
        return

    group_openid = _extract_group_openid(message)
    if api is not None and group_openid and hasattr(api, "post_group_message"):
        await api.post_group_message(
            group_openid=group_openid,
            msg_type=0,
            msg_id=msg_id,
            content=text,
        )
        return

    openid = _extract_external_user_id(message)
    if api is not None and hasattr(api, "post_c2c_message"):
        await api.post_c2c_message(openid=openid, msg_type=0, msg_id=msg_id, content=text)
        return

    raise RuntimeError("当前 botpy 消息对象不支持自动回复")


def _extract_event_id(message: Any) -> str:
    for attr in ("id", "event_id", "msg_id"):
        value = getattr(message, attr, None)
        if value:
            return str(value)
    user_id = _extract_external_user_id(message)
    content = _extract_content(message)
    # str.__hash__ 受 PYTHONHASHSEED 每进程加盐,重启后同一条消息会得到不同 event_id,
    # 破坏跨重启去重。改用稳定摘要,保证同一内容始终映射到同一 event_id。
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
    return f"{user_id}:{digest}"


def _extract_external_user_id(message: Any) -> str:
    for attr in ("openid", "user_openid", "author_id", "user_id", "member_openid"):
        value = getattr(message, attr, None)
        if value:
            return str(value)
    author = getattr(message, "author", None)
    if author is not None:
        for attr in ("member_openid", "user_openid", "openid", "id"):
            value = getattr(author, attr, None)
            if value:
                return str(value)
    raise RuntimeError("无法从 QQ 消息中解析用户 ID")


def _extract_group_openid(message: Any) -> str | None:
    for attr in ("group_openid", "group_id"):
        value = getattr(message, attr, None)
        if value:
            return str(value)
    return None


def _extract_display_name(message: Any) -> str:
    author = getattr(message, "author", None)
    if author is not None:
        for attr in ("username", "nick", "name"):
            value = getattr(author, attr, None)
            if value:
                return str(value)
    for attr in ("username", "nick", "name"):
        value = getattr(message, attr, None)
        if value:
            return str(value)
    return ""


def _extract_content(message: Any) -> str:
    for attr in ("content", "text", "message"):
        value = getattr(message, attr, None)
        if value:
            return str(value)
    return ""


def _clean_content(text: str) -> str:
    clean = (text or "").strip()
    clean = re.sub(r"<@!?\d+>", "", clean).strip()
    return clean
