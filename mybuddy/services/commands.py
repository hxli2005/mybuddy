"""跨渠道文本命令。

QQ、Web、未来 App 可以复用同一套命令处理,避免每个渠道各自实现 /help、
/quota、/good 等逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .chat import ChatService, RequestContext


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    text: str = ""


PERSONA_FIELD_ALIASES = {
    "name": "name",
    "名字": "name",
    "style": "style",
    "风格": "style",
    "tone": "tone",
    "语气": "tone",
    "relationship": "relationship",
    "关系": "relationship",
    "address": "address_user",
    "称呼": "address_user",
}

PERSONA_FIELD_LABELS = {
    "name": "名字",
    "style": "整体风格",
    "tone": "语气",
    "relationship": "关系定位",
    "address_user": "称呼",
}


class ChannelCommandService:
    def __init__(self, chat_service: ChatService) -> None:
        self._chat = chat_service

    def handle(self, ctx: RequestContext, text: str) -> CommandResult:
        clean = (text or "").strip()
        if not clean.startswith("/"):
            return CommandResult(handled=False)
        # 命令同样要尊重账号状态。被禁用的用户能改人格/查额度/反馈,是因为命令路径绕过了
        # chat() 里的 is_active 检查;在分发任何命令前先拦掉。
        user = self._chat.get_user_record(ctx.user_id)
        if user is None:
            return CommandResult(handled=True, text="账号不存在,请联系管理员开通。")
        if not user.is_active:
            return CommandResult(handled=True, text="你的测试账号当前未启用,请联系管理员开通。")
        command, _, arg = clean.partition(" ")
        cmd = command.lower()
        if cmd == "/help":
            return CommandResult(
                handled=True,
                text=(
                    "可用命令:\n"
                    "/help 查看帮助\n"
                    "/quota 查看今日测试额度\n"
                    "/persona 查看或修改个人 AI 人格\n"
                    "/privacy 查看数据说明\n"
                    "/reset 重置当前运行上下文\n"
                    "/good 标记上一轮有帮助\n"
                    "/bad 标记上一轮不合适"
                ),
            )
        if cmd == "/privacy":
            return CommandResult(
                handled=True,
                text=(
                    "测试期会保存对话文本、反馈、提醒、笔记和抽取出的记忆,用于提供连续聊天体验。"
                    "你可以联系管理员清空或删除测试数据。"
                ),
            )
        if cmd == "/quota":
            quota = self._chat.quota_payload(ctx)
            limit = quota["limit"]
            used = quota["used"]
            if limit <= 0:
                return CommandResult(handled=True, text=f"今日已使用 {used} 轮,当前不设上限。")
            return CommandResult(handled=True, text=f"今日额度:{used}/{limit},剩余 {quota['remaining']} 轮。")
        if cmd == "/persona":
            return self._handle_persona(ctx, arg.strip())
        if cmd == "/reset":
            changed = self._chat.reset_runtime(ctx.user_id)
            return CommandResult(
                handled=True,
                text="已重置当前上下文。" if changed else "当前没有需要重置的运行上下文。",
            )
        if cmd in {"/good", "/bad"}:
            label = "good" if cmd == "/good" else "bad"
            try:
                self._chat.feedback(ctx, label=label, turn_id=arg.strip() or None)
            except RuntimeError as e:
                return CommandResult(handled=True, text=str(e))
            return CommandResult(handled=True, text=f"已记录反馈:{label}")
        return CommandResult(handled=True, text="未知命令。发送 /help 查看可用命令。")

    def _handle_persona(self, ctx: RequestContext, arg: str) -> CommandResult:
        if not arg:
            return CommandResult(handled=True, text=self._format_persona(self._chat.persona_payload(ctx)))

        subcommand, _, value = arg.partition(" ")
        sub = subcommand.lower()
        clean_value = value.strip()
        if sub in {"help", "帮助"}:
            return CommandResult(handled=True, text=_persona_help_text())
        if sub in {"reset", "重置"}:
            payload = self._chat.reset_persona_payload(ctx)
            return CommandResult(
                handled=True,
                text=f"已重置为全局默认人格。\n{self._format_persona(payload)}",
            )
        if sub in {"habit", "习惯"}:
            if not clean_value:
                return CommandResult(handled=True, text="请在 /persona habit 后面写要追加的回应习惯。")
            payload = self._append_persona_habit(ctx, clean_value)
            return CommandResult(
                handled=True,
                text=f"已追加回应习惯:{clean_value}\n{self._format_persona(payload)}",
            )
        if sub in {"habits", "习惯列表"}:
            if clean_value.lower() not in {"clear", "清空"}:
                return CommandResult(handled=True, text="回应习惯只支持: /persona habits clear")
            payload = self._chat.update_persona_payload(ctx, {"response_habits": []})
            return CommandResult(handled=True, text=f"已清空回应习惯。\n{self._format_persona(payload)}")

        field = PERSONA_FIELD_ALIASES.get(sub)
        if field is None:
            return CommandResult(handled=True, text=_persona_help_text())
        if not clean_value:
            label = PERSONA_FIELD_LABELS[field]
            return CommandResult(handled=True, text=f"请在 /persona {subcommand} 后面写新的{label}。")
        payload = self._chat.update_persona_payload(ctx, {field: clean_value})
        return CommandResult(
            handled=True,
            text=f"已更新{PERSONA_FIELD_LABELS[field]}。\n{self._format_persona(payload)}",
        )

    def _append_persona_habit(self, ctx: RequestContext, habit: str) -> dict[str, Any]:
        payload = self._chat.persona_payload(ctx)
        persona = payload["persona"]
        habits = [str(item).strip() for item in persona.get("response_habits", []) if str(item).strip()]
        habits.append(habit)
        return self._chat.update_persona_payload(ctx, {"response_habits": habits})

    def _format_persona(self, payload: dict[str, Any]) -> str:
        persona = payload["persona"]
        mode = "继承全局默认" if payload.get("inherits_default") else "个人自定义"
        habits = [str(item).strip() for item in persona.get("response_habits", []) if str(item).strip()]
        lines = [
            f"当前 AI 人格:{mode}",
            f"名字:{persona.get('name') or '-'}",
            f"称呼:{persona.get('address_user') or '-'}",
            f"关系:{_compact(persona.get('relationship') or '-')}",
            f"风格:{_compact(persona.get('style') or '-')}",
            f"语气:{_compact(persona.get('tone') or '-')}",
        ]
        if habits:
            lines.append("回应习惯:")
            lines.extend(f"- {_compact(habit, limit=42)}" for habit in habits[:5])
            if len(habits) > 5:
                lines.append(f"- 还有 {len(habits) - 5} 条")
        return "\n".join(lines)


def _persona_help_text() -> str:
    return (
        "人格命令:\n"
        "/persona 查看当前人格\n"
        "/persona name 小鹿 修改名字\n"
        "/persona style 更直接一点 修改整体风格\n"
        "/persona tone 自然、短句 修改语气\n"
        "/persona relationship 像学习搭子 修改关系定位\n"
        "/persona address 阿航 修改称呼\n"
        "/persona habit 先给结论 追加回应习惯\n"
        "/persona habits clear 清空回应习惯\n"
        "/persona reset 重置为默认人格"
    )


def _compact(value: str, *, limit: int = 56) -> str:
    clean = " ".join(value.split())
    if len(clean) <= limit:
        return clean
    return f"{clean[:limit - 1]}..."
