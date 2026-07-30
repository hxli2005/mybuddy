"""AI 评分器(AssessmentScorer)单元测试:白名单、越界拒绝、围栏剥离与异常兜底。"""

from __future__ import annotations

import pytest

from mybuddy.assessment.scoring import AssessmentScorer
from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolSpec


class ScriptedProvider(BaseLLMProvider):
    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls: list[dict] = []

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
        self.calls.append({"messages": list(messages), "model": model})
        text = self._texts.pop(0) if self._texts else "{}"
        return LLMResponse(text=text, finish_reason="stop")


def _scorer(text: str) -> AssessmentScorer:
    return AssessmentScorer(ScriptedProvider([text]), small_model="small")


@pytest.mark.asyncio
async def test_valid_phq9_score_accepted() -> None:
    scorer = _scorer('{"assessment_type": "phq9", "dimension_index": 1, "score": 2}')
    out = await scorer.try_score("最近一直提不起劲", pending_phq9_indices=[0, 1, 2])
    assert out == {"assessment_type": "phq9", "dimension_index": 1, "score": 2}


@pytest.mark.asyncio
async def test_valid_gad7_score_accepted_without_pending_whitelist() -> None:
    # 未提供 pending 列表时不做白名单过滤
    scorer = _scorer('{"assessment_type": "gad7", "dimension_index": 6, "score": 0}')
    out = await scorer.try_score("没什么好怕的")
    assert out == {"assessment_type": "gad7", "dimension_index": 6, "score": 0}


@pytest.mark.asyncio
async def test_rejects_dimension_not_in_pending_whitelist() -> None:
    scorer = _scorer('{"assessment_type": "phq9", "dimension_index": 3, "score": 2}')
    out = await scorer.try_score("好累", pending_phq9_indices=[0, 1])
    assert out is None


@pytest.mark.asyncio
async def test_whitelist_is_per_assessment_type() -> None:
    # gad7 的白名单不影响 phq9 结果
    scorer = _scorer('{"assessment_type": "gad7", "dimension_index": 2, "score": 1}')
    out = await scorer.try_score(
        "总在担心", pending_phq9_indices=[5], pending_gad7_indices=[2]
    )
    assert out == {"assessment_type": "gad7", "dimension_index": 2, "score": 1}


@pytest.mark.asyncio
async def test_none_assessment_type_returns_none() -> None:
    scorer = _scorer('{"assessment_type": "none", "dimension_index": -1, "score": -1}')
    assert await scorer.try_score("今天天气不错") is None


@pytest.mark.asyncio
async def test_rejects_out_of_range_dimension_index() -> None:
    # gad7 最大维度 6;phq9 最大 8;负数一律拒绝
    for payload in (
        '{"assessment_type": "gad7", "dimension_index": 7, "score": 1}',
        '{"assessment_type": "phq9", "dimension_index": 9, "score": 1}',
        '{"assessment_type": "phq9", "dimension_index": -2, "score": 1}',
    ):
        assert await _scorer(payload).try_score("x") is None


@pytest.mark.asyncio
async def test_rejects_out_of_range_or_non_int_score() -> None:
    for payload in (
        '{"assessment_type": "phq9", "dimension_index": 1, "score": 4}',
        '{"assessment_type": "phq9", "dimension_index": 1, "score": -1}',
        '{"assessment_type": "phq9", "dimension_index": 1, "score": "2"}',
    ):
        assert await _scorer(payload).try_score("x") is None


@pytest.mark.asyncio
async def test_rejects_unknown_assessment_type() -> None:
    scorer = _scorer('{"assessment_type": "phq15", "dimension_index": 1, "score": 1}')
    assert await scorer.try_score("x") is None


@pytest.mark.asyncio
async def test_strips_code_fence_before_parsing() -> None:
    scorer = _scorer(
        '```json\n{"assessment_type": "phq9", "dimension_index": 2, "score": 3}\n```'
    )
    out = await scorer.try_score("整晚睡不着", pending_phq9_indices=[2])
    assert out == {"assessment_type": "phq9", "dimension_index": 2, "score": 3}


@pytest.mark.asyncio
async def test_invalid_json_returns_none() -> None:
    scorer = _scorer("这不是 JSON")
    assert await scorer.try_score("x") is None


@pytest.mark.asyncio
async def test_provider_exception_returns_none() -> None:
    class BrokenProvider(BaseLLMProvider):
        async def generate(self, *a, **k):
            raise RuntimeError("boom")

    scorer = AssessmentScorer(BrokenProvider())
    assert await scorer.try_score("x") is None
