"""LLM 事实抽取器:从对话中抽取值得长期记忆的内容。

借鉴 mem0 的自动抽取思路:
  - 每 N 轮对话后,把最近的消息交给 LLM,让它判断哪些信息"值得记住"
  - 输出三类:长期记忆片段、用户画像字段更新、动态命题候选

抽取结果:
  - facts: 要写入长期记忆的文本片段
  - profile_fields: {"key": "value"} 类型的字段更新
  - claims: [{"claim": ..., "confidence": float}] 命题候选

注意:抽取是"可能产生幻觉"的操作(M3 无法完全消除,属于已知风险),
因此新增字段/命题的置信度都较低,需要后续证据持续增强。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mybuddy.llm import BaseLLMProvider

RELATIONSHIP_MEMORY_TYPES = (
    "shared_moment",
    "open_thread",
    "private_code",
    "anti_preference",
    "relationship_note",
    "character_note",
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
        "triggers": ["相关触发词"],
        "confidence": 0.7
      }
    ],
    "private_code": [],
    "anti_preference": [],
    "relationship_note": [],
    "character_note": []
  }
}

规则:
- facts:从对话中提取明确陈述的事实(如"用户叫小明""用户喜欢美式咖啡")。无则返回空数组。
- profile_fields:可确定为真的用户属性(如名字、生日、饮食偏好、过敏信息)。无则返回空对象。
  字段名用中文简写(如"名字""生日""咖啡偏好""过敏"等)。
- claims:不确定但值得追踪的推测,confidence 0.3-0.7。例如"用户似乎偏爱简洁直接的沟通方式"。
  每条 claim 10-30 字,confidence 必须给。
- relationship_memories:
  - shared_moment:用户和 AI 之间形成的共同经历、回忆卡、有效陪伴片段。
  - open_thread:未来有明确由头可回访的未完成话题;必须有具体 evidence,不要泛泛关心。
  - private_code:用户和 AI 形成的暗号或特殊说法。
  - anti_preference:用户明确不喜欢的回应方式。
  - relationship_note:关于当前关系质感、边界、默契的稳定线索。
  - character_note:AI 角色侧表达习惯或生活状态线索,只能来自对话中已经建立的设定。
- 不要编造对话中未出现的内容。
- 事实和画像只针对用户(USER);relationship_memories 可以抽取用户与 AI 的互动结果,但必须有对话证据。
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

        conversation = "\n".join(recent_messages)
        user_prompt = f"对话:\n{conversation}\n\n请提取信息,只输出 JSON:"

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
            out[mem_type].extend(_normalize_memory_items(raw.get(mem_type)))
    # 兼容模型把新字段直接放顶层的情况。
    for mem_type in RELATIONSHIP_MEMORY_TYPES:
        out[mem_type].extend(_normalize_memory_items(data.get(mem_type)))
    return out


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
