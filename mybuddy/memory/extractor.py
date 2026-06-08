"""LLM 事实抽取器:从对话中抽取值得长期记忆的内容。

借鉴 mem0 的自动抽取思路:
  - 每 N 轮对话后,把最近的消息交给 LLM,让它判断哪些信息"值得记住"
  - 输出明确事实、用户画像字段和少量核心关系记忆

抽取结果:
  - facts: 要写入 profile 记忆卡的明确事实
  - profile_fields: {"key": "value"} 类型的字段更新

注意:抽取是"可能产生幻觉"的操作(M3 无法完全消除,属于已知风险),
因此弱推测默认不进入长期记忆,需要后续证据持续增强。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mybuddy.llm import BaseLLMProvider

CORE_RELATIONSHIP_MEMORY_TYPES = (
    "preference",
    "shared_moment",
    "open_thread",
)

LEGACY_RELATIONSHIP_MEMORY_TYPES = (
    "anti_preference",
    "relationship_note",
    "character_note",
)

RELATIONSHIP_MEMORY_TYPES = (
    *CORE_RELATIONSHIP_MEMORY_TYPES,
    *LEGACY_RELATIONSHIP_MEMORY_TYPES,
)


EXTRACT_PROMPT = """你是一个关系记忆管理助手。请从以下用户与 AI 的对话中,提取值得长期记住的信息。

严格按 JSON 格式输出,不要输出任何其他文本:

{
  "facts": ["值得记住的事实 1", "值得记住的事实 2"],
  "profile_fields": {"字段名": "字段值"},
  "relationship_memories": {
    "preference": [
      {
        "title": "偏好或避雷标题",
        "content": "用户明确表达过的偏好、禁忌或回应方式",
        "triggers": ["再次出现时可使用的触发词"],
        "confidence": 0.8
      }
    ],
    "shared_moment": [
      {
        "title": "共同经历标题",
        "content": "用户和 AI 一起经历/形成的互动片段",
        "triggers": ["再次出现时可召回的触发词"],
        "emotional_color": "低压/安心/信任等",
        "callback_style": "轻轻提起,不要解释过多",
        "confidence": 0.8
      }
    ],
    "open_thread": [
      {
        "title": "未完成话题",
        "content": "之后有具体由头可关心的事",
        "contact_reason": "为什么之后可以提起",
        "event_time": "如果对话出现明确发生时间,填 ISO 日期或原始时间短语",
        "expires_at": "如果未完成话题有明确截止/过期时间,填 ISO 日期时间或原始时间短语",
        "triggers": ["相关触发词"],
        "confidence": 0.7
      }
    ]
  },
  "entities": [
    {"name": "人名或宠物名", "relation": "与用户的关系(如 母亲/猫/同事/好友)", "note": "关于 TA 的关键信息"}
  ],
  "corrections": [
    {"old": "用户之前说过、现在被改口或否定的旧信息", "reason": "用户如何纠正的"}
  ]
}

规则:
- facts:从对话中提取明确陈述的事实(如"用户叫小明""用户喜欢美式咖啡")。无则返回空数组。
- profile_fields:可确定为真的用户属性(如名字、生日、饮食偏好、过敏信息)。无则返回空对象。
  字段名用中文简写(如"名字""生日""咖啡偏好""过敏"等)。
- relationship_memories:
  - 只使用 preference/shared_moment/open_thread 三类。不要新增其他类型。
  - preference:用户明确表达的稳定偏好、避雷、喜欢/不喜欢的回应方式。
  - shared_moment:用户和 AI 之间形成的共同经历、回忆卡、有效陪伴片段。
  - open_thread:未来有明确由头可回访的未完成话题;必须有具体 evidence,不要泛泛关心。
    如果能判断截止或过期时间,填写 expires_at;如果能判断事件发生时间,填写 event_time。
- entities:用户生活里反复或明确提到的重要的人或宠物(家人/伴侣/好友/同事/宠物)。
  name 填名字或称呼,relation 填关系,note 填关键信息。只记真正重要、明确提到的;
  一次性路人、泛指的人群不要记。无则返回空数组。
- corrections:仅当用户明确改口、否定或撤回之前说过的信息时填(如"其实不是…""我之前说错了""现在不…了")。
  old 用一句话描述要作废的旧信息;纠正后的新信息照常放 facts/profile_fields,不要放这里。无则返回空数组。
