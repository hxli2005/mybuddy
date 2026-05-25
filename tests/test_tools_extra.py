"""M7 新工具测试:weather 真 API / translate / web_search / notes / list_skills。

HTTP 工具用 httpx MockTransport 注入伪响应,不发真实网络。
LLM 工具用最小 ScriptedProvider。
长期记忆复用 test_memory.mock_embed。
"""

from __future__ import annotations

import json

import httpx
import pytest

from mybuddy.config import Config
from mybuddy.learning import SkillRegistry
from mybuddy.llm import LLMResponse, Message, ToolSpec
from mybuddy.memory import LongTermMemory
from mybuddy.storage import Note, init_db, session_scope
from mybuddy.tools import (
    set_context,
    setup_memory_tool,
    setup_skill_tool,
)
from mybuddy.tools.context import reset as reset_ctx

from .test_memory import mock_embed

# =============================================================================
# weather — MockTransport
# =============================================================================


def _weather_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"name": "北京", "latitude": 39.9, "longitude": 116.4},
                    ]
                },
            )
        # forecast
        return httpx.Response(
            200,
            json={
                "current": {
                    "temperature_2m": 18.5,
                    "relative_humidity_2m": 55,
                    "wind_speed_10m": 12.3,
                    "weather_code": 2,
                },
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_weather_real_api(monkeypatch) -> None:
    cfg = Config()
    cfg.tools.weather_mock = False
    set_context(config=cfg)

    transport = _weather_transport()

    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    from mybuddy.tools.weather import weather

    result = await weather("北京")
    assert result["city"] == "北京"
    assert result["temperature_c"] == 18.5
    assert result["condition"] == "局部多云"  # weather_code=2


@pytest.mark.asyncio
async def test_weather_known_city_skips_geocoding(monkeypatch) -> None:
    cfg = Config()
    cfg.tools.weather_mock = False
    set_context(config=cfg)

    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        assert "geocoding" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "current": {
                    "temperature_2m": 21,
                    "relative_humidity_2m": 50,
                    "wind_speed_10m": 9,
                    "weather_code": 1,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    from mybuddy.tools.weather import weather

    result = await weather("北京天气")
    assert result["city"] == "北京"
    assert result["condition"] == "大体晴朗"
    assert len(seen_urls) == 1


@pytest.mark.asyncio
async def test_weather_fallback_on_404(monkeypatch) -> None:
    cfg = Config()
    cfg.tools.weather_mock = False
    set_context(config=cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    from mybuddy.tools.weather import weather

    result = await weather("Atlantis")
    assert result["city"] == "Atlantis"
    assert "fallback" in result["note"]


@pytest.mark.asyncio
async def test_weather_mock_mode() -> None:
    cfg = Config()
    cfg.tools.weather_mock = True
    set_context(config=cfg)

    from mybuddy.tools.weather import weather

    result = await weather("北京")
    assert "mock 模式" in result["note"]


# =============================================================================
# translate
# =============================================================================


class _SimpleScripted:
    """最小 provider:只需 .generate 可 await。"""

    def __init__(self, text: str) -> None:
        self._text = text
        self.last_system: str | None = None

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        self.last_system = system
        return LLMResponse(text=self._text, finish_reason="stop")


@pytest.mark.asyncio
async def test_translate_happy_path() -> None:
    cfg = Config()
    provider = _SimpleScripted("Hello, world!")
    set_context(config=cfg, provider=provider)

    from mybuddy.tools.translate import translate

    result = await translate("你好,世界!", target_lang="英文")
    assert result["ok"] is True
    assert result["translated"] == "Hello, world!"
    assert result["target_lang"] == "英文"
    assert "只输出译文" in provider.last_system


@pytest.mark.asyncio
async def test_translate_empty_input() -> None:
    cfg = Config()
    provider = _SimpleScripted("N/A")
    set_context(config=cfg, provider=provider)

    from mybuddy.tools.translate import translate

    result = await translate("   ")
    assert result["ok"] is False


# =============================================================================
# web_search
# =============================================================================

_DDG_HTML_SAMPLE = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Title A</a>
  <a class="result__snippet" href="#">This is the snippet for result A.</a>
</div>
<div class="result">
  <a class="result__a" href="https://example.com/b">Title <b>B</b></a>
  <a class="result__snippet" href="#">Snippet for <i>B</i>.</a>
</div>
<div class="result">
  <a class="result__a" href="https://example.com/c">Title C</a>
  <a class="result__snippet" href="#">Snippet C.</a>
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_web_search_parses_results(monkeypatch) -> None:
    from mybuddy.tools import web_search as mod

    mod._internals["clear_cache"]()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_DDG_HTML_SAMPLE)

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    cfg = Config()
    cfg.tools.web_search_max_results = 5
    set_context(config=cfg)

    result = await mod.web_search("hello", max_results=5)
    assert result["query"] == "hello"
    assert len(result["results"]) == 3
    # DDG 重定向链接应被还原
    assert result["results"][0]["url"] == "https://example.com/a"
    # 第二条的 title 含 HTML 标签,应被剥掉
    assert result["results"][1]["title"] == "Title B"
    assert result["results"][1]["snippet"] == "Snippet for B."


@pytest.mark.asyncio
async def test_web_search_network_failure(monkeypatch) -> None:
    from mybuddy.tools import web_search as mod

    mod._internals["clear_cache"]()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    cfg = Config()
    set_context(config=cfg)

    result = await mod.web_search("xyz")
    assert result["results"] == []
    assert "ConnectError" in result["error"]


def test_web_search_decode_ddg_redirect() -> None:
    from mybuddy.tools.web_search import _decode_ddg_redirect

    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fx%3Fq%3D1"
    assert _decode_ddg_redirect(href) == "https://example.com/x?q=1"
    # 已是 http(s) URL 原样返回
    assert _decode_ddg_redirect("https://a.com/b") == "https://a.com/b"


# =============================================================================
# notes
# =============================================================================


@pytest.fixture
def notes_env(tmp_path):
    reset_ctx()
    cfg = Config()
    engine = init_db(str(tmp_path / "notes.db"))
    chroma_dir = tmp_path / "chroma_notes"
    chroma_dir.mkdir()
    ltm = LongTermMemory(
        persist_dir=str(chroma_dir),
        collection_name="test_notes",
        embedding_fn=mock_embed,
    )
    set_context(engine=engine, config=cfg)
    setup_memory_tool(ltm)
    return engine, cfg, ltm


def test_write_note_creates_row_and_chroma(notes_env) -> None:
    engine, cfg, ltm = notes_env

    from mybuddy.tools.notes import write_note

    result = write_note("今天写完了 M7 的第一版", title="开发日记", tags=["mybuddy", "开发"])
    assert result["ok"] is True
    assert result["id"] > 0

    with session_scope(engine) as s:
        rows = s.query(Note).all()
        assert len(rows) == 1
        assert rows[0].title == "开发日记"
        tags = json.loads(rows[0].tags_json)
        assert "mybuddy" in tags

    # Chroma 也应有对应文档
    hits = ltm.search("M7", top_k=3, mem_type="note")
    assert len(hits) >= 1


def test_search_notes_semantic(notes_env) -> None:
    _, _, ltm = notes_env

    from mybuddy.tools.notes import search_notes, write_note

    write_note("周末去西湖骑车", title="计划", tags=["出行"])
    write_note("买一罐挂耳咖啡", title="购物清单")

    out = search_notes("西湖")
    data = json.loads(out)
    assert len(data) >= 1
    assert any("西湖" in d["content"] for d in data)
    # tags 应回填
    top = next(d for d in data if "西湖" in d["content"])
    assert "出行" in top["tags"]


def test_search_notes_empty_returns_message(notes_env) -> None:
    from mybuddy.tools.notes import search_notes

    assert "没有相关笔记" in search_notes("不存在的内容")


def test_write_note_empty_content(notes_env) -> None:
    from mybuddy.tools.notes import write_note

    assert write_note("   ")["ok"] is False


# =============================================================================
# list_skills
# =============================================================================


def test_list_skills_empty(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    setup_skill_tool(reg)

    from mybuddy.tools.skill_tool import list_skills

    out = list_skills()
    assert "没有积累" in out


def test_list_skills_returns_sorted(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    reg.create(name="低", triggers=["x"], steps=["a"], confidence=0.4)
    reg.create(name="高", triggers=["y"], steps=["b", "c"], confidence=0.8)
    reg.create(name="中", triggers=["z"], steps=["d"], confidence=0.6)
    setup_skill_tool(reg)

    from mybuddy.tools.skill_tool import list_skills

    data = json.loads(list_skills())
    assert [d["name"] for d in data] == ["高", "中", "低"]
    assert data[0]["confidence"] == 0.8
    assert "b; c" in data[0]["steps_preview"]


def test_list_skills_excludes_archived(tmp_path) -> None:
    reg = SkillRegistry(tmp_path)
    s = reg.create(name="归档的", triggers=["x"], steps=["a"], confidence=0.3)
    s.archived = True
    reg.save(s)
    reg.create(name="活跃的", triggers=["y"], steps=["b"], confidence=0.7)
    setup_skill_tool(reg)

    from mybuddy.tools.skill_tool import list_skills

    data = json.loads(list_skills())
    names = [d["name"] for d in data]
    assert "活跃的" in names
    assert "归档的" not in names
