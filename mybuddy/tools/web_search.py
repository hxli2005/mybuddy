"""web_search 工具:DuckDuckGo HTML 端点。

为什么不用 BeautifulSoup:多一个依赖,且 DDG 的 HTML 足够规则 —— 用正则抓
`<a class="result__a" href="..."`、snippet、title 三块就够了。若将来 DDG
改版就回退 `results: []` 不让对话崩。

缓存:60s TTL 的 in-memory dict。MVP 单进程够用。
"""

from __future__ import annotations

import html
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx

from .context import get_config
from .registry import tool

logger = logging.getLogger(__name__)


DDG_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
CACHE_TTL_SEC = 60


_RESULT_BLOCK_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class _CacheEntry:
    at: float
    value: list[dict[str, str]] = field(default_factory=list)


_cache: dict[str, _CacheEntry] = {}


def _clean(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _decode_ddg_redirect(href: str) -> str:
    """DDG 会把结果链接包成 `//duckduckgo.com/l/?uddg=<encoded>`,解码回真 URL。"""
    if "uddg=" not in href:
        if href.startswith("//"):
            return "https:" + href
        return href
    parsed = urllib.parse.urlparse(href if href.startswith("http") else "https:" + href)
    qs = urllib.parse.parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return urllib.parse.unquote(qs["uddg"][0])
    return href


def _parse_results(html_text: str, max_results: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in _RESULT_BLOCK_RE.finditer(html_text):
        href_raw, title_raw, snippet_raw = m.group(1), m.group(2), m.group(3)
        out.append(
            {
                "url": _decode_ddg_redirect(html.unescape(href_raw)),
                "title": _clean(title_raw),
                "snippet": _clean(snippet_raw),
            }
        )
        if len(out) >= max_results:
            break
    return out


@tool(
    name="web_search",
    description=(
        "在 DuckDuckGo 上搜索网页,返回前若干条结果(标题/URL/摘要)。"
        "适合用户问最近的新闻、事实性问题、需要外部资料时使用。"
    ),
)
async def web_search(query: str, max_results: int = 5) -> dict:
    """Web 搜索。

    参数:
      query: 搜索关键词,自然语言
      max_results: 返回条数上限(默认 5)
    """
    query = (query or "").strip()
    if not query:
        return {"query": "", "results": [], "error": "query 为空"}

    cfg = get_config()
    max_results = min(
        max_results or cfg.tools.web_search_max_results,
        cfg.tools.web_search_max_results,
    )

    # 缓存命中
    now = time.time()
    cached = _cache.get(query)
    if cached and (now - cached.at) < CACHE_TTL_SEC:
        return {"query": query, "results": cached.value[:max_results], "cached": True}

    try:
        async with httpx.AsyncClient(
            timeout=cfg.tools.http_timeout,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.post(DDG_URL, data={"q": query})
            resp.raise_for_status()
            results = _parse_results(resp.text, max_results)
    except httpx.HTTPError as e:
        logger.warning("web_search 失败: %s", e)
        return {"query": query, "results": [], "error": f"{type(e).__name__}: {e}"}

    _cache[query] = _CacheEntry(at=now, value=results)
    return {"query": query, "results": results}


def _clear_cache_for_tests() -> None:
    """测试用:清空缓存。"""
    _cache.clear()


__all__ = ["web_search"]


# 对内导出,方便测试
_internals: dict[str, Any] = {
    "parse_results": _parse_results,
    "decode_ddg_redirect": _decode_ddg_redirect,
    "clear_cache": _clear_cache_for_tests,
}
