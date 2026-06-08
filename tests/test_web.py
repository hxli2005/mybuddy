"""`mybuddy web`(单用户标准库 HTTP 服务)回归测试。

核心:每个 /api/chat 请求不再用 `asyncio.run` 起一次性 loop —— 否则 agent 在对话里
fire-and-forget 起的后台记忆抽取会在回复返回时被随 loop 一起取消,网页对话永远学不到
新事实。改为投递到 DemoServer 的常驻后台 loop,回复返回后后台 task 仍能跑完。
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from mybuddy.agent import Agent
from mybuddy.api import AppState
from mybuddy.config import Config
from mybuddy.learning import TrajectoryLogger
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolSpec
from mybuddy.memory import MemoryManager, ShortTermMemory, UserProfile
from mybuddy.storage import init_db
from mybuddy.tools import ToolRegistry, set_context
from mybuddy.web import DemoHandler, DemoServer


class _OneShotProvider(BaseLLMProvider):
    """直接收敛的假 provider,一轮回一句话。"""

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
        return LLMResponse(text="好的~", finish_reason="stop")


def _build_state(tmp_path: Path, marker: Path, *, extract_delay: float) -> AppState:
    """组装一个带真实 Agent 的 AppState;抽取被替换成‘睡一会儿再写盘’的探针。

    抽取需要异步等待(模拟 small-model 往返),且耗时长于一次回复 —— 这样若它在回复返回
    时被取消,marker 就永远不会出现;只有后台 task 真的跑完了 marker 才会写出。
    """
    cfg = Config()
    cfg.memory.extract_after_turns = 1  # 一轮即触发抽取
    engine = init_db(str(tmp_path / "web.db"))
    set_context(engine=engine, config=cfg)
    provider = _OneShotProvider()

    mm = MemoryManager.__new__(MemoryManager)
    mm._engine = engine
    mm._config = cfg
    mm._ltm = None
    mm._provider = provider
    mm._session_id = "web-test"
    mm._short_term = ShortTermMemory(capacity=cfg.memory.short_term_size)
    mm._profile = UserProfile(engine, None)
    mm._extractor = None
    mm._recent_turns = []
    mm._recent_turn_ids = []
    mm._turns_since_extract = 0
    mm.build_context_section = lambda _user_input: ""  # type: ignore[method-assign]

    async def _fake_run_extract(turns: list[str], turn_ids: list[str]) -> bool:
        import asyncio

        await asyncio.sleep(extract_delay)
        marker.write_text("extracted", encoding="utf-8")
        return True

    mm.run_extract = _fake_run_extract  # type: ignore[method-assign]

    agent = Agent(
        provider=provider,
        config=cfg,
        registry=ToolRegistry(),
        memory=mm,
        trajectory_logger=TrajectoryLogger(tmp_path / "traj"),
        engine=engine,
    )

    state = AppState(config_path="config.yaml")
    state.cfg = cfg
    state.engine = engine
    state.provider = provider
    state.agent = agent
    return state


def _post_chat(host: str, port: int, message: str) -> dict[str, Any]:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request(
            "POST",
            "/api/chat",
            body=json.dumps({"message": message}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 200, f"status={resp.status} body={body}"
        return json.loads(body)
    finally:
        conn.close()


def test_web_chat_background_extraction_survives_reply(tmp_path: Path) -> None:
    """回复返回后,后台记忆抽取仍在常驻 loop 上跑完并写盘(不被随请求取消)。"""
    marker = tmp_path / "extracted.txt"
    state = _build_state(tmp_path, marker, extract_delay=0.05)
    server = DemoServer(("127.0.0.1", 0), DemoHandler, state=state, frontend_dir=tmp_path)
    host, port = server.server_address[0], server.server_address[1]
    serve_thread = threading.Thread(target=server.serve_forever, daemon=True)
    serve_thread.start()
    try:
        payload = _post_chat(host, port, "记住我喜欢猫")
        assert payload["text"]  # 拿到了回复

        # 回复此刻已返回,但后台抽取(睡 0.05s)还在跑。轮询等它写盘:
        # 旧的 asyncio.run-per-request 路径下,task 在回复返回时被取消,marker 永不出现。
        deadline = time.time() + 3.0
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "后台记忆抽取应在回复返回后继续跑完并写盘"
    finally:
        server.shutdown()
        serve_thread.join(timeout=5)
        server.server_close()


def test_web_server_close_drains_inflight_extraction(tmp_path: Path) -> None:
    """关闭服务时会把仍在途的后台抽取跑完,再停 loop。"""
    marker = tmp_path / "drained.txt"
    # 抽取故意拉长:回复返回后它必然仍在途,只能靠 server_close 的 drain 等它收尾。
    state = _build_state(tmp_path, marker, extract_delay=0.4)
    server = DemoServer(("127.0.0.1", 0), DemoHandler, state=state, frontend_dir=tmp_path)
    host, port = server.server_address[0], server.server_address[1]
    serve_thread = threading.Thread(target=server.serve_forever, daemon=True)
    serve_thread.start()
    try:
        _post_chat(host, port, "记住我喜欢猫")
        assert not marker.exists(), "0.4s 的抽取此刻应仍在途(尚未写盘)"
    finally:
        server.shutdown()
        serve_thread.join(timeout=5)
        server.server_close()  # 这里 drain 应把在途抽取等完

    assert marker.exists(), "server_close 应在关闭前把在途后台抽取跑完"


@pytest.mark.asyncio
async def test_chat_payload_spawns_background_extract(tmp_path: Path) -> None:
    """单元层面:一轮 chat_payload 后,agent 确实挂了一个后台抽取 task。"""
    marker = tmp_path / "spawned.txt"
    state = _build_state(tmp_path, marker, extract_delay=0.01)

    await state.chat_payload("记住我喜欢猫")

    bg_tasks = state.agent._bg_tasks
    assert bg_tasks, "达到抽取阈值时应 spawn 后台抽取 task"
    import asyncio

    await asyncio.gather(*list(bg_tasks))
    assert marker.exists()
