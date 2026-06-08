from __future__ import annotations

import json

from mybuddy.agent.search import (
    SearchDecision,
    build_search_context,
    classify_search_need,
    extract_search_sources,
)


def test_classify_search_need_distinguishes_news_from_personal_context() -> None:
    assert classify_search_need("查一下 OpenAI 最近有什么新闻").level == "must"
    assert classify_search_need("现在 OpenAI CEO 是谁?").level == "must"
    assert classify_search_need("这个产品口碑怎么样?").level == "should"
    assert classify_search_need("今天我好累,陪我一下").level == "none"
    assert classify_search_need("帮我写一段开场白").level == "none"


def test_classify_search_need_lowers_threshold_for_interest_topics() -> None:
    topics = ["恋与深空"]

    detail = classify_search_need("恋与深空那张卡剧情讲什么", interest_topics=topics)
    latest = classify_search_need("恋与深空最近新卡怎么样", interest_topics=topics)
    feeling = classify_search_need("我觉得恋与深空好戳我", interest_topics=topics)

    assert detail.level == "should"
    assert detail.topic == "恋与深空"
    assert latest.level == "must"
    assert latest.topic == "恋与深空"
    assert feeling.level == "none"


def test_companion_guard_wins_over_interest_recency() -> None:
    # 兴趣话题 + 时近词,但整体是情绪陪伴语气:陪伴守卫应优先返回 none,
    # 而不是因为"兴趣 + 最近"抢先判成 must(回归 bug:守卫曾被排在兴趣分支之后而永不可达)。
    decision = classify_search_need("我最近在玩原神好累", interest_topics=["原神"])
    assert decision.level == "none"


def test_high_stakes_still_searches_even_in_emotional_tone() -> None:
    # 高风险事实即使带情绪语气也要核验,优先级高于陪伴守卫。
    decision = classify_search_need("我好焦虑,最近用药方案要不要调整")
    assert decision.level == "must"


def test_build_search_context_instructs_uncertainty_when_results_empty() -> None:
    context = build_search_context(
        SearchDecision("must", "新闻或热点问题"),
        query="某事件最新消息",
        result_text=json.dumps({"query": "某事件最新消息", "results": [], "error": "network down"}),
    )

    assert "外部资料检索" in context
    assert "检索错误:network down" in context
    assert "不能装作已经确认" in context
    assert "不要说“搜索那边”" in context


def test_extract_search_sources_returns_user_facing_source_cards() -> None:
    sources = extract_search_sources(
        json.dumps(
            {
                "results": [
                    {
                        "title": "官方公告",
                        "url": "https://example.com/news",
                        "snippet": "活动将于本周开启。",
                        "date": "2026-06-04",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )

    assert sources == [
        {
            "title": "官方公告",
            "url": "https://example.com/news",
            "snippet": "活动将于本周开启。",
            "date": "2026-06-04",
        }
    ]
