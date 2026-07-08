"""VPet bridge payload helpers.

MyBuddy keeps the companion AI state; VPet keeps the desktop window, animation,
speech bubble, and optional TTS. This module converts MyBuddy chat and pending
message payloads into a small, stable shape that a VPet C# plugin can consume
without understanding Agent internals.
"""

from __future__ import annotations

from typing import Any

BRIDGE_VERSION = "vpet-bridge/1"
_NUM_0_100 = {"food", "drink", "feeling", "health", "strength"}
_UNBOUNDED_NONNEG = {"likability", "money"}
_MODES = {"Happy", "Nomal", "PoorCondition", "Ill"}


def normalize_body_state(value: Any) -> dict[str, Any]:
    """白名单归一化 VPet 身体数值,供事件遥测落表。"""
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key)
        if clean_key in _NUM_0_100:
            number = _number(item)
            if number is not None:
                out[clean_key] = max(0, min(100, number))
            continue
        if clean_key in _UNBOUNDED_NONNEG:
            number = _number(item)
            if number is not None:
                out[clean_key] = max(0, min(100000, number))
            continue
        if clean_key == "mode" and str(item) in _MODES:
            out[clean_key] = str(item)
    return out


def chat_to_vpet_payload(
    chat: dict[str, Any],
    *,
    source_event: str = "chat",
) -> dict[str, Any]:
    """Convert an `/api/chat` style payload into a VPet-facing response."""
    text = str(chat.get("text") or "")
    action = action_for_chat(chat, source_event=source_event)
    return {
        "ok": True,
        "bridge": BRIDGE_VERSION,
        "source_event": source_event,
        "text": text,
        "speech": {
            "text": text,
            "interrupt": action["priority"] >= 80,
        },
        "action": action,
        "expression": expression_for_action(action["name"]),
        "emotion": chat.get("emotion"),
        "emotional_support": chat.get("emotional_support"),
        "turn_id": chat.get("turn_id"),
        "finish_reason": chat.get("finish_reason"),
        "tool_calls": chat.get("tool_calls") or [],
        "triggered_skills": chat.get("triggered_skills") or [],
        "search_sources": chat.get("search_sources") or [],
        "pending": [
            pending_to_vpet_event(item)
            for item in chat.get("pending_messages") or []
        ],
    }


def pending_to_vpet_payload(items: list[dict[str, Any]], *, drained: bool) -> dict[str, Any]:
    """Convert pending MyBuddy messages into VPet events."""
    events = [pending_to_vpet_event(item) for item in items]
    return {
        "ok": True,
        "bridge": BRIDGE_VERSION,
        "drained": drained,
        "events": events,
    }


def pending_to_vpet_event(item: dict[str, Any]) -> dict[str, Any]:
    source = str(item.get("source") or "unknown")
    content = str(item.get("content") or "")
    action = action_for_pending(source)
    interrupt = bool(item.get("interrupt", source == "reminder"))
    speech = {
        "text": content,
        "interrupt": interrupt,
    }
    if "persistent" in item:
        speech["persistent"] = bool(item.get("persistent"))
    return {
        "id": item.get("id"),
        "source": source,
        "role": item.get("role") or _role_for_pending(source),
        "text": content,
        "speech": speech,
        "action": action,
        "expression": expression_for_action(action["name"]),
        "scheduled_at": item.get("scheduled_at"),
        "message_id": item.get("message_id"),
        "meta": item.get("meta") or {},
    }


def action_for_chat(
    chat: dict[str, Any],
    *,
    source_event: str = "chat",
) -> dict[str, Any]:
    emotion = chat.get("emotion") if isinstance(chat.get("emotion"), dict) else {}
    support = (
        chat.get("emotional_support")
        if isinstance(chat.get("emotional_support"), dict)
        else {}
    )
    label = str(emotion.get("label") or support.get("label") or "neutral")
    strength = _float(emotion.get("strength") or support.get("strength"))
    support_mode = str(support.get("mode") or "neutral")

    if chat.get("finish_reason") == "quota_exceeded":
        return _action("idle", priority=20, reason="quota_exceeded")
    if support_mode == "safety":
        return _action("safety", priority=100, reason="safety_support")
    if label == "negative" and strength >= 0.6:
        return _action("concern", priority=85, reason="strong_negative_emotion")
    if label == "negative":
        return _action("comfort", priority=70, reason="negative_emotion")
    if label == "positive" and strength >= 0.3:
        return _action("happy", priority=55, reason="positive_emotion")
    if chat.get("tool_calls"):
        return _action("thinking", priority=50, reason="tool_assisted_reply")
    if source_event and source_event not in {"chat", "user_chat"}:
        return _action("react", priority=45, reason=f"event:{source_event}")
    return _action("talk", priority=40, reason="normal_reply")


def action_for_pending(source: str) -> dict[str, Any]:
    if source == "reminder":
        return _action("remind", priority=90, reason="scheduled_reminder")
    if source == "greeting":
        return _action("greet", priority=50, reason="daily_greeting")
    if source in {"nudge", "dynamic"}:
        return _action("concern", priority=75, reason=f"proactive_{source}")
    return _action("notify", priority=45, reason=f"pending_{source or 'unknown'}")


def expression_for_action(action: str) -> dict[str, str]:
    mapping = {
        "comfort": "worried",
        "concern": "worried",
        "greet": "smile",
        "happy": "happy",
        "idle": "neutral",
        "notify": "neutral",
        "react": "curious",
        "remind": "alert",
        "safety": "serious",
        "talk": "neutral",
        "thinking": "thinking",
    }
    return {"name": mapping.get(action, "neutral")}


def _action(name: str, *, priority: int, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "priority": priority,
        "loop": False,
        "reason": reason,
    }


def _role_for_pending(source: str) -> str:
    return "assistant" if source in {"greeting", "nudge", "dynamic"} else "system"


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _number(value: Any) -> int | float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number
