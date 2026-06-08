"""应用服务层。

Web、QQ、未来 App 等入口都应优先调用这里,避免渠道代码直接碰 Agent/Memory。
"""

from .chat import ChatResponse, ChatService, RequestContext
from .commands import ChannelCommandService

__all__ = [
    "ChannelCommandService",
    "ChatResponse",
    "ChatService",
    "RequestContext",
]
