"""Search intent routing and prompt context formatting."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

SearchLevel = Literal["none", "should", "must"]


@dataclass(frozen=True)
class SearchDecision:
    level: SearchLevel
    reason: str = ""
    topic: str = ""


_EXPLICIT_SEARCH_RE = re.compile(
    r"(查一下|搜一下|搜索|上网|网上.*说|帮我看看|帮我查|look\s*up|search)",
    re.I,
)
_SOURCE_REQUEST_RE = re.compile(
    r"(来源|出处|链接|引用|官网|官方说法|官方公告|source|citation|link|official)",
    re.I,
)
_NEWS_RE = re.compile(r"(新闻|热搜|热点|头条|发生了什么|怎么回事|最新消息)", re.I)
_RECENCY_RE = re.compile(
    r"(最新|最近|现在|目前|当前|今天|昨天|刚刚|实时|今年|近况|latest|current|recent|today)",
    re.I,
)
_TIME_SENSITIVE_RE = re.compile(
    r"(政策|法规|法律|价格|股价|汇率|利率|版本|发布|更新|上市|发布会|比赛|赛程|比分|"
    r"榜单|票房|公司|融资|裁员|CEO|总裁|总统|总理|首相|部长|产品|模型|论文|事故|"
    r"争议|口碑|评价|趋势)",
    re.I,
)
_HIGH_STAKES_RE = re.compile(
    r"(医疗|用药|药物|症状|诊断|法律|法规|签证|移民|税务|金融|投资|股票|基金|保险|贷款)",
    re.I,
)
_PERSONAL_COMPANION_RE = re.compile(
    r"(我|我的|我们|你|小布).{0,8}(累|难受|焦虑|烦|崩溃|开心|想你|陪我|睡不着|压力|害怕|委屈|记得|提醒|写|整理)",
    re.I,
)
_CREATIVE_RE = re.compile(r"(写一段|改写|润色|翻译|起名|生成|设计|构思|帮我写)", re.I)
_INTEREST_FACT_RE = re.compile(
    r"(剧情|设定|角色|卡牌|卡池|活动|机制|规则|攻略|技能|数值|时间线|结局|主线|支线|"
    r"版本|更新|声优|作者|演员|阵容|参数|性能|价格|口碑|评价|排名|是什么|讲什么|"
    r"怎么|哪里|哪张|哪个|有没有|值得)",
    re.I,
)
_INTEREST_RECENCY_RE = re.compile(
    r"(最新|最近|现在|目前|当前|今天|昨天|刚刚|实时|今年|新卡|限时|联动|新版本)",
    re.I,
)


def classify_search_need(
    text: str,
    *,
    interest_topics: list[str] | None = None,
) -> SearchDecision:
    clean = " ".join((text or "").strip().split())
    if not clean:
        return SearchDecision("none")

    if _EXPLICIT_SEARCH_RE.search(clean):
        return SearchDecision("must", "用户明确要求搜索")
    if _SOURCE_REQUEST_RE.search(clean):
        return SearchDecision("must", "用户要求来源或链接")
    if _NEWS_RE.search(clean):
        return SearchDecision("must", "新闻或热点问题")

    has_recency = bool(_RECENCY_RE.search(clean))
    has_time_sensitive_topic = bool(_TIME_SENSITIVE_RE.search(clean))
    has_high_stakes = bool(_HIGH_STAKES_RE.search(clean))

    # 高风险事实(医疗/法律/金融等)即使夹在情绪语气里也要核验,优先级最高。
    if has_high_stakes and (has_recency or has_time_sensitive_topic):
        return SearchDecision("must", "高风险事实需要核验")

    # 情绪陪伴 / 创作类消息不联网检索。必须放在兴趣与时效启发式之前,否则下面的
    # 兴趣/时效分支会抢先返回 must,使这条守卫永远不可达。
    if _PERSONAL_COMPANION_RE.search(clean) or _CREATIVE_RE.search(clean):
        return SearchDecision("none")

    matched_interest = _match_interest_topic(clean, interest_topics or [])
    if matched_interest:
        if has_recency or _INTEREST_RECENCY_RE.search(clean):
            return SearchDecision("must", "用户兴趣话题的时效信息需要核验", matched_interest)
        if has_time_sensitive_topic or _INTEREST_FACT_RE.search(clean) or _looks_like_question(clean):
            return SearchDecision("should", "用户兴趣话题中的事实细节需要校验", matched_interest)

    if has_recency and has_time_sensitive_topic:
        return SearchDecision("must", "时效性事实问题")
    if has_time_sensitive_topic and _looks_like_question(clean):
        return SearchDecision("should", "事实可能随时间变化")

    return SearchDecision("none")


def may_use_interest_topics(text: str) -> bool:
    """判断是否值得收集用户兴趣话题。

    兴趣话题只有在消息带有时效、时间敏感主题、兴趣事实或提问标记时,才可能把
    检索判定从 none 提升到 should/must。否则可以跳过较重的兴趣话题收集
    (会遍历画像/命题并读取全部长期记忆卡片)。
    """
    clean = " ".join((text or "").strip().split())
    if not clean:
        return False
    return bool(
        _RECENCY_RE.search(clean)
        or _INTEREST_RECENCY_RE.search(clean)
        or _TIME_SENSITIVE_RE.search(clean)
        or _INTEREST_FACT_RE.search(clean)
        or _looks_like_question(clean)
    )


def build_search_context(
    decision: SearchDecision,
    *,
    query: str,
    result_text: str,
    max_items: int = 5,
) -> str:
    data = _safe_json(result_text)
    results = data.get("results")
    if not isinstance(results, list):
        results = []
    error = data.get("error")
    query = str(data.get("query") or query).strip()

    lines = [
        "## 外部资料检索",
        f"- 触发级别:{decision.level}",
        f"- 触发原因:{decision.reason or '需要核验现实世界信息'}",
        f"- 搜索词:{query}",
        "",
        "回答要求:",
        "- 优先依据下面的搜索结果回答,不要凭旧知识补充未被结果支持的新闻细节。",
        "- 如果结果为空、报错或互相冲突,要明确说目前无法可靠确认。",
        "- 可以自然地说“我刚看了几条结果”,但不要暴露工具参数或调试信息。",
        "- 不要说“搜索那边”“工具”“系统”这类后台表述;没查到时说“我这边没查到可靠结果”。",
        "- 涉及政策、医疗、法律、金融等高风险内容时,提醒用户以官方或专业来源为准。",
        "- 涉及用户兴趣话题时,优先官方/Wiki/权威资料;社区反馈只能当作讨论或口碑,不要当成事实。",
        "",
        "搜索结果:",
    ]
    if decision.topic:
        lines.insert(4, f"- 命中兴趣话题:{decision.topic}")

    if error:
        lines.append(f"- 检索错误:{error}")
    if not results:
        lines.append("- 没有拿到可用结果。不能装作已经确认。")
        return "\n".join(lines)

    for index, item in enumerate(results[:max_items], start=1):
        if not isinstance(item, dict):
            continue
        title = _clip(str(item.get("title") or "无标题"), 120)
        url = _clip(str(item.get("url") or ""), 240)
        snippet = _clip(str(item.get("snippet") or ""), 220)
        lines.append(f"{index}. {title}")
        if url:
            lines.append(f"   链接:{url}")
        if snippet:
            lines.append(f"   摘要:{snippet}")
    return "\n".join(lines)


def build_unavailable_search_context(decision: SearchDecision, *, query: str) -> str:
    lines = [
        "## 外部资料检索",
        f"- 触发级别:{decision.level}",
        f"- 触发原因:{decision.reason or '需要核验现实世界信息'}",
        f"- 搜索词:{query.strip()}",
    ]
    if decision.topic:
        lines.append(f"- 命中兴趣话题:{decision.topic}")
    lines.extend(
        [
            "- 检索状态:web_search 工具不可用。",
            "",
            "回答要求:",
            "- 不要凭旧知识回答最新新闻、价格、政策、职位变动或高风险事实。",
            "- 不要说“搜索那边”“工具”“系统”这类后台表述;可以说“我这边没查到可靠结果”,并请用户提供链接或稍后再试。",
        ]
    )
    return "\n".join(lines)


def search_result_count(result_text: str) -> int:
    results = _safe_json(result_text).get("results")
    return len(results) if isinstance(results, list) else 0


def extract_search_sources(result_text: str, *, max_items: int = 5) -> list[dict[str, str]]:
    results = _safe_json(result_text).get("results")
    if not isinstance(results, list):
        return []
    sources: list[dict[str, str]] = []
    for item in results[:max_items]:
        if not isinstance(item, dict):
            continue
        title = _clip(str(item.get("title") or "无标题"), 120)
        url = _clip(str(item.get("url") or ""), 260)
        snippet = _clip(str(item.get("snippet") or ""), 180)
        date = _clip(str(item.get("date") or ""), 40)
        if not url and not title:
            continue
        source = {"title": title, "url": url, "snippet": snippet}
        if date:
            source["date"] = date
        sources.append(source)
    return sources


def _match_interest_topic(text: str, topics: list[str]) -> str:
    normalized_text = _normalize_topic(text)
    for topic in topics:
        clean_topic = topic.strip()
        if len(clean_topic) < 2:
            continue
        normalized_topic = _normalize_topic(clean_topic)
        if normalized_topic and normalized_topic in normalized_text:
            return clean_topic
    return ""


def _normalize_topic(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _looks_like_question(text: str) -> bool:
    return any(mark in text for mark in ("?", "？", "吗", "如何", "怎么样", "是什么", "有没有", "值得"))


def _safe_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _clip(text: str, limit: int) -> str:
    clean = " ".join(text.strip().split())
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "..."
