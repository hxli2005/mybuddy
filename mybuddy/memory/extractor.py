"""LLM 事实抽取器:从对话中抽取值得长期记忆的内容。

借鉴 mem0 的自动抽取思路:
  - 每 N 轮对话后,把最近的消息交给 LLM,让它判断哪些信息"值得记住"
  - 输出明确事实、用户画像字段和少量核心关系记忆

抽取结果:
  - facts: 要写入 profile 记忆卡的明确事实
  - profile_fields: {"key": "value"} 类型的字段更新
  - claims: 少量后台候选观察,默认应为空

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
  "claims": [
    {"claim": "关于用户的推测性命题", "confidence": 0.6}
  ],
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
  }
}

规则:
- facts:从对话中提取明确陈述的事实(如"用户叫小明""用户喜欢美式咖啡")。无则返回空数组。
- profile_fields:可确定为真的用户属性(如名字、生日、饮食偏好、过敏信息)。无则返回空对象。
  字段名用中文简写(如"名字""生日""咖啡偏好""过敏"等)。
- claims:默认返回空数组。只有同类线索在这段对话中反复出现、但还不足以写成事实时才输出。
  不要把一次性的情绪、拖延、疲惫写成长期命题。
- relationship_memories:
  - 只使用 preference/shared_moment/open_thread 三类。不要新增其他类型。
  - preference:用户明确表达的稳定偏好、避雷、喜欢/不喜欢的回应方式。
  - shared_moment:用户和 AI 之间形成的共同经历、回忆卡、有效陪伴片段。
  - open_thread:未来有明确由头可回访的未完成话题;必须有具体 evidence,不要泛泛关心。
    如果能判断截止或过期时间,填写 expires_at;如果能判断事件发生时间,填写 event_time。
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
        claims: list[dict[str, Any]] | None = None,
        relationship_memories: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.facts: list[str] = facts or []
        self.profile_fields: dict[str, str] = profile_fields or {}
        self.claims: list[dict[str, Any]] = claims or []
        self.relationship_memories: dict[str, list[dict[str, Any]]] = (
            relationship_memories or {k: [] for k in RELATIONSHIP_MEMORY_TYPES}
        )

    def is_empty(self) -> bool:
        return (
            not self.facts
            and not self.profile_fields
            and not self.claims
            and not any(self.relationship_memories.values())
        )

    def __repr__(self) -> str:
        return (
            f"FactExtractResult(facts={len(self.facts)}, "
            f"fields={len(self.profile_fields)}, claims={len(self.claims)}, "
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
            claims=_dict_list(data.get("claims")),
            relationship_memories=_relationship_memories(data),
        )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从可能夹杂了 markdown 代码块的文本中提取 JSON 对象。"""
    # 去掉 ```json ... ``` 包裹
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean = "\n".join(lines)

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return None


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(k).strip(): str(v).strip()
        for k, v in value.items()
        if str(k).strip() and str(v).strip()
    }


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


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
