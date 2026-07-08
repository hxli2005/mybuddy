"""动态「角色生活」状态合成器(A 方案:身体化暖虚构,但活的 + 接真记忆)。

不再用 config 里写死的 character_life(每次都是同一杯温水),而是每轮从真实信号合成
小布「此刻」的状态:
  - 距上次对话多久(gap)→ 心情(刚聊完的松快 / 好几天没来的惦记与想念);
  - 当前几点(本地时间)→ 身体化的此刻状态(夜里留灯 / 早上拉窗帘);
  - 上次聊的话题(用户真实说过的)→ 「刚还在想你提的那件事」(接真记忆)。

仍是身体化的暖虚构 register(保留小布有身体、有自己日常),但会随真实信号变化,
而非一段死字符串。任何失败 / 无信号(首次对话、无 engine)都回退到 config 的静态
character_life,绝不影响主流程。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from mybuddy._time import utcnow
from mybuddy.config import CharacterLifeConfig, PersonaConfig

# 太短或纯打招呼的消息不当「话题」,避免「刚还在想你提的『在吗』」这种空洞句
_GREETING_TOPICS = {"在吗", "在么", "在不在", "你在吗", "你好", "嗨", "hi", "hello", "在"}
# 纯客套 / 收尾语也不当话题(否则会合成「刚还在想你提的『好 谢谢你』」这种空洞贴心)
_FILLER_RE = re.compile(
    r"^(好的?|嗯+|哈+|谢谢你?|多谢|ok|okay|收到|晚安|拜拜|再见|没事了?|是的?|对的?|行)"
    r"[，。!~、\s]*$",
    re.IGNORECASE,
)

# 本地时段 → 身体化的此刻状态(暖、具体)
_TIME_STATUS: list[tuple[int, int, str]] = [
    (0, 5, "夜挺深了,我就留着一盏灯,等你冒头"),
    (5, 11, "刚把窗帘拉开,桌上那杯水还温着"),
    (11, 14, "手头收拾到一半,听见你来就先停下了"),
    (14, 18, "正歪在椅子里发会儿呆,你就来了"),
    (18, 23, "灯都开上了,正想着你这会儿该忙完了吧"),
    (23, 24, "都这点了还赖着没去睡,就等你一句"),
]


def _status_for_hour(hour: int) -> str:
    for lo, hi, text in _TIME_STATUS:
        if lo <= hour < hi:
            return text
    return CharacterLifeConfig().today_status


def _mood_for_gap(gap_minutes: float | None) -> str:
    if gap_minutes is None:
        return "放松,有点想逗你说两句"
    if gap_minutes < 30:
        return "还在刚才那股劲里,挺松快"
    if gap_minutes < 60 * 24:
        return "心情不错,正好想逗你两句"
    if gap_minutes < 60 * 24 * 4:
        return "有点惦记你,好几天没动静了"
    return "说实话有点想你了,这么久没冒头,我这边一直给你留着位置"


def _clean_topic(text: str) -> str:
    clean = (text or "").strip().replace("\n", " ")
    if len(clean) < 4 or clean.lower() in _GREETING_TOPICS or _FILLER_RE.match(clean):
        return ""
    return clean[:14] + ("…" if len(clean) > 14 else "")


def _pick_topic(rows: list[dict]) -> str:
    """从最近的用户消息里挑最实质的一条(最长、非客套),而非生硬取最后一句。"""
    cands = [_clean_topic(r.get("content", "")) for r in rows if r.get("role") == "user"]
    cands = [c for c in cands if c]
    return max(cands, key=len) if cands else ""


def synthesize_living_state(
    persona: PersonaConfig,
    *,
    engine=None,  # noqa: ANN001 —— SQLAlchemy Engine,避免 import 环
    session_id: str = "",
    now_utc: datetime | None = None,
    now_local: datetime | None = None,
    body_state: dict[str, Any] | None = None,
    body_state_injection: bool = False,
) -> CharacterLifeConfig:
    """合成小布此刻的动态生活状态;无信号时回退到静态 character_life。"""
    base = persona.character_life
    real_body = body_state if body_state_injection and body_state else None
    if engine is None:
        if real_body:
            return _life_from_body_state(base, real_body, topic="")
        return base
    try:
        from mybuddy.storage import list_messages

        rows = list_messages(engine, limit=20, session_id=session_id or None)
    except Exception:  # noqa: BLE001 —— 合成是尽力而为,任何失败都回退静态,不阻塞对话
        if real_body:
            return _life_from_body_state(base, real_body, topic="")
        return base
    if not rows:
        if real_body:
            return _life_from_body_state(base, real_body, topic="")
        return base  # 首次对话:用 config 的静态 seed

    now_utc = now_utc or utcnow()
    now_local = now_local or datetime.now().astimezone()

    # gap:距上一条消息(本轮 user 尚未入库,故 rows[-1] 是上一轮)
    gap_min: float | None = None
    last_ts = rows[-1].get("created_at")
    if last_ts:
        try:
            gap_min = max((now_utc - datetime.fromisoformat(last_ts)).total_seconds() / 60.0, 0.0)
        except (TypeError, ValueError):
            gap_min = None

    topic = _pick_topic(rows)
    if real_body:
        return _life_from_body_state(base, real_body, topic=topic)

    return CharacterLifeConfig(
        today_status=_status_for_hour(now_local.hour),
        current_mood=_mood_for_gap(gap_min),
        recent_self_event=(f"刚还在想你上次提的「{topic}」,惦记着呢" if topic else base.recent_self_event),
        availability_style=base.availability_style,
    )


def _life_from_body_state(
    base: CharacterLifeConfig,
    body_state: dict[str, Any],
    *,
    topic: str,
) -> CharacterLifeConfig:
    """用 VPet 屏幕上真实身体数值接管身体相关状态。"""
    food = _body_number(body_state, "food")
    drink = _body_number(body_state, "drink")
    feeling = _body_number(body_state, "feeling")
    health = _body_number(body_state, "health")
    strength = _body_number(body_state, "strength")
    mode = str(body_state.get("mode") or "")

    status_bits: list[str] = []
    if food is not None and food <= 30:
        status_bits.append("肚子有点空")
    if drink is not None and drink <= 30:
        status_bits.append("有点口渴")
    if health is not None and health <= 30:
        status_bits.append("身体不太舒服")
    if strength is not None and strength <= 30:
        status_bits.append("有点没力气")
    if mode == "Ill":
        status_bits.append("看起来在生病")
    elif mode == "PoorCondition":
        status_bits.append("状态不太好")
    today_status = "、".join(status_bits) if status_bits else "身体数值看起来还稳,按眼前状态陪你"

    if feeling is not None and feeling <= 30:
        current_mood = "情绪有点低,会更黏一点"
    elif strength is not None and strength <= 30:
        current_mood = "偏累,说话会放轻一点"
    elif food is not None and food <= 30:
        current_mood = "肚子有点空,语气会更黏一点"
    elif (health is not None and health <= 30) or mode in {"Ill", "PoorCondition"}:
        current_mood = "不太舒服,会更想贴近一点"
    elif feeling is not None and feeling >= 70:
        current_mood = "心情还不错,但会照着眼前状态说话"
    else:
        current_mood = "情绪平稳,照着眼前身体状态说话"

    return CharacterLifeConfig(
        today_status=today_status,
        current_mood=current_mood,
        recent_self_event=(f"刚还在想你上次提的「{topic}」,惦记着呢" if topic else base.recent_self_event),
        availability_style=base.availability_style,
    )


def _body_number(body_state: dict[str, Any], key: str) -> float | None:
    value = body_state.get(key)
    if isinstance(value, int | float):
        return float(value)
    return None