- 不要编造对话中未出现的内容。
- 事实和画像只针对用户(USER);relationship_memories 可以抽取用户与 AI 的互动结果,但必须有对话证据。
- 不要记录普通寒暄、短暂情绪、一次性任务过程或 AI 自己臆测的性格标签。
- 不要生成恋爱化、占有欲、越界承诺或医疗心理诊断。
"""


class FactExtractResult:
    """抽取结果。"""

    def __init__(
        self,
        facts: list[str] | None = None,
        profile_fields: dict[str, str] | None = None,
        relationship_memories: dict[str, list[dict[str, Any]]] | None = None,
        corrections: list[dict[str, str]] | None = None,
        entities: list[dict[str, str]] | None = None,
    ) -> None:
        self.facts: list[str] = facts or []
        self.profile_fields: dict[str, str] = profile_fields or {}
        self.relationship_memories: dict[str, list[dict[str, Any]]] = (
            relationship_memories or {k: [] for k in RELATIONSHIP_MEMORY_TYPES}
        )
        # 用户显式改口/否定:每项 {"old": 要作废的旧信息, "reason": 纠正说法}
        self.corrections: list[dict[str, str]] = corrections or []
        # 用户身边重要的人/宠物:每项 {"name", "relation", "note"}
        self.entities: list[dict[str, str]] = entities or []

    def is_empty(self) -> bool:
        return (
            not self.facts
            and not self.profile_fields
            and not any(self.relationship_memories.values())
            and not self.corrections
            and not self.entities
        )

    def __repr__(self) -> str:
        return (
            f"FactExtractResult(facts={len(self.facts)}, "
            f"fields={len(self.profile_fields)}, "
            f"relationship_memories={sum(len(v) for v in self.relationship_memories.values())})"
        )


class FactExtractor:
    """用 LLM 从对话片段中抽取事实、字段和命题候选。"""

    def __init__(self, provider: BaseLLMProvider, small_model: str | None = None) -> None:
        self._provider = provider
        self._small_model = small_model

    async def extract(self, recent_messages: list[str]) -> FactExtractResult:
        """输入最近轮次的对话文本,返回抽取结果。"""
        if not recent_messages:
            return FactExtractResult()

        from mybuddy._time import utcnow

        conversation = "\n".join(recent_messages)
        today = utcnow().date().isoformat()
        user_prompt = f"当前日期:{today}\n\n对话:\n{conversation}\n\n请提取信息,只输出 JSON:"

        from mybuddy.llm import Message, Role

        resp = await self._provider.generate(
            messages=[Message(role=Role.USER, content=user_prompt)],
            system=EXTRACT_PROMPT,
            temperature=0.3,
            model=self._small_model or None,
        )

        return self._parse(resp.text)

    def _parse(self, text: str) -> FactExtractResult:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取第一个 JSON 对象
            data = _extract_json_object(text)

        if not isinstance(data, dict):
            return FactExtractResult()

        return FactExtractResult(
            facts=_str_list(data.get("facts")),
            profile_fields=_str_dict(data.get("profile_fields")),
            relationship_memories=_relationship_memories(data),
            corrections=_corrections(data.get("corrections")),
            entities=_entities(data.get("entities")),
        )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从可能夹杂了 markdown 代码块 / 客套话的文本中提取 JSON 对象。

    小模型常见失败:'好的,以下是结果:{...}'、JSON 后多一句解释、漏掉 ``` 围栏。
    依次尝试:剥围栏后整段 → 第一个括号配平的 {...}(容忍前后多余文本),
    都失败才返回 None。避免一句客套话就让整批抽取静默丢弃。
    """
    # 去掉 ```json ... ``` 包裹
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean = "\n".join(lines)

    for candidate in (clean, _first_balanced_object(clean)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _first_balanced_object(text: str) -> str | None:
    """返回第一个括号配平的 ``{...}`` 子串(正确跳过字符串字面量内的花括号)。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        # 模型偶尔把事实返成 {"text": "..."} / {"fact": "..."} 而非裸字符串;
        # 直接 str(dict) 会得到 "{'text': ...}" repr 垃圾,这里取其文本键。
        if isinstance(item, dict):
            s = str(item.get("text") or item.get("content") or item.get("fact") or "").strip()
        else:
            s = str(item).strip()
        if s:
            out.append(s)
    return out


def _entities(value: Any) -> list[dict[str, str]]:
    """规整 entities:[{name, relation, note}]。name/note 至少有一个才保留。"""
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        relation = str(item.get("relation") or "").strip()
        note = str(item.get("note") or item.get("content") or "").strip()
        if name or note:
            out.append({"name": name, "relation": relation, "note": note})
    return out


def _corrections(value: Any) -> list[dict[str, str]]:
    """规整 corrections:[{old, reason}],兼容裸字符串。"""
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            old = item.strip()
            if old:
                out.append({"old": old, "reason": ""})
            continue
        if not isinstance(item, dict):
            continue
        old = str(item.get("old") or item.get("content") or "").strip()
        if old:
            out.append({"old": old, "reason": str(item.get("reason") or "").strip()})
    return out


def _str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in value.items():
        key = str(k).strip()
        if not key:
            continue
        # 多值字段(过敏/兴趣等)模型常返成 list;str(list) 会写成 "['花生','海鲜']"
        # repr 并注入提示词。list 拼成顿号分隔,复杂结构(dict)跳过避免脏数据。
        if isinstance(v, list):
            val = "、".join(str(x).strip() for x in v if str(x).strip())
        elif isinstance(v, (str, int, float, bool)):
            val = str(v).strip()
        else:
            continue
        if val:
            out[key] = val
    return out


def _relationship_memories(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out = {k: [] for k in RELATIONSHIP_MEMORY_TYPES}
    raw = data.get("relationship_memories")
    if isinstance(raw, dict):
        for mem_type in RELATIONSHIP_MEMORY_TYPES:
            target = _canonical_relationship_type(mem_type)
            if target:
                out[target].extend(_normalize_memory_items(raw.get(mem_type)))
    # 兼容模型把新字段直接放顶层的情况。
    for mem_type in RELATIONSHIP_MEMORY_TYPES:
        target = _canonical_relationship_type(mem_type)
        if target:
            out[target].extend(_normalize_memory_items(data.get(mem_type)))
    return out


def _canonical_relationship_type(mem_type: str) -> str | None:
    if mem_type in CORE_RELATIONSHIP_MEMORY_TYPES:
        return mem_type
    if mem_type in {"anti_preference", "relationship_note"}:
        return "preference"
    return None


def _normalize_memory_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                items.append({"content": text})
            continue
        if not isinstance(item, dict):
            continue
        normalized = {str(k): v for k, v in item.items()}
        content = (
            normalized.get("content")
            or normalized.get("summary")
            or normalized.get("text")
            or normalized.get("title")
        )
        if content and str(content).strip():
            normalized["content"] = str(content).strip()
            items.append(normalized)
    return items
