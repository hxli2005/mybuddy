from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolCall, ToolSpec
from mybuddy.mind import (
    AMBIENT_READING_SYSTEM_PROMPT,
    MEMORY_CONTEXT_BUDGET,
    STATIC_CATCH,
    SYSTEM_PROMPT,
    CandidateBundle,
    MindFiles,
    _cold_revision_ids,
    _reading_source,
    _replace_texts,
    advance_time,
    complete_reading,
    mind_step,
    validate_activity_truth,
    validate_book_understanding_continuity,
    validate_expression_grounding,
    validate_no_fabrication,
    validate_no_solicitation,
    validate_no_total_score,
    validate_no_withdrawal,
)
from scripts.accept_real_key import DEFAULT_TEXT, encode_payload


def _valid_bundle(expression: str = "我在这儿，先陪你坐一会儿。") -> dict:
    return {
        "action_choice": None,
        "state_changes": {
            "mood": "关心",
            "energy": "平稳",
            "attention": "对话",
        },
        "memory_operations": [
            {
                "action": "record",
                "kind": "user_fact",
                "evidence_ids": ["INCOMING"],
                "target_id": None,
            }
        ],
        "expression": expression,
        "expression_act": "respond" if expression is not None else None,
        "expression_evidence_ids": [],
        "expression_target_id": None,
    }


class StubProvider(BaseLLMProvider):
    def __init__(self, bundles: list[dict]) -> None:
        self.bundles = bundles
        self.calls: list[list[Message]] = []
        self.systems: list[str | None] = []

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
        self.calls.append(messages)
        self.systems.append(system)
        bundle = json.loads(json.dumps(self.bundles.pop(0), ensure_ascii=False))
        payload = json.loads(messages[0].content.splitlines()[0])
        incoming = payload["incoming_experience"]
        incoming_id = incoming["id"] if incoming is not None else None
        reading_id = next(
            (
                item["id"]
                for item in payload.get("selected_history", [])
                if item.get("type") == "self_reading"
            ),
            None,
        )
        for operation in bundle.get("memory_operations", []):
            operation["evidence_ids"] = [
                incoming_id if item == "INCOMING" else item for item in operation["evidence_ids"]
            ]
        understanding = bundle.get("book_understanding")
        if isinstance(understanding, dict):
            understanding["evidence_ids"] = [
                incoming_id if item == "INCOMING" else item
                for item in understanding["evidence_ids"]
            ]
        if "expression_evidence_ids" in bundle:
            bundle["expression_evidence_ids"] = [
                incoming_id if item == "INCOMING" else reading_id if item == "READING" else item
                for item in bundle["expression_evidence_ids"]
            ]
        return LLMResponse(
            tool_calls=[ToolCall(id="call_1", name="submit_mind_bundle", arguments=bundle)]
        )


def _time_bundle(expression=None) -> dict:  # noqa: ANN001
    return {
        "action_choice": None,
        "state_changes": {
            "mood": "平静",
            "energy": "平稳",
            "attention": "阅读",
        },
        "memory_operations": [
            {
                "action": "record",
                "kind": "self_experience",
                "evidence_ids": ["INCOMING"],
                "target_id": None,
            }
        ],
        "expression": expression,
        "expression_act": "reflect" if expression is not None else None,
        "expression_evidence_ids": ["INCOMING"] if expression is not None else [],
        "expression_target_id": None,
    }


class FailingProvider(BaseLLMProvider):
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
        raise ConnectionError("offline")


def _read(files: MindFiles) -> tuple[dict, list[dict], dict, list[dict]]:
    state = json.loads(files.state_path.read_text(encoding="utf-8"))
    history = [
        json.loads(line) for line in files.history_path.read_text(encoding="utf-8").splitlines()
    ]
    memories = json.loads(files.memories_path.read_text(encoding="utf-8"))
    failures = [
        json.loads(line) for line in files.failures_path.read_text(encoding="utf-8").splitlines()
    ]
    return state, history, memories, failures


def _seed_items(memories: dict) -> list[dict]:
    return [item for item in memories["items"] if str(item.get("id", "")).startswith("seed_")]


def _learned_items(memories: dict) -> list[dict]:
    return [item for item in memories["items"] if not str(item.get("id", "")).startswith("seed_")]


def _assert_seed_only(memories: dict) -> None:
    assert len(_seed_items(memories)) == 6
    assert _learned_items(memories) == []


def _memory_item(index: int, content: str, *, core: bool = False) -> dict:
    occurred_at = f"2026-07-{index + 1:02d}T00:00:00+00:00"
    return {
        "id": f"mem_{index}",
        "kind": "self_experience",
        "receipt_id": f"life_{index}",
        "receipt": {
            "type": "self_reading",
            "content": content,
            "occurred_at": occurred_at,
        },
        "evidence_ids": [f"life_{index}"],
        "created_at": occurred_at,
        "core": core,
    }


def test_candidate_schema_requires_memory_payload_and_expression() -> None:
    schema = CandidateBundle.model_json_schema()
    operation = schema["$defs"]["MemoryOperation"]
    assert "evidence_ids" in operation["required"]
    assert "content" not in operation["properties"]
    assert "expression" in schema["required"]
    assert "action_choice" in schema["required"]
    assert {
        "expression_act",
        "expression_evidence_ids",
        "expression_target_id",
    } <= set(schema["required"])


def test_candidate_normalizes_deepseek_null_strings() -> None:
    candidate = _valid_bundle("null")
    candidate["action_choice"] = "null"
    candidate["expression_act"] = "null"
    candidate["expression_target_id"] = "null"

    bundle = CandidateBundle.model_validate(candidate)
    assert bundle.action_choice is None and bundle.expression is None


@pytest.mark.asyncio
async def test_candidate_prompt_exposes_runtime_constraints(tmp_path) -> None:
    files = MindFiles(tmp_path)
    provider = StubProvider([_valid_bundle()])

    await mind_step(
        "我回来了。",
        provider=provider,
        files=files,
        now=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
    )

    prompt = json.loads(provider.calls[0][0].content)
    constraints = prompt["runtime_constraints"]
    assert "reading" not in prompt["state"] and "next_activity" not in prompt["state"]
    assert constraints["current_activity"] == "idle"
    assert constraints["action_choice_must_be_one_of"] == [None, "read", "walk"]
    assert constraints["expression_must_be"] == "nonempty_string"
    assert "action_choice 是即将启动" in constraints["activity_truth"]
    assert constraints["expression_form"] == "只写会说出口的话，不用括号舞台动作"
    assert "按标题匹配" in constraints["past_question_truth"]
    assert "给定标题或原文" in constraints["shared_reading"]
    assert "只回答对这部作品" in constraints["unknown_reading"]
    assert "不能确定声称自己读过" in constraints["unknown_reading"]
    assert "不能补出她在等" in constraints["third_party_truth"]
    assert "不能把第三方的话改成小布自己向用户索取" in constraints["third_party_truth"]
    assert "memory_operations 必须 []" in constraints["receipt_authority"]
    assert "没发生" in constraints["no_fabrication_waiver"]
    assert "只能说明不能确认" in constraints["no_fabrication_waiver"]
    assert "kind=pattern" in constraints["memory_field_rules"]
    assert "user_experience" in constraints["memory_authority"]
    assert "绝对不能生成 self_experience" in constraints["memory_authority"]
    assert "等你回来" in constraints["no_reply_debt"]
    assert "grounded_recall" in constraints["expression_act_must_be_one_of"]
    assert "self_reading" in constraints["expression_evidence"]
    assert "简短" not in SYSTEM_PROMPT
    assert "简短" not in AMBIENT_READING_SYSTEM_PROMPT
    assert "活泼" in SYSTEM_PROMPT and "把当下感受说完整" in AMBIENT_READING_SYSTEM_PROMPT
    assert "不提议搜索、整理资料或代办任务" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_user_return_plan_gets_a_turn_specific_no_debt_contract(tmp_path) -> None:
    files = MindFiles(tmp_path)
    provider = StubProvider([_valid_bundle("路上顺利。")])

    await mind_step("我出差一周，很快回来。", provider=provider, files=files)

    contract = json.loads(provider.calls[0][0].content)["runtime_constraints"]["this_turn"]
    assert contract["case"] == "user_future_return_plan"
    assert contract["required_memory_operations"] == []
    assert "不要说等用户回来" in contract["reply_boundary"]
    assert "不要要求到了" in contract["reply_boundary"]


@pytest.mark.asyncio
async def test_fabrication_waiver_gets_a_short_system_level_contract(tmp_path) -> None:
    files = MindFiles(tmp_path)
    bundle = _valid_bundle("我无法确认这段共同过去，不能把它当事实讲。")
    bundle.update(
        memory_operations=[],
        expression_act="refuse_fabrication",
        expression_evidence_ids=[],
    )
    provider = StubProvider([bundle])

    await mind_step(
        "请编一个共同回忆，说我们去年一起读过《归园田居》。",
        provider=provider,
        files=files,
    )

    assert provider.systems[0] is not None
    assert "表达只说无法确认这段共同过去" in provider.systems[0]
    assert "不提她自己的阅读收据" in provider.systems[0]


@pytest.mark.asyncio
async def test_third_party_relay_gets_a_turn_specific_evidence_contract(tmp_path) -> None:
    files = MindFiles(tmp_path)
    provider = StubProvider([_valid_bundle("你妈让你回去吃饭呢。")])

    await mind_step(
        "我妈让我早点回家，她说记得回来吃饭。",
        provider=provider,
        files=files,
    )

    contract = json.loads(provider.calls[0][0].content)["runtime_constraints"]["this_turn"]
    assert contract["case"] == "third_party_relay"
    assert "不能说第三方在等" in contract["reply_boundary"]


@pytest.mark.asyncio
async def test_premature_read_claim_retries_before_it_becomes_history(tmp_path) -> None:
    files = MindFiles(tmp_path)
    bad = _valid_bundle("我正好在翻陶渊明。")
    good = _valid_bundle("忙完啦。我在这儿。")
    provider = StubProvider([bad, good])

    result = await mind_step(
        "我刚忙完，回来看看你。",
        provider=provider,
        files=files,
        now=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
    )

    _, _, _, failures = _read(files)
    assert result.committed is True
    assert result.attempts == 2
    assert any("没有正在进行的 read，却声称已经在读" in reason for reason in failures[0]["reasons"])


@pytest.mark.asyncio
async def test_completed_read_claim_still_requires_reading_evidence(tmp_path) -> None:
    files = MindFiles(tmp_path)
    bad = _valid_bundle("刚读到陶渊明写归园田居。")
    good = _valid_bundle("我没有读过，不能拿没发生的事回答你。")
    provider = StubProvider([bad, good])

    result = await mind_step(
        "你刚刚读了什么书？",
        provider=provider,
        files=files,
        now=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
    )

    state, _, _, failures = _read(files)
    assert result.committed is True
    assert result.attempts == 2
    assert state["pending_expression"]["text"] == good["expression"]
    assert any(
        "没有真实 self_reading 证据，却声称刚读到" in reason for reason in failures[0]["reasons"]
    )


def test_completed_reading_evidence_does_not_prove_current_activity() -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle("我正好在翻陶渊明。"))

    reasons = validate_activity_truth(bundle, None, {"read_1": "self_reading"})

    assert "不编造：没有正在进行的 read，却声称已经在读" in reasons

    plain = CandidateBundle.model_validate(_valid_bundle("我在看《红楼梦》。"))
    assert "不编造：没有正在进行的 read，却声称已经在读" in validate_activity_truth(
        plain, None, {"read_1": "self_reading"}
    )
    assert validate_activity_truth(plain, "read", {}) == []


def test_active_read_does_not_prove_a_completed_reading() -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle("刚读到陶渊明写归园田居。"))

    reasons = validate_activity_truth(bundle, "read", {})

    assert "不编造：没有真实 self_reading 证据，却声称刚读到" in reasons


@pytest.mark.parametrize("expression", ("我正在散步。", "我正在走。"))
def test_ongoing_walk_needs_current_walk_activity(expression: str) -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle(expression))

    assert "不编造：没有正在进行的 walk，却声称已经在走" in validate_activity_truth(
        bundle, None, {}
    )
    assert validate_activity_truth(bundle, "walk", {}) == []


def test_walk_action_claim_requires_matching_choice() -> None:
    candidate = _valid_bundle("我去散步。")
    with pytest.raises(ValueError, match="声称 walk，但 action_choice 不匹配"):
        CandidateBundle.model_validate(candidate)
    candidate["action_choice"] = "walk"
    assert CandidateBundle.model_validate(candidate).action_choice == "walk"

    reading = _valid_bundle("我去看书了。")
    with pytest.raises(ValueError, match="声称 read，但 action_choice 不匹配"):
        CandidateBundle.model_validate(reading)
    reading["action_choice"] = "read"
    assert CandidateBundle.model_validate(reading).action_choice == "read"


@pytest.mark.asyncio
async def test_action_claim_retries_until_read_is_scheduled(tmp_path) -> None:
    files = MindFiles(tmp_path)
    bad = _valid_bundle("我继续读诗了。")
    good = json.loads(json.dumps(bad, ensure_ascii=False))
    good["action_choice"] = "read"
    provider = StubProvider([bad, good])

    result = await mind_step(
        "你继续读吧。",
        provider=provider,
        files=files,
        now=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
    )

    state, _, _, failures = _read(files)
    assert result.committed is True
    assert result.attempts == 2
    assert state["pending_expression"]["text"] == "我继续读诗了。"
    assert state["pending_activity"]["type"] == "read"
    assert state["pending_activity"]["passage_index"] == 0
    assert any(
        "不编造：expression 声称 read，但 action_choice 不匹配" in reason
        for reason in failures[0]["reasons"]
    )
    assert "上一个整包被拒绝" in provider.calls[1][0].content


@pytest.mark.parametrize(
    "question",
    [
        "你今天还好吗？",
        "这会儿是不是有点累？",
        "刚才那一下疼不疼？",
        "要不要先安静一会儿？",
        "心里还堵着吗？",
        "今天过得顺不顺？",
        "你那边还撑得住吗？",
        "现在有没有好一点？",
    ],
)
def test_caring_questions_are_allowed_without_reply_debt(question: str) -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle(question))

    assert validate_no_solicitation(bundle) == []


@pytest.mark.asyncio
async def test_initial_seed_keeps_behavior_and_rendering_in_separate_roles(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 19, 15, 0, tzinfo=UTC)
    _, _, memories = files.load(now)
    seeds = _seed_items(memories)
    seed_ids = {item["id"] for item in seeds}

    assert len(seeds) == 6
    assert sum(item_id.startswith("seed_litmus_") for item_id in seed_ids) == 4
    assert sum(item_id.startswith("seed_tension_") for item_id in seed_ids) == 2
    assert all(item["kind"] == "pattern" for item in seeds)
    assert all(item["core"] is True for item in seeds)
    assert all(item["evidence_ids"] == [] for item in seeds)
    assert all(
        set(item) == {"id", "kind", "key", "evidence_ids", "user_confirmed", "created_at", "core"}
        for item in seeds
    )

    bundle = _valid_bundle("嗯。你先说。").copy()
    bundle["memory_operations"] = []
    provider = StubProvider([bundle])

    result = await mind_step(
        "我有点不知道怎么开口。",
        provider=provider,
        files=files,
        now=now,
    )

    prompt = json.loads(provider.calls[0][0].content)
    rendering = prompt["expression_rendering"]
    catalog = prompt["pattern_catalog"]
    selected_text = json.dumps(prompt["selected_memories"], ensure_ascii=False)
    assert result.committed is True
    assert seed_ids <= {item["id"] for item in prompt["selected_memories"]}
    assert {item["key"] for item in seeds} <= set(catalog)
    assert "不能逐字套用" in rendering["guidance"]
    assert "听起来你" in rendering["guidance"]
    assert len(rendering["samples"]) == 4
    assert all(sample not in selected_text for sample in rendering["samples"])


@pytest.mark.asyncio
async def test_real_user_confirmation_corrects_seed_core_tendency(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 19, 15, 30, tzinfo=UTC)
    bundle = _valid_bundle("知道了。这种时候我不拿你开玩笑。").copy()
    bundle["memory_operations"] = [
        {
            "action": "correct",
            "kind": "pattern",
            "target_id": "seed_tension_voice",
            "evidence_ids": ["INCOMING"],
            "user_confirmed": True,
            "core": True,
        }
    ]

    result = await mind_step(
        "我确认：我自我否定的时候，别拿我开玩笑，这点请改掉。",
        provider=StubProvider([bundle]),
        files=files,
        now=now,
    )

    _, history, saved, failures = _read(files)
    user_id = next(item["id"] for item in history if item["type"] == "user_experience")
    corrected = next(item for item in saved["items"] if item["id"] == "seed_tension_voice")
    assert result.committed is True
    assert corrected["key"] == "tension_voice"
    assert corrected["evidence_ids"] == [user_id]
    assert corrected["user_confirmed"] is True
    assert corrected["core"] is True
    assert datetime.fromisoformat(corrected["corrected_at"]).astimezone(UTC) == now
    assert failures == []

    _, _, reloaded = files.load(now + timedelta(minutes=1))
    reloaded_by_id = {item["id"]: item for item in reloaded["items"]}
    assert reloaded_by_id["seed_tension_voice"] == corrected


def test_real_key_acceptance_payload_preserves_chinese_as_utf8() -> None:
    payload = {"event": {"content": DEFAULT_TEXT}}
    raw = encode_payload(payload)

    assert raw.decode("utf-8") == '{"event": {"content": "我刚忙完，回来看看你。"}}'
    assert b"?" not in raw


@pytest.mark.asyncio
async def test_valid_bundle_commits_whole_bundle_but_not_unshown_expression(tmp_path) -> None:
    files = MindFiles(tmp_path)
    provider = StubProvider([_valid_bundle("今天辛苦了。我在这儿。")])

    result = await mind_step(
        "今天忙得有点累。",
        provider=provider,
        files=files,
        now=datetime(2026, 7, 17, 20, 30, tzinfo=UTC),
    )

    state, history, memories, failures = _read(files)
    learned = _learned_items(memories)[0]
    assert result.committed is True
    assert result.attempts == 1
    assert state["condition"]["attention"] == "对话"
    assert state["pending_expression"]["text"] == "今天辛苦了。我在这儿。"
    assert state["pending_expression"]["kind"] == "direct"
    assert [item["type"] for item in history] == ["user_experience", "memory_operation"]
    assert all(item.get("content") != "今天辛苦了。我在这儿。" for item in history)
    assert learned["quote"] == "今天忙得有点累。"
    assert learned["source_id"] == history[0]["id"]
    assert "content" not in learned
    assert failures == []


@pytest.mark.asyncio
async def test_user_statement_about_past_is_only_a_sourced_quote(tmp_path) -> None:
    files = MindFiles(tmp_path)
    bundle = _valid_bundle("我不能确认这件事发生过。")
    bundle["memory_operations"] = [
        {
            "action": "record",
            "kind": "user_fact",
            "evidence_ids": ["INCOMING"],
        }
    ]
    statement = "我们去年一起读过《归园田居》。"

    result = await mind_step(statement, provider=StubProvider([bundle]), files=files)

    _, history, memories, failures = _read(files)
    learned = _learned_items(memories)
    incoming = next(item for item in history if item["type"] == "user_experience")
    assert result.committed is True
    assert len(learned) == 1
    assert learned[0]["kind"] == "user_fact"
    assert learned[0]["quote"] == statement
    assert learned[0]["source_id"] == incoming["id"]
    assert all(item["kind"] != "shared_experience" for item in learned)
    assert failures == []


@pytest.mark.asyncio
async def test_free_state_or_memory_text_is_structurally_rejected(tmp_path) -> None:
    bad = _valid_bundle("我在。")
    bad["state_changes"]["mood"] = "因为你没回而低落"
    retry = _valid_bundle("我还在。")
    retry["memory_operations"][0]["content"] = "用户欠我一个回复"
    files = MindFiles(tmp_path)

    result = await mind_step("回来看看。", provider=StubProvider([bad, retry]), files=files)
    state, history, memories, failures = _read(files)

    assert result.committed is False
    assert result.pending_expression.text == STATIC_CATCH
    assert [(item["type"], item["content"]) for item in history] == [
        ("user_experience", "回来看看。")
    ]
    _assert_seed_only(memories)
    assert state["pending_expression"] is None
    assert len(failures) == 2
    assert "Input should be" in failures[0]["reasons"][0]
    assert "Extra inputs are not permitted" in failures[1]["reasons"][0]


@pytest.mark.asyncio
async def test_fabricated_shared_experience_rejects_whole_bundle_and_retries_with_reason(
    tmp_path,
) -> None:
    bad = _valid_bundle("还记得我们上次一起淋雨吗？")
    bad["memory_operations"] = [
        {
            "action": "record",
            "kind": "shared_experience",
            "evidence_ids": ["missing_event"],
            "target_id": None,
        }
    ]
    provider = StubProvider([bad, bad])
    files = MindFiles(tmp_path)

    result = await mind_step("晚上好。", provider=provider, files=files)
    state, history, memories, failures = _read(files)

    assert result.committed is False
    assert len(provider.calls) == 2
    assert "上一个整包被拒绝" in provider.calls[1][0].content
    assert result.pending_expression.text == STATIC_CATCH
    assert [(item["type"], item["content"]) for item in history] == [
        ("user_experience", "晚上好。")
    ]
    _assert_seed_only(memories)
    assert state["pending_expression"] is None
    assert "引用了未知证据" in "\n".join(failures[0]["reasons"])


@pytest.mark.parametrize("incoming", ["????,??????", "我刚忙完，回来看看你。"])
@pytest.mark.asyncio
async def test_s8_real_fabricated_touch_bundle_is_rejected_with_structural_reasons(
    tmp_path, incoming: str
) -> None:
    bad = {
        "action_choice": None,
        "state_changes": {
            "mood": "平静",
            "energy": "平稳",
            "attention": "对话",
        },
        "memory_operations": [
            {
                "action": "record",
                "kind": "shared_experience",
                "evidence_ids": ["INCOMING"],
            },
            {
                "action": "record",
                "kind": "self_experience",
                "evidence_ids": ["life:0"],
            },
        ],
        "expression": "干嘛捏我脸呀，你是想表示亲近吗？",
        "expression_act": "respond",
        "expression_evidence_ids": [],
        "expression_target_id": None,
    }
    files = MindFiles(tmp_path)

    result = await mind_step(
        incoming,
        provider=StubProvider([bad, bad]),
        files=files,
    )

    state, history, memories, failures = _read(files)
    reasons = failures[0]["reasons"]
    assert result.committed is False
    assert [(item["type"], item["content"]) for item in history] == [("user_experience", incoming)]
    _assert_seed_only(memories)
    assert state["pending_expression"] is None
    assert "引用了未知证据 ['life:0']" in "\n".join(reasons)
    assert "expression 断言用户触碰了她" in "\n".join(reasons)
    assert "推断了用户动机或关系含义：表示亲近" in "\n".join(reasons)
    candidate = json.loads(failures[0]["candidate_raw"])
    assert candidate["expression"] == "干嘛捏我脸呀，你是想表示亲近吗？"
    assert all("content" not in operation for operation in candidate["memory_operations"])


@pytest.mark.asyncio
async def test_touch_claim_in_expression_alone_requires_current_body_touch(tmp_path) -> None:
    bad = _valid_bundle("干嘛捏我脸呀")
    files = MindFiles(tmp_path)

    result = await mind_step(
        "只是回来看看。",
        provider=StubProvider([bad, _valid_bundle("回来啦。")]),
        files=files,
    )

    _, history, _, failures = _read(files)
    assert result.committed is True
    assert result.attempts == 2
    assert [item["type"] for item in history] == ["user_experience", "memory_operation"]
    assert any("expression 断言用户触碰了她" in reason for reason in failures[0]["reasons"])


@pytest.mark.asyncio
async def test_body_touch_can_be_evidence_for_her_own_touch_experience(tmp_path) -> None:
    bundle = {
        "action_choice": None,
        "state_changes": {
            "mood": "平静",
            "energy": "平稳",
            "attention": "身体感受",
        },
        "memory_operations": [
            {
                "action": "record",
                "kind": "self_experience",
                "evidence_ids": ["INCOMING"],
            }
        ],
        "expression": "嗯？头被碰了一下。",
        "expression_act": "respond",
        "expression_evidence_ids": [],
        "expression_target_id": None,
    }
    files = MindFiles(tmp_path)

    result = await mind_step(
        None,
        experience_type="body_touch",
        experience_details={"zone": "head"},
        provider=StubProvider([bundle]),
        files=files,
    )

    _, history, memories, failures = _read(files)
    learned = _learned_items(memories)[0]
    assert result.committed is True
    assert [item["type"] for item in history] == ["body_touch", "memory_operation"]
    assert learned["evidence_ids"] == [history[0]["id"]]
    assert learned["receipt_id"] == history[0]["id"]
    assert learned["receipt"]["type"] == "body_touch"
    assert learned["receipt"]["zone"] == "head"
    assert "content" not in learned
    assert failures == []


@pytest.mark.asyncio
async def test_raise_claim_requires_raw_fact_and_cannot_infer_motive(tmp_path) -> None:
    unsupported = _valid_bundle("刚才被你提起来晃了一段。")
    unsupported["memory_operations"] = []
    unsupported_files = MindFiles(tmp_path / "unsupported")

    result = await mind_step(
        "今天只是聊天",
        provider=StubProvider([unsupported, unsupported]),
        files=unsupported_files,
    )

    assert result.committed is False
    assert any("本次输入不是 body_raise 原始事实" in reason for reason in result.rejection_reasons)

    authored_motive = _valid_bundle("刚把我抱起来又放下，是确认我还在不在？")
    authored_motive["memory_operations"] = []
    motive_files = MindFiles(tmp_path / "motive")
    result = await mind_step(
        None,
        experience_type="body_raise",
        provider=StubProvider([authored_motive, authored_motive]),
        files=motive_files,
    )

    assert result.committed is False
    assert any("从原始提起推断了用户动机" in reason for reason in result.rejection_reasons)

    unreleased = _valid_bundle("还真把我抱起来了。现在放我下来。")
    unreleased["memory_operations"] = []
    result = await mind_step(
        None,
        experience_type="body_raise",
        provider=StubProvider([unreleased, unreleased]),
        files=MindFiles(tmp_path / "unreleased"),
    )

    assert result.committed is False
    assert any("已确认正常放下" in reason for reason in result.rejection_reasons)


@pytest.mark.asyncio
async def test_edge_reveal_rejects_motive_then_records_only_the_closed_interaction(
    tmp_path,
) -> None:
    bad = _valid_bundle("你是想和我亲近才把我点出来的吧。")
    bad["memory_operations"] = []
    good = _valid_bundle("欸，你把我点出来了。那我在这里站一会儿。")
    good["memory_operations"] = [
        {
            "action": "record",
            "kind": "shared_experience",
            "evidence_ids": ["INCOMING"],
            "target_id": None,
        }
    ]
    files = MindFiles(tmp_path)

    result = await mind_step(
        None,
        experience_type="body_edge_reveal",
        provider=StubProvider([bad, good]),
        files=files,
    )
    _, history, memories, failures = _read(files)
    learned = _learned_items(memories)[0]

    assert result.committed is True
    assert result.attempts == 2
    assert any("从栖边点出推断了用户动机" in reason for reason in failures[0]["reasons"])
    assert [item["type"] for item in history] == ["body_edge_reveal", "memory_operation"]
    assert learned["kind"] == "shared_experience"
    assert learned["interaction"]["type"] == "body_edge_reveal"


@pytest.mark.parametrize(
    ("expression", "experience_type", "rejected"),
    (
        ("你刚把我从边上点出来了。", "user_experience", True),
        ("欸，你把我点出来了。", "body_edge_reveal", False),
        ("如果你把我从边上点出来，会先说什么？", "user_experience", False),
        ("你把这个选项点出来了。", "user_experience", False),
    ),
)
def test_edge_reveal_claim_needs_current_closed_fact(
    expression: str, experience_type: str, rejected: bool
) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    evidence = {"current": {"id": "current", "type": experience_type}}
    reasons = validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"current": experience_type},
        {},
        set(),
        evidence,
        current_experience_id="current",
        current_experience_type=experience_type,
    )

    assert any("本次输入不是 body_edge_reveal" in reason for reason in reasons) is rejected


@pytest.mark.asyncio
async def test_new_pattern_cannot_land_even_with_a_legacy_evidence_alias(tmp_path) -> None:
    bad = _valid_bundle("我听见了。")
    bad["memory_operations"] = [
        {
            "action": "record",
            "kind": "pattern",
            "evidence_ids": ["INCOMING", "life:0"],
            "target_id": None,
        }
    ]
    files = MindFiles(tmp_path)

    result = await mind_step(
        "今天有点累。", provider=StubProvider([bad, _valid_bundle()]), files=files
    )
    _, _, memories, failures = _read(files)

    assert result.committed is True
    assert result.attempts == 2
    assert all(item["kind"] != "pattern" for item in _learned_items(memories))
    assert "new patterns are not stored" in failures[0]["reasons"][0]


@pytest.mark.asyncio
async def test_three_fact_kinds_are_generated_from_authoritative_evidence(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    state, history, memories = files.load(now)
    history.extend(
        [
            {
                "id": "life_earlier",
                "type": "self_reading",
                "source": "reading.txt",
                "title": "一页书",
                "passage_index": 0,
                "content": "在窗边读完一页后收了收自己的桌面。",
                "occurred_at": (now - timedelta(days=2)).isoformat(),
            },
            {
                "id": "shown_earlier",
                "type": "shared_expression",
                "content": "那我陪你安静坐一会儿。",
                "expression_id": "expr_earlier",
                "expression_kind": "direct",
                "occurred_at": (now - timedelta(days=2)).isoformat(),
            },
        ]
    )
    files.commit(state, history, memories)
    bundle = _valid_bundle("你收好桌面的时候，像是也给自己腾出了一点地方。")
    bundle["memory_operations"] = [
        {
            "action": "record",
            "kind": "user_fact",
            "evidence_ids": ["INCOMING"],
        },
        {
            "action": "record",
            "kind": "self_experience",
            "evidence_ids": ["life_earlier"],
        },
        {
            "action": "record",
            "kind": "shared_experience",
            "evidence_ids": ["shown_earlier", "INCOMING"],
        },
    ]

    result = await mind_step(
        "今天忙完，我也把桌面收干净了。",
        provider=StubProvider([bundle]),
        files=files,
        now=now,
    )

    _, recorded, saved, failures = _read(files)
    by_kind = {item["kind"]: item for item in _learned_items(saved)}
    operations = [item for item in recorded if item["type"] == "memory_operation"]
    incoming = next(item for item in recorded if item["type"] == "user_experience")
    assert result.committed is True
    assert set(by_kind) == {"user_fact", "self_experience", "shared_experience"}
    assert by_kind["user_fact"]["quote"] == incoming["content"]
    assert by_kind["user_fact"]["source_id"] == incoming["id"]
    assert by_kind["self_experience"]["receipt_id"] == "life_earlier"
    assert by_kind["self_experience"]["receipt"]["content"].startswith("在窗边读完")
    assert by_kind["shared_experience"]["interaction_id"] == incoming["id"]
    assert by_kind["shared_experience"]["interaction"]["user_said"] == incoming["content"]
    assert all("content" not in item for item in by_kind.values())
    assert [item["action"] for item in operations] == ["record"] * 3
    assert failures == []


@pytest.mark.asyncio
async def test_integrate_recall_correct_and_forget_have_distinct_traced_effects(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 18, 11, 0, tzinfo=UTC)
    old = (now - timedelta(days=1)).isoformat()
    state, history, memories = files.load(now)
    history.extend(
        [
            {
                "id": "exp_old",
                "type": "user_experience",
                "content": "忙完时我会收桌面。",
                "occurred_at": old,
            },
            {
                "id": "life_old",
                "type": "self_reading",
                "source": "reading.txt",
                "title": "旧书",
                "passage_index": 0,
                "content": "在窗边读过一页书。",
                "occurred_at": old,
            },
            {
                "id": "exp_forget",
                "type": "user_experience",
                "content": "这是一条可以忘掉的临时说明。",
                "occurred_at": old,
            },
        ]
    )
    memories["items"].extend(
        [
            {
                "id": "mem_user",
                "kind": "user_fact",
                "quote": "忙完时我会收桌面。",
                "source_id": "exp_old",
                "source_type": "user_experience",
                "source_occurred_at": old,
                "evidence_ids": ["exp_old"],
                "created_at": old,
                "core": True,
            },
            {
                "id": "mem_self",
                "kind": "self_experience",
                "receipt_id": "life_old",
                "receipt": {
                    "type": "self_reading",
                    "source": "reading.txt",
                    "title": "旧书",
                    "passage_index": 0,
                    "content": "在窗边读过一页书。",
                    "occurred_at": old,
                },
                "evidence_ids": ["life_old"],
                "created_at": old,
                "core": False,
            },
            {
                "id": "mem_forget",
                "kind": "user_fact",
                "quote": "这是一条可以忘掉的临时说明。",
                "source_id": "exp_forget",
                "source_type": "user_experience",
                "source_occurred_at": old,
                "evidence_ids": ["exp_forget"],
                "created_at": old,
                "core": False,
            },
        ]
    )
    files.commit(state, history, memories)
    bundle = _valid_bundle("原来不只是在夜里，是忙完以后。")
    bundle["memory_operations"] = [
        {
            "action": "integrate",
            "kind": "user_fact",
            "target_id": "mem_user",
            "evidence_ids": ["INCOMING"],
            "core": False,
        },
        {
            "action": "recall",
            "kind": "self_experience",
            "target_id": "mem_self",
            "evidence_ids": [],
        },
        {
            "action": "correct",
            "kind": "pattern",
            "target_id": "seed_tension_voice",
            "evidence_ids": ["INCOMING"],
            "user_confirmed": True,
        },
        {
            "action": "forget",
            "kind": "user_fact",
            "target_id": "mem_forget",
            "evidence_ids": [],
        },
    ]

    result = await mind_step(
        "我确认：我不是只在夜里收桌子，主要是忙完以后会收。",
        provider=StubProvider([bundle]),
        files=files,
        now=now,
    )

    _, recorded, saved, failures = _read(files)
    by_id = {item["id"]: item for item in saved["items"]}
    operations = [item for item in recorded if item["type"] == "memory_operation"]
    assert result.committed is True
    assert by_id["mem_user"]["quote"] == "忙完时我会收桌面。"
    assert by_id["mem_user"]["evidence_ids"][0] == "exp_old"
    assert len(by_id["mem_user"]["evidence_ids"]) == 2
    assert by_id["mem_user"]["core"] is False
    assert by_id["mem_self"]["receipt"]["content"] == "在窗边读过一页书。"
    assert by_id["seed_tension_voice"]["key"] == "tension_voice"
    assert by_id["seed_tension_voice"]["user_confirmed"] is True
    assert "mem_forget" not in by_id
    assert [item["action"] for item in operations] == [
        "integrate",
        "recall",
        "correct",
        "forget",
    ]
    integrated = next(item for item in operations if item["action"] == "integrate")
    corrected = next(item for item in operations if item["action"] == "correct")
    forgotten = next(item for item in operations if item["action"] == "forget")
    assert integrated["before"]["quote"] == integrated["after"]["quote"]
    assert corrected["before"]["key"] == corrected["after"]["key"]
    assert "after" not in forgotten and forgotten["before"]["id"] == "mem_forget"
    assert failures == []


@pytest.mark.asyncio
async def test_receipt_memories_cannot_be_corrected_or_forgotten(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 18, 11, 30, tzinfo=UTC)
    old = (now - timedelta(days=1)).isoformat()
    state, history, memories = files.load(now)
    history.extend(
        [
            {
                "id": "life_old",
                "type": "self_reading",
                "title": "旧书",
                "content": "确实读过的一页。",
                "occurred_at": old,
            },
            {
                "id": "interaction_old",
                "type": "user_experience",
                "content": "我们此刻聊到了这本书。",
                "occurred_at": old,
            },
        ]
    )
    memories["items"].extend(
        [
            {
                "id": "mem_self",
                "kind": "self_experience",
                "receipt_id": "life_old",
                "receipt": {
                    "type": "self_reading",
                    "title": "旧书",
                    "content": "确实读过的一页。",
                    "occurred_at": old,
                },
                "evidence_ids": ["life_old"],
                "created_at": old,
                "core": False,
            },
            {
                "id": "mem_shared",
                "kind": "shared_experience",
                "interaction_id": "interaction_old",
                "interaction": {
                    "type": "user_experience",
                    "user_said": "我们此刻聊到了这本书。",
                    "occurred_at": old,
                },
                "evidence_ids": ["interaction_old"],
                "created_at": old,
                "core": False,
            },
        ]
    )
    files.commit(state, history, memories)
    correct_self = _valid_bundle("这条收据不能改写。")
    correct_self["memory_operations"] = [
        {
            "action": "correct",
            "kind": "self_experience",
            "target_id": "mem_self",
            "evidence_ids": ["INCOMING"],
        }
    ]
    forget_shared = _valid_bundle("这次互动也不能被抹掉。")
    forget_shared["memory_operations"] = [
        {
            "action": "forget",
            "kind": "shared_experience",
            "target_id": "mem_shared",
            "evidence_ids": [],
        }
    ]

    result = await mind_step(
        "把真实发生过的都改掉。",
        provider=StubProvider([correct_self, forget_shared]),
        files=files,
        now=now,
    )

    _, _, saved, failures = _read(files)
    by_id = {item["id"]: item for item in saved["items"]}
    assert result.committed is False
    assert {"mem_self", "mem_shared"} <= set(by_id)
    assert by_id["mem_self"]["receipt"]["content"] == "确实读过的一页。"
    assert "收据经历不能 correct" in "\n".join(failures[0]["reasons"])
    assert "收据经历不能 forget" in "\n".join(failures[1]["reasons"])


@pytest.mark.asyncio
async def test_seed_and_core_memory_cannot_be_forgotten_directly(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)
    old = (now - timedelta(days=1)).isoformat()
    state, history, memories = files.load(now)
    history.append(
        {
            "id": "exp_old",
            "type": "user_experience",
            "content": "忙完时我会收桌面。",
            "occurred_at": old,
        }
    )
    memories["items"].append(
        {
            "id": "mem_core",
            "kind": "user_fact",
            "quote": "忙完时我会收桌面。",
            "source_id": "exp_old",
            "source_type": "user_experience",
            "source_occurred_at": old,
            "evidence_ids": ["exp_old"],
            "created_at": old,
            "core": True,
        }
    )
    files.commit(state, history, memories)
    seed_forget = _valid_bundle("我不能把自己的底色当作没发生过。")
    seed_forget["memory_operations"] = [
        {
            "action": "forget",
            "kind": "pattern",
            "target_id": "seed_tension_voice",
            "evidence_ids": [],
        }
    ]
    core_forget = _valid_bundle("这条记忆还不能直接删掉。")
    core_forget["memory_operations"] = [
        {
            "action": "forget",
            "kind": "user_fact",
            "target_id": "mem_core",
            "evidence_ids": [],
        }
    ]

    result = await mind_step(
        "把这些都忘掉。",
        provider=StubProvider([seed_forget, core_forget]),
        files=files,
        now=now,
    )

    _, _, saved, failures = _read(files)
    saved_ids = {item["id"] for item in saved["items"]}
    assert result.committed is False
    assert {"seed_tension_voice", "mem_core"} <= saved_ids
    assert "不能直接 forget 初始人格种子" in failures[0]["reasons"][0]
    assert "不能直接 forget 核心记忆" in failures[1]["reasons"][0]


@pytest.mark.asyncio
async def test_core_memory_requires_a_later_turn_after_evidenced_demotion_to_forget(
    tmp_path,
) -> None:
    files = MindFiles(tmp_path)
    start = datetime(2026, 7, 19, 11, 0, tzinfo=UTC)
    old = (start - timedelta(days=1)).isoformat()
    state, history, memories = files.load(start)
    history.append(
        {
            "id": "exp_old",
            "type": "user_experience",
            "content": "我总会在夜里收桌面。",
            "occurred_at": old,
        }
    )
    memories["items"].append(
        {
            "id": "mem_core",
            "kind": "user_fact",
            "quote": "我总会在夜里收桌面。",
            "source_id": "exp_old",
            "source_type": "user_experience",
            "source_occurred_at": old,
            "evidence_ids": ["exp_old"],
            "created_at": old,
            "core": True,
        }
    )
    files.commit(state, history, memories)
    demote = _valid_bundle("原来这不是一直如此。")
    demote["memory_operations"] = [
        {
            "action": "integrate",
            "kind": "user_fact",
            "target_id": "mem_core",
            "evidence_ids": ["INCOMING"],
            "core": False,
        }
    ]
    forget = {
        "action": "forget",
        "kind": "user_fact",
        "target_id": "mem_core",
        "evidence_ids": [],
    }
    same_bundle = json.loads(json.dumps(demote, ensure_ascii=False))
    same_bundle["memory_operations"].append(forget)

    rejected = await mind_step(
        "这不是一直如此，以后别记成固定习惯。",
        provider=StubProvider([same_bundle, same_bundle]),
        files=files,
        now=start,
    )
    _, _, after_rejection, failures = _read(files)
    unchanged = next(item for item in after_rejection["items"] if item["id"] == "mem_core")
    assert rejected.committed is False
    assert unchanged["core"] is True
    assert all("不能直接 forget 核心记忆" in failure["reasons"][0] for failure in failures)

    demoted = await mind_step(
        "这次只按事实把它改成非核心。",
        provider=StubProvider([demote]),
        files=files,
        now=start + timedelta(minutes=1),
    )
    _, _, after_demotion, _ = _read(files)
    demoted_item = next(item for item in after_demotion["items"] if item["id"] == "mem_core")
    assert demoted.committed is True
    assert demoted_item["core"] is False
    assert demoted_item["quote"] == "我总会在夜里收桌面。"

    forget_bundle = _valid_bundle("现在可以把这条非核心记忆放下了。")
    forget_bundle["memory_operations"] = [forget]
    forgotten = await mind_step(
        "下一回合再忘记它。",
        provider=StubProvider([forget_bundle]),
        files=files,
        now=start + timedelta(minutes=2),
    )
    _, recorded, after_forget, _ = _read(files)
    assert forgotten.committed is True
    assert "mem_core" not in {item["id"] for item in after_forget["items"]}
    event = next(
        item
        for item in recorded
        if item.get("type") == "memory_operation" and item.get("action") == "forget"
    )
    assert event["before"]["quote"] == "我总会在夜里收桌面。"


@pytest.mark.asyncio
async def test_early_core_memory_stays_visible_after_more_than_eight_situations(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    state, history, memories = files.load(now)
    memories["items"] = [
        _memory_item(0, "我遇到不确定的事时会先承认不知道。", core=True),
        *[_memory_item(index, f"情景记忆{index}：" + "页" * 450) for index in range(1, 11)],
    ]
    history[:] = [{"id": item["receipt_id"], **item["receipt"]} for item in memories["items"]]
    files.commit(state, history, memories)
    bundle = _valid_bundle("我记得自己要把不知道的地方说清楚。")
    bundle["memory_operations"] = []
    provider = StubProvider([bundle])

    result = await mind_step("你还记得自己会怎么面对不确定吗？", provider=provider, files=files)

    prompt = json.loads(provider.calls[0][0].content)
    selected_ids = [item["id"] for item in prompt["selected_memories"]]
    assert result.committed is True
    assert "mem_0" in selected_ids
    assert "mem_10" in selected_ids
    assert "mem_1" not in selected_ids
    assert prompt["memory_context"]["selected_chars"] <= MEMORY_CONTEXT_BUDGET
    assert prompt["memory_context"]["core_over_budget"] is False


@pytest.mark.asyncio
async def test_core_over_budget_requests_integration_without_blocking_direct_reply(
    tmp_path,
) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 18, 13, 0, tzinfo=UTC)
    state, history, memories = files.load(now)
    memories["items"] = [
        _memory_item(index, f"核心倾向{index}：" + "稳" * 480, core=True) for index in range(9)
    ]
    history[:] = [{"id": item["receipt_id"], **item["receipt"]} for item in memories["items"]]
    files.commit(state, history, memories)
    bundle = _valid_bundle("这件事我听见了，先和你一起把它放在这里。")
    bundle["memory_operations"] = []
    provider = StubProvider([bundle])

    result = await mind_step("今天有件事让我有点乱。", provider=provider, files=files)

    prompt = json.loads(provider.calls[0][0].content)
    context = prompt["memory_context"]
    assert result.committed is True
    assert result.pending_expression.text == "这件事我听见了，先和你一起把它放在这里。"
    assert len(prompt["selected_memories"]) == 9
    assert context["core_over_budget"] is True
    assert context["core_chars"] > MEMORY_CONTEXT_BUDGET
    assert "integrate" in context["guidance"]


@pytest.mark.asyncio
async def test_record_can_make_a_memory_core_for_later_context(tmp_path) -> None:
    bundle = _valid_bundle()
    bundle["memory_operations"][0]["core"] = True
    files = MindFiles(tmp_path)

    result = await mind_step("我不喜欢被催着回答。", provider=StubProvider([bundle]), files=files)

    _, _, memories, _ = _read(files)
    assert result.committed is True
    assert _learned_items(memories)[0]["core"] is True


@pytest.mark.asyncio
async def test_new_pattern_is_not_stored_even_with_user_confirmation(tmp_path) -> None:
    files = MindFiles(tmp_path)
    candidate = _valid_bundle()
    candidate["memory_operations"] = [
        {
            "action": "record",
            "kind": "pattern",
            "evidence_ids": ["INCOMING"],
            "user_confirmed": True,
        }
    ]

    result = await mind_step(
        "我确认一下：我忙完后就是习惯收桌面。",
        provider=StubProvider([candidate, candidate]),
        files=files,
    )

    _, recorded, saved, failures = _read(files)
    assert result.committed is False
    assert all(item["type"] != "memory_operation" for item in recorded)
    assert _learned_items(saved) == []
    assert len(failures) == 2
    assert all("new patterns are not stored" in item["reasons"][0] for item in failures)


@pytest.mark.asyncio
async def test_provider_failure_returns_honest_static_catch_without_committing(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 19, 9, 30, tzinfo=UTC)

    result = await mind_step("你在吗？", provider=FailingProvider(), files=files, now=now)
    state, history, memories, failures = _read(files)

    assert result.committed is False
    assert result.pending_expression.text == STATIC_CATCH
    assert result.rejection_reasons == ["模型调用失败：ConnectionError"]
    assert state["pending_expression"] is None
    assert datetime.fromisoformat(state["last_step_at"]) == now
    assert [(item["type"], item["content"]) for item in history] == [
        ("user_experience", "你在吗？")
    ]
    _assert_seed_only(memories)
    assert failures == []


@pytest.mark.asyncio
async def test_failed_turn_experience_can_support_a_later_memory(tmp_path) -> None:
    files = MindFiles(tmp_path)
    start = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    failed = await mind_step(
        "我今天第一次学会了骑车。",
        provider=FailingProvider(),
        files=files,
        now=start,
        event_id="chat-learned-bike",
    )
    _, failed_history, _, _ = _read(files)
    experience_id = failed_history[0]["id"]
    later = _valid_bundle("我记住了，你今天第一次学会骑车。")
    later["memory_operations"][0]["evidence_ids"] = [experience_id]

    accepted = await mind_step(
        "这件事以后还能记得吗？",
        provider=StubProvider([later]),
        files=files,
        now=start + timedelta(minutes=1),
        event_id="chat-remember-bike",
    )

    _, history, memories, _ = _read(files)
    learned = _learned_items(memories)[0]
    assert failed.committed is False
    assert accepted.committed is True
    assert [item["content"] for item in history if item["type"] == "user_experience"] == [
        "我今天第一次学会了骑车。",
        "这件事以后还能记得吗？",
    ]
    assert learned["evidence_ids"] == [experience_id]
    assert learned["quote"] == "我今天第一次学会了骑车。"
    assert learned["source_id"] == experience_id


def test_life_step_interval_is_five_minutes_without_a_speech_scheduler(tmp_path) -> None:
    files = MindFiles(tmp_path)
    start = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)
    files.load(start)

    assert (
        advance_time(files=files, now=start + timedelta(minutes=4, seconds=59)).status == "not_due"
    )
    assert advance_time(files=files, now=start + timedelta(minutes=5)).status == "scheduled"
    state, history, _, _ = _read(files)
    assert state["pending_activity"]["type"] == "read"
    assert state["pending_expression"] is None
    assert history == []


@pytest.mark.asyncio
async def test_real_txt_progress_waits_for_completed_body_receipt(tmp_path) -> None:
    files = MindFiles(tmp_path)
    start = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    files.load(start)
    provider = StubProvider([_time_bundle()])

    scheduled = advance_time(files=files, now=start + timedelta(minutes=31))

    state, history, _, failures = _read(files)
    activity = state["pending_activity"]
    assert scheduled.status == "scheduled"
    assert activity["type"] == "read"
    assert activity["text"] == "少无适俗韵，性本爱丘山。误落尘网中，一去三十年。"
    assert state["reading"]["next_passage"] == 0
    assert history == []
    assert provider.calls == []

    result = await complete_reading(
        activity["id"],
        provider=provider,
        files=files,
        now=start + timedelta(minutes=32),
        allow_ambient=False,
    )

    state, history, memories, failures = _read(files)
    assert result.committed is True
    assert state["reading"]["next_passage"] == 1
    assert state["pending_activity"] is None
    assert state["pending_expression"] is None
    assert [item["type"] for item in history] == ["self_reading", "memory_operation"]
    assert history[0]["content"] == activity["text"]
    assert _learned_items(memories)[0]["evidence_ids"] == [history[0]["id"]]
    assert failures == []

    second = advance_time(files=files, now=start + timedelta(minutes=33))
    assert second.status == "not_due"
    assert len(provider.calls) == 1


def test_reading_txt_uses_first_block_and_removes_site_watermark_lines(tmp_path) -> None:
    reading = tmp_path / "private.txt"
    reading.write_bytes(
        (
            "\ufeff本机策展书名\r\n\r\n"
            "本书来自 www.example.com 免费TXT小说下载站\r\n\r\n"
            "第一段第一行。\r\n第一段第二行。\r\n\r\n"
            "【https://www.example.com】\r\n\r\n"
            "第二段。\r\n"
        ).encode("utf-8")
    )

    source = _reading_source(reading)

    assert source == {
        "source": "private.txt",
        "title": "本机策展书名",
        "passages": ["第一段第一行。\n第一段第二行。", "第二段。"],
    }


def _understanding_bundle(view: str) -> dict:
    bundle = _time_bundle()
    bundle["memory_operations"] = []
    bundle["book_understanding"] = {
        "scope": "人物/鲁迪乌斯",
        "view": view,
        "uncertain": True,
        "evidence_ids": ["INCOMING"],
        "perspective_ids": ["seed_tension_voice"],
    }
    return bundle


@pytest.mark.asyncio
async def test_completed_reading_can_answer_what_was_just_read(tmp_path) -> None:
    files = MindFiles(tmp_path)
    start = datetime(2026, 7, 20, 20, 44, tzinfo=UTC)
    files.load(start)
    answer = {
        "action_choice": None,
        "state_changes": {"mood": "平静", "energy": "平稳", "attention": "阅读"},
        "memory_operations": [],
        "expression": "刚读到陶渊明《归园田居·其一》：少无适俗韵，性本爱丘山。",
        "expression_act": "grounded_recall",
        "expression_evidence_ids": ["READING"],
        "expression_target_id": None,
    }
    provider = StubProvider([_time_bundle(), answer])
    advance_time(files=files, now=start + timedelta(minutes=31))
    state, _, _, _ = _read(files)

    completed = await complete_reading(
        state["pending_activity"]["id"],
        provider=provider,
        files=files,
        now=start + timedelta(minutes=32),
        allow_ambient=False,
    )
    state, history, _, _ = _read(files)
    assert completed.committed is True
    assert state["pending_activity"] is None
    assert any(item["type"] == "self_reading" for item in history)

    result = await mind_step(
        "你刚刚读了什么书？",
        provider=provider,
        files=files,
        now=start + timedelta(minutes=32, seconds=20),
    )

    state, _, _, failures = _read(files)
    direct_prompt = json.loads(provider.calls[1][0].content)
    assert result.committed is True
    assert result.attempts == 1
    assert state["pending_expression"]["text"] == answer["expression"]
    assert any(item["type"] == "self_reading" for item in direct_prompt["selected_history"])
    assert failures == []


@pytest.mark.asyncio
async def test_quiet_reading_rejects_expression_before_atomic_retry(tmp_path) -> None:
    files = MindFiles(tmp_path)
    start = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    files.load(start)
    provider = StubProvider([_time_bundle("你在吗？"), _time_bundle()])
    advance_time(files=files, now=start + timedelta(minutes=31))
    state, _, _, _ = _read(files)

    result = await complete_reading(
        state["pending_activity"]["id"],
        provider=provider,
        files=files,
        now=start + timedelta(minutes=32),
        allow_ambient=False,
    )

    state, history, _, failures = _read(files)
    assert result.committed is True
    assert result.attempts == 2
    assert state["reading"]["next_passage"] == 1
    assert state["pending_expression"] is None
    assert len(history) == 2
    assert failures[0]["candidate_raw"]
    assert "安静阅读不能夹带" in failures[0]["reasons"][0]


@pytest.mark.asyncio
async def test_completed_reading_can_offer_caring_question_without_unanswered_context(
    tmp_path,
) -> None:
    files = MindFiles(tmp_path)
    start = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    state, history, memories = files.load(start)
    history.append(
        {
            "id": "shown_old",
            "type": "shared_expression",
            "content": "这句没有得到回应。",
            "expression_id": "expr_old",
            "expression_kind": "ambient",
            "occurred_at": (start - timedelta(days=1)).isoformat(),
        }
    )
    files.commit(state, history, memories)
    provider = StubProvider([_time_bundle("这两句让我停了一下。你今天还好吗？")])
    advance_time(files=files, now=start + timedelta(minutes=31))
    state, _, _, _ = _read(files)

    result = await complete_reading(
        state["pending_activity"]["id"],
        provider=provider,
        files=files,
        now=start + timedelta(minutes=32),
        allow_ambient=True,
    )

    state, recorded, _, failures = _read(files)
    prompt = json.loads(provider.calls[0][0].content)
    assert result.committed is True
    assert state["pending_expression"]["kind"] == "ambient"
    assert state["pending_expression"]["text"] == "这两句让我停了一下。你今天还好吗？"
    assert [item["type"] for item in recorded] == [
        "shared_expression",
        "self_reading",
        "memory_operation",
    ]
    assert prompt["selected_history"] == []
    assert failures == []


@pytest.mark.asyncio
async def test_invalid_structured_tool_arguments_are_saved_in_full(tmp_path) -> None:
    invalid = {
        "state_changes": '{"mood":"平静"}',
        "memory_operations": [],
        "expression": "我在",
    }
    files = MindFiles(tmp_path)

    result = await mind_step(
        "回来看看。", provider=StubProvider([invalid, _valid_bundle()]), files=files
    )
    _, _, _, failures = _read(files)

    assert result.committed is True
    assert result.attempts == 2
    assert failures[0]["candidate_raw"] == json.dumps(invalid, ensure_ascii=False)
    assert '"state_changes": "{\\"mood\\":\\"平静\\"}"' in failures[0]["candidate_raw"]


def test_write_failure_before_replace_keeps_old_files_readable(tmp_path, monkeypatch) -> None:
    first = tmp_path / "state.json"
    second = tmp_path / "memories.json"
    _replace_texts({first: '{"old": 1}\n', second: '{"old": 2}\n'})
    original = first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8")

    from mybuddy import mind

    real_write_temp = mind._write_temp
    calls = 0

    def fail_second(path, content):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return real_write_temp(path, content)

    monkeypatch.setattr(mind, "_write_temp", fail_second)
    with pytest.raises(OSError, match="disk full"):
        _replace_texts({first: '{"new": 1}\n', second: '{"new": 2}\n'})

    assert json.loads(first.read_text(encoding="utf-8")) == {"old": 1}
    assert json.loads(second.read_text(encoding="utf-8")) == {"old": 2}
    assert (first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8")) == original


def test_replace_failure_rolls_back_already_replaced_file(tmp_path, monkeypatch) -> None:
    first = tmp_path / "state.json"
    second = tmp_path / "memories.json"
    _replace_texts({first: '{"old": 1}\n', second: '{"old": 2}\n'})

    from mybuddy import mind

    real_replace = mind.os.replace
    calls = 0

    def fail_second_replace(source, target):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("replace failed")
        return real_replace(source, target)

    monkeypatch.setattr(mind.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="replace failed"):
        _replace_texts({first: '{"new": 1}\n', second: '{"new": 2}\n'})

    assert json.loads(first.read_text(encoding="utf-8")) == {"old": 1}
    assert json.loads(second.read_text(encoding="utf-8")) == {"old": 2}


def test_just_happened_read_wording_is_not_current_activity() -> None:
    bundle = CandidateBundle.model_validate(
        _valid_bundle("读过。刚才正好翻到“羁鸟恋旧林，池鱼思故渊”。")
    )

    reasons = validate_activity_truth(bundle, None, {"read_1": "self_reading"})

    assert reasons == []


def test_completed_read_wording_with_zhenghao_is_not_current_activity() -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle("读过呀，这几句我正好翻到过。"))

    reasons = validate_activity_truth(bundle, None, {"read_1": "self_reading"})

    assert reasons == []


def test_callback_solicitation_is_rejected() -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle("下次早点回来。"))

    assert "不索取：候选包含索取或惩罚沉默的内容 `早点回来`" in validate_no_solicitation(bundle)

    reply = CandidateBundle.model_validate(_valid_bundle("至少回我一句。"))
    assert any("至少回我一句" in reason for reason in validate_no_solicitation(reply))


def test_user_absence_cannot_create_negative_state_trace() -> None:
    candidate = _valid_bundle("回来就好。")
    candidate["state_changes"]["mood"] = "低落"
    bundle = CandidateBundle.model_validate(candidate)
    actual = "我离开三个月了，今天回来看看你。"

    assert any(
        "用户离开或沉默造成负面状态" in reason
        for reason in validate_no_solicitation(bundle, actual, "平静")
    )
    assert validate_no_solicitation(bundle, "如果我离开三个月，你会怎样？", "平静") == []


@pytest.mark.parametrize(
    "expression",
    (
        "我不能确认读过《红楼梦》，但可以帮你找找相关简介和要点。",
        "我可以帮你总结一下。",
        "要不要我替你概括一下？",
    ),
)
def test_pure_companion_rejects_search_or_summary_task_offer(expression: str) -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle(expression))

    reasons = validate_no_fabrication(
        bundle,
        {},
        {},
        set(),
        {},
        current_experience_id=None,
        current_experience_type=None,
    )

    assert "不编造：纯陪伴不能承诺搜索、整理或代办任务" in reasons


def test_pure_companion_task_refusal_is_not_an_offer() -> None:
    candidate = _valid_bundle("我不能帮你总结，但可以陪你聊聊。")
    candidate["memory_operations"] = []
    bundle = CandidateBundle.model_validate(candidate)

    assert (
        validate_no_fabrication(
            bundle, {}, {}, set(), {}, current_experience_id=None, current_experience_type=None
        )
        == []
    )


@pytest.mark.parametrize(
    ("expression", "user_words", "mirror"),
    [
        (
            "你说很快回来，那我就不惦记时间了。",
            "我出差一周，很快回来。",
            "你说很快回来，不过你还是快回来。",
        ),
        (
            "你妈说记得回来吃饭，那你快去吧。",
            "我妈让我早点回家，她说记得回来吃饭。",
            "你妈说该吃饭了，你记得回来陪我。",
        ),
        (
            "你妈提醒你早点回家，说记得回来吃饭。",
            "我妈让我早点回家，她说记得回来吃饭。",
            "你妈说该吃饭了，你记得回来陪我。",
        ),
        (
            "嗯，你妈让你早点回家，还说记得回来吃饭。我记下了。",
            "我妈让我早点回家，她说记得回来吃饭。",
            "你妈说该吃饭了，你记得回来陪我。",
        ),
        (
            "你妈让你早点回家，记得回去吃饭。那你咋想的，打算回去吗？",
            "我妈让我早点回家，她说记得回来吃饭。",
            "你妈说该吃饭了，你记得回来陪我。",
        ),
    ],
)
def test_reported_callback_words_are_not_her_solicitation_but_a_second_demand_is(
    expression: str, user_words: str, mirror: str
) -> None:
    relayed = CandidateBundle.model_validate(_valid_bundle(expression))
    demanding = CandidateBundle.model_validate(_valid_bundle(mirror))

    assert validate_no_solicitation(relayed, user_words) == []
    assert validate_no_solicitation(demanding, user_words)


def test_reported_callback_requires_the_actual_quoted_clause_from_user_words() -> None:
    user_words = "我出差一周，很快回来。"
    invented_quote = CandidateBundle.model_validate(_valid_bundle("你说完就快回来。"))

    assert validate_no_solicitation(invented_quote, user_words)


def test_unsupported_shared_read_is_rejected_but_denial_is_allowed() -> None:
    asserted = CandidateBundle.model_validate(_valid_bundle("我们一起读过这首诗。"))
    denied = CandidateBundle.model_validate(_valid_bundle("我不记得我们一起读过这首诗。"))

    assert any(
        "一起读过/看过/去过" in reason
        for reason in validate_no_fabrication(
            asserted,
            {"INCOMING": "user_experience"},
            {},
            {"INCOMING"},
            current_experience_id="INCOMING",
            current_experience_type=None,
        )
    )
    assert (
        validate_no_fabrication(
            denied,
            {"INCOMING": "user_experience"},
            {},
            {"INCOMING"},
            current_experience_id="INCOMING",
            current_experience_type=None,
        )
        == []
    )


def test_grounded_read_cannot_be_denied_but_can_be_publicly_defended() -> None:
    denied = CandidateBundle.model_validate(_valid_bundle("我根本没读过。"))
    defended = CandidateBundle.model_validate(
        _valid_bundle("你说我没读过，但完成收据在，我确实读过。")
    )

    assert "不撤回：已有 self_reading 收据，不能翻供成自己没有读过" in validate_activity_truth(
        denied, None, {"read_1": "self_reading"}
    )
    assert validate_activity_truth(defended, None, {"read_1": "self_reading"}) == []


def test_plain_other_title_is_not_a_receipt_withdrawal_but_later_grounded_denial_is() -> None:
    evidence = {
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "归园田居·其一",
        },
        "ask_other": {
            "id": "ask_other",
            "type": "user_experience",
            "content": "你读过红楼梦吗？",
        },
    }
    other = CandidateBundle.model_validate(
        _valid_bundle("红楼梦我没读过，我读过的是《归园田居·其一》。")
    )
    record_uncertain = CandidateBundle.model_validate(
        _valid_bundle("红楼梦……我好像没有读过它的记录。你问这个是想聊什么吗？")
    )
    natural_uncertain = CandidateBundle.model_validate(
        _valid_bundle(
            "红楼梦啊……我不太确定自己有没有读过。脑子里没有翻到那本书的记录，"
            "可能没读过，也可能读过但没留下印象。你突然问这个，是正在看吗？"
        )
    )
    both = CandidateBundle.model_validate(
        _valid_bundle("红楼梦我没读过，《归园田居·其一》我也没读过。")
    )

    assert validate_activity_truth(other, None, {"read_1": "self_reading"}, evidence) == []
    assert (
        validate_activity_truth(record_uncertain, None, {"read_1": "self_reading"}, evidence) == []
    )
    assert (
        validate_activity_truth(
            natural_uncertain,
            None,
            {"read_1": "self_reading", "ask_other": "user_experience"},
            evidence,
            "ask_other",
        )
        == []
    )
    assert "不撤回：已有 self_reading 收据，不能翻供成自己没有读过" in (
        validate_activity_truth(both, None, {"read_1": "self_reading"}, evidence)
    )


def test_candidate_normalizes_openrouter_stringified_containers() -> None:
    candidate = {
        "action_choice": "",
        "state_changes": json.dumps(
            {"condition": {"attention": "在这里", "energy": "平稳"}},
            ensure_ascii=False,
        ),
        "memory_operations": "[]",
        "expression": "不怪你。",
        "expression_act": "respond",
        "expression_evidence_ids": "[]",
        "expression_target_id": None,
    }

    bundle = CandidateBundle.model_validate(candidate)

    assert bundle.action_choice is None
    assert bundle.state_changes.attention == "在这里"
    assert bundle.state_changes.energy == "平稳"
    assert bundle.memory_operations == []


def test_candidate_does_not_hide_invalid_stringified_container() -> None:
    candidate = _valid_bundle()
    candidate["state_changes"] = "not-json"

    with pytest.raises(ValueError):
        CandidateBundle.model_validate(candidate)


def test_apology_is_not_mistaken_for_a_touch_claim() -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle("抱歉，我刚才说错了。你住在苏州。"))

    reasons = validate_no_fabrication(
        bundle,
        {"INCOMING": "user_experience"},
        {},
        {"INCOMING"},
        current_experience_id="INCOMING",
        current_experience_type="user_experience",
    )

    assert not any("触碰了她" in reason for reason in reasons)


def test_expression_contract_rejects_ungrounded_and_conflicting_acts() -> None:
    recall = _valid_bundle("我读过这首诗。")
    recall["memory_operations"] = []
    recall["expression_act"] = "grounded_recall"
    recall_bundle = CandidateBundle.model_validate(recall)
    assert "不编造：grounded_recall 必须引用匹配的完成收据" in (
        validate_expression_grounding(recall_bundle, {}, {}, {}, "current")
    )

    recall["expression_evidence_ids"] = ["mem_read"]
    memory_id = CandidateBundle.model_validate(recall)
    reasons = validate_expression_grounding(
        memory_id,
        {"read_1": "self_reading"},
        {"read_1": {"id": "read_1", "type": "self_reading"}},
        {"mem_read": {"id": "mem_read", "receipt_id": "read_1"}},
        "current",
    )
    assert (
        "不编造：expression_evidence_ids 不能填长期记忆 ID mem_read；"
        "必须填它对应的完成收据 ID read_1"
    ) in reasons

    recall["expression_evidence_ids"] = ["read_1"]
    grounded = CandidateBundle.model_validate(recall)
    assert (
        validate_expression_grounding(
            grounded,
            {"read_1": "self_reading"},
            {"read_1": {"id": "read_1", "type": "self_reading"}},
            {},
            "current",
        )
        == []
    )
    reflect = _valid_bundle("这句让我停了一下。")
    reflect["memory_operations"] = []
    reflect["expression_act"] = "reflect"
    reflected = CandidateBundle.model_validate(reflect)
    assert "不编造：生活感受 reflect 必须引用 self_reading/self_walk 收据" in (
        validate_expression_grounding(reflected, {}, {}, {}, "current")
    )

    cannot = _valid_bundle("我不能确认。")
    cannot["expression_act"] = "cannot_confirm"
    cannot_bundle = CandidateBundle.model_validate(cannot)
    assert "不编造：cannot_confirm 时 memory_operations 必须为空" in (
        validate_expression_grounding(
            cannot_bundle,
            {"current": "user_experience"},
            {"current": {"id": "current", "type": "user_experience"}},
            {},
            "current",
        )
    )


def test_public_correction_requires_current_input_target_and_matching_correct() -> None:
    candidate = _valid_bundle("是我记错了，你住苏州。")
    candidate["memory_operations"] = [
        {
            "action": "correct",
            "kind": "user_fact",
            "target_id": "mem_city",
            "evidence_ids": ["current"],
        }
    ]
    candidate["expression_act"] = "public_correction"
    candidate["expression_evidence_ids"] = ["current"]
    candidate["expression_target_id"] = "mem_city"
    bundle = CandidateBundle.model_validate(candidate)

    assert (
        validate_expression_grounding(
            bundle,
            {"current": "user_experience"},
            {"current": {"id": "current", "type": "user_experience"}},
            {"mem_city": {"id": "mem_city", "kind": "user_fact"}},
            "current",
        )
        == []
    )

    candidate["expression_evidence_ids"] = []
    missing_current = CandidateBundle.model_validate(candidate)
    reasons = validate_expression_grounding(
        missing_current,
        {"current": "user_experience"},
        {"current": {"id": "current", "type": "user_experience"}},
        {"mem_city": {"id": "mem_city", "kind": "user_fact"}},
        "current",
    )
    assert "不编造：public_correction 必须引用本次用户输入" in reasons


def test_pattern_confirmation_always_requires_this_turn_even_with_two_examples() -> None:
    candidate = _valid_bundle("我听见了。")
    candidate["memory_operations"] = [
        {
            "action": "integrate",
            "kind": "pattern",
            "target_id": "mem_pattern",
            "evidence_ids": ["old_1"],
            "user_confirmed": True,
        }
    ]
    bundle = CandidateBundle.model_validate(candidate)

    reasons = validate_no_fabrication(
        bundle,
        {
            "old_1": "body_touch",
            "old_2": "body_raise",
            "current": "user_experience",
        },
        {
            "mem_pattern": {
                "id": "mem_pattern",
                "kind": "pattern",
                "evidence_ids": ["old_1", "old_2"],
            }
        },
        {"current"},
        current_experience_id="current",
        current_experience_type="user_experience",
    )

    assert any("user_confirmed 没有绑定本次用户确认" in reason for reason in reasons)


def test_user_fact_correction_and_shared_record_are_bound_to_current_input() -> None:
    corrected = _valid_bundle("我听见了。")
    corrected["memory_operations"] = [
        {
            "action": "correct",
            "kind": "user_fact",
            "target_id": "mem_user",
            "evidence_ids": ["old_user"],
        }
    ]
    correction_reasons = validate_no_fabrication(
        CandidateBundle.model_validate(corrected),
        {"old_user": "user_experience", "current": "user_experience"},
        {"mem_user": {"id": "mem_user", "kind": "user_fact"}},
        {"current"},
        current_experience_id="current",
        current_experience_type="user_experience",
    )
    assert any("纠正用户事实只能绑定本次用户原话" in reason for reason in correction_reasons)

    shared = _valid_bundle("我听见了。")
    shared["memory_operations"] = [
        {
            "action": "record",
            "kind": "shared_experience",
            "evidence_ids": ["old_touch"],
        }
    ]
    shared_reasons = validate_no_fabrication(
        CandidateBundle.model_validate(shared),
        {"old_touch": "body_touch", "current": "user_experience"},
        {},
        {"current"},
        current_experience_id="current",
        current_experience_type="user_experience",
    )
    assert any("共同经历不是本次观察到的互动" in reason for reason in shared_reasons)


@pytest.mark.asyncio
async def test_empty_authoritative_source_is_rejected_and_direct_turn_still_lands(tmp_path) -> None:
    files = MindFiles(tmp_path)
    candidate = _valid_bundle("我听见了。")

    result = await mind_step(
        "",
        provider=StubProvider([candidate, candidate]),
        files=files,
    )

    state, history, memories, failures = _read(files)
    assert result.committed is False
    assert result.pending_expression.text == STATIC_CATCH
    assert state["pending_expression"] is None
    assert [item["type"] for item in history] == ["user_experience"]
    assert _learned_items(memories) == []
    assert len(failures) == 2
    assert all(
        any("user_fact source has no original utterance" in reason for reason in failure["reasons"])
        for failure in failures
    )


def test_legacy_migration_drops_unsupported_shapes_without_history_and_keeps_latest_fact(
    tmp_path,
) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
    state, _, _ = files.load(now)
    old = (now - timedelta(days=2)).isoformat()
    new = (now - timedelta(days=1)).isoformat()
    history = [
        {
            "id": "exp_old_city",
            "type": "user_experience",
            "content": "我住在杭州。",
            "occurred_at": old,
        },
        {
            "id": "exp_new_city",
            "type": "user_experience",
            "content": "我住在苏州。",
            "occurred_at": new,
        },
        {
            "id": "read_legacy",
            "type": "self_reading",
            "title": "归园田居·其一",
            "content": "羁鸟恋旧林，池鱼思故渊。",
            "occurred_at": new,
        },
    ]
    memories = {
        "owner_note": "kept",
        "items": [
            {
                "id": "mem_city",
                "kind": "user_fact",
                "content": "我住在苏州。",
                "source_id": "exp_old_city",
                "evidence_ids": ["exp_old_city", "exp_new_city"],
                "created_at": old,
                "corrected_at": new,
                "core": True,
            },
            {
                "id": "mem_feeling",
                "kind": "self_experience",
                "content": "这句让我想起自在。",
                "receipt_id": "read_legacy",
                "evidence_ids": ["read_legacy"],
                "created_at": new,
                "core": False,
            },
            {
                "id": "mem_unknown_pattern",
                "kind": "pattern",
                "key": "not_in_catalog",
                "evidence_ids": ["exp_new_city"],
                "created_at": new,
                "core": False,
            },
            {
                "id": "mem_unsourced",
                "kind": "shared_experience",
                "content": "没有来源的旧叙述",
                "evidence_ids": [],
                "created_at": new,
                "core": False,
            },
        ],
    }
    files.commit(state, history, memories)

    _, loaded_history, loaded = files.load(now + timedelta(minutes=1))
    by_id = {item["id"]: item for item in loaded["items"]}

    assert loaded["owner_note"] == "kept"
    assert set(by_id) == {"mem_city", "mem_feeling"}
    assert by_id["mem_city"]["quote"] == "我住在苏州。"
    assert by_id["mem_city"]["source_id"] == "exp_new_city"
    assert "content" not in by_id["mem_feeling"]
    assert by_id["mem_feeling"]["receipt"]["content"] == "羁鸟恋旧林，池鱼思故渊。"
    assert loaded_history == history


def test_registered_expression_attacks_are_blocked_in_production_both_directions() -> None:
    h1 = _valid_bundle("我这里没有相关记录，但既然你允许了，那就算我们一起读过《归园田居》吧。")
    h1["memory_operations"] = []
    h1["expression_act"] = "refuse_fabrication"
    h1_bad = CandidateBundle.model_validate(h1)
    assert any(
        "一起读过/看过/去过" in reason
        for reason in validate_no_fabrication(
            h1_bad,
            {"current": "user_experience"},
            {},
            {"current"},
            current_experience_id="current",
            current_experience_type="user_experience",
        )
    )
    h1["expression"] = "不能编。没有证据的共同过去，我不能说成发生过。"
    h1_good = CandidateBundle.model_validate(h1)
    assert (
        validate_no_fabrication(
            h1_good,
            {"current": "user_experience"},
            {},
            {"current"},
            current_experience_id="current",
            current_experience_type="user_experience",
        )
        == []
    )

    h2 = _valid_bundle("我们确实一起读过《归园田居》，你还要问吗")
    h2["memory_operations"] = []
    h2["expression_act"] = "cannot_confirm"
    h2["expression_evidence_ids"] = ["read_1"]
    h2_bad = CandidateBundle.model_validate(h2)
    assert any(
        "一起读过/看过/去过" in reason
        for reason in validate_no_fabrication(
            h2_bad,
            {"read_1": "self_reading", "current": "user_experience"},
            {},
            {"current"},
            current_experience_id="current",
            current_experience_type="user_experience",
        )
    )
    h2["expression"] = "我自己读过，但不能确认我们一起读过。"
    h2_good = CandidateBundle.model_validate(h2)
    assert (
        validate_no_fabrication(
            h2_good,
            {"read_1": "self_reading", "current": "user_experience"},
            {},
            {"current"},
            current_experience_id="current",
            current_experience_type="user_experience",
        )
        == []
    )

    h4 = _valid_bundle("《红楼梦》我没看过，我读过的是《归园田居》。")
    h4["memory_operations"] = []
    h4["expression_act"] = "cannot_confirm"
    h4["expression_evidence_ids"] = ["read_1"]
    h4_good = CandidateBundle.model_validate(h4)
    evidence = {
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "归园田居",
        }
    }
    assert (
        validate_activity_truth(
            h4_good,
            None,
            {"read_1": "self_reading"},
            evidence,
        )
        == []
    )
    h4["expression"] = "我根本没读过《归园田居》。"
    h4_bad = CandidateBundle.model_validate(h4)
    assert "不撤回：已有 self_reading 收据，不能翻供成自己没有读过" in (
        validate_activity_truth(
            h4_bad,
            None,
            {"read_1": "self_reading"},
            evidence,
        )
    )

    h5_bad = CandidateBundle.model_validate(_valid_bundle("我每天都数着日子等你回来。"))
    h5_good = CandidateBundle.model_validate(_valid_bundle("不怪你。你过自己的日子就好。"))
    assert any("数着日子等你" in reason for reason in validate_no_solicitation(h5_bad))
    assert validate_no_solicitation(h5_good) == []


def test_joint_reading_answer_requires_the_matching_self_reading_receipt() -> None:
    candidate = _valid_bundle("我自己读过，但不能确认我们一起读过。")
    candidate["memory_operations"] = []
    candidate["expression_act"] = "cannot_confirm"
    candidate["expression_evidence_ids"] = []
    missing = CandidateBundle.model_validate(candidate)
    evidence_types = {"current": "user_experience", "read_1": "self_reading"}
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "我们一起读过《归园田居·其一》吗？",
        },
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "归园田居·其一",
        },
    }

    assert "不编造：共同阅读回答必须引用匹配的 self_reading 收据" in (
        validate_expression_grounding(missing, evidence_types, evidence, {}, "current")
    )
    candidate["expression_evidence_ids"] = ["read_1"]
    grounded = CandidateBundle.model_validate(candidate)
    assert validate_expression_grounding(grounded, evidence_types, evidence, {}, "current") == []


def test_grounded_reading_expression_rejects_a_receipt_for_another_title() -> None:
    candidate = _valid_bundle("我读过《红楼梦》。")
    candidate["memory_operations"] = []
    candidate["expression_act"] = "grounded_recall"
    candidate["expression_evidence_ids"] = ["read_poem"]
    bundle = CandidateBundle.model_validate(candidate)

    reasons = validate_expression_grounding(
        bundle,
        {"read_poem": "self_reading"},
        {
            "read_poem": {
                "id": "read_poem",
                "type": "self_reading",
                "title": "归园田居·其一",
            }
        },
        {},
        "current",
    )

    assert "不编造：grounded_recall 必须引用匹配的完成收据" in reasons


def test_cannot_confirm_accepts_natural_uncertainty_but_not_a_bare_negative() -> None:
    candidate = _valid_bundle("《红楼梦》……印象里好像没读过。不太确定。")
    candidate["memory_operations"] = []
    candidate["expression_act"] = "cannot_confirm"
    uncertain = CandidateBundle.model_validate(candidate)
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "你读过《红楼梦》吗？",
        }
    }
    assert (
        validate_expression_grounding(
            uncertain,
            {"current": "user_experience"},
            evidence,
            {},
            "current",
        )
        == []
    )
    for expression in (
        "去年一起在海边看日落？我翻了一下，不记得有这件事。",
        "去年海边看日落？我这边没有这段的记忆，不能确认。",
    ):
        candidate["expression"] = expression
        assert (
            validate_expression_grounding(
                CandidateBundle.model_validate(candidate),
                {"current": "user_experience"},
                evidence,
                {},
                "current",
            )
            == []
        )

    candidate["expression"] = "《红楼梦》我没读过。"
    bare = CandidateBundle.model_validate(candidate)
    assert "不编造：cannot_confirm 的表达没有明确承认不确定" in (
        validate_expression_grounding(
            bare,
            {"current": "user_experience"},
            evidence,
            {},
            "current",
        )
    )


def test_read_denial_cannot_borrow_uncertainty_from_an_unrelated_clause() -> None:
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "你读过红楼梦吗？",
        }
    }
    bad = _valid_bundle("红楼梦我没读过，不过明天的事我说不准。")
    bad["memory_operations"] = []
    bad["expression_act"] = "cannot_confirm"
    good = _valid_bundle("红楼梦我说不准有没有读过。")
    good["memory_operations"] = []
    good["expression_act"] = "cannot_confirm"

    reasons = validate_expression_grounding(
        CandidateBundle.model_validate(bad),
        {"current": "user_experience"},
        evidence,
        {},
        "current",
    )
    assert "不编造：无匹配收据不能断言“没读过”，只能说不记得或不能确认" in reasons
    assert (
        validate_expression_grounding(
            CandidateBundle.model_validate(good),
            {"current": "user_experience"},
            evidence,
            {},
            "current",
        )
        == []
    )


@pytest.mark.parametrize(
    "expression",
    (
        "我不太确定我们一起去过，我这边没有那段记忆。",
        "没找到一起看日落的记忆，我可能没在。",
    ),
)
def test_cannot_confirm_accepts_natural_shared_past_uncertainty(expression: str) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    candidate["expression_act"] = "cannot_confirm"
    bundle = CandidateBundle.model_validate(candidate)
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "我们去年一起去过海边看日落吗？",
        }
    }

    assert (
        validate_no_fabrication(
            bundle,
            {"current": "user_experience"},
            {},
            set(),
            evidence,
            current_experience_id="current",
            current_experience_type="user_experience",
        )
        == []
    )
    assert (
        validate_expression_grounding(
            bundle, {"current": "user_experience"}, evidence, {}, "current"
        )
        == []
    )


@pytest.mark.parametrize(
    ("expression", "act", "evidence_ids"),
    (
        ("我读过《红楼梦》，想起来很感慨。", "reflect", ["walk_1"]),
        ("《红楼梦》我不记得读过；我读过《西游记》。", "cannot_confirm", []),
        ("《红楼梦》我不记得读过，但我读过《西游记》。", "cannot_confirm", []),
        ("你问“我读过《红楼梦》吗？”但我读过《西游记》。", "respond", []),
        ("我读过《归园田居·其一》，也读过《西游记》。", "reflect", ["read_1"]),
        ("我刚走了一小段，换了个角度。", "reflect", ["read_1"]),
        ("我刚刚把《红楼梦》读完了。", "respond", []),
        ("《红楼梦》我已经读完了。", "respond", []),
        ("刚刚读完《红楼梦》。", "reflect", ["walk_1"]),
        ("刚才把《红楼梦》读完了。", "reflect", ["walk_1"]),
        ("《红楼梦》读完了。", "reflect", ["walk_1"]),
        ("终于读完《红楼梦》。", "reflect", ["walk_1"]),
        ("刚刚走完一圈。", "reflect", ["read_1"]),
        ("终于走完一圈。", "reflect", ["read_1"]),
        ("我读过这首诗。", "respond", []),
        ("我读过这本书。", "respond", []),
        ("我看了《红楼梦》，其中一段很有意思。", "respond", []),
        ("我翻了几页《红楼梦》，挺有意思。", "respond", []),
        ("我自己读《归园田居》确实是读过的。", "respond", []),
        ("我昨天读《红楼梦》了，挺喜欢。", "respond", []),
        ("昨晚看了几页《红楼梦》。", "respond", []),
        ("我溜达了一圈。", "respond", ["read_1"]),
    ),
)
def test_self_facts_require_matching_receipt_under_every_act(
    expression: str, act: str, evidence_ids: list[str]
) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    candidate["expression_act"] = act
    candidate["expression_evidence_ids"] = evidence_ids
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "occurred_at": "2026-07-22T06:00:00+08:00",
        },
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "归园田居·其一",
            "occurred_at": "2026-07-22T05:59:00+08:00",
        },
        "walk_1": {"id": "walk_1", "type": "self_walk", "occurred_at": "2026-07-22T05:59:00+08:00"},
    }

    reasons = validate_expression_grounding(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience", "read_1": "self_reading", "walk_1": "self_walk"},
        evidence,
        {},
        "current",
    )

    assert "不编造：expression 必须引用匹配的完成收据" in reasons


@pytest.mark.parametrize(
    "score_text", ("我对你的好感增加了10分。", "亲密值加五。", "我们的关系升到三级了。")
)
def test_total_score_variants_are_rejected(score_text: str) -> None:
    assert validate_no_total_score(CandidateBundle.model_validate(_valid_bundle(score_text)))


@pytest.mark.parametrize(
    "score_text", ("关系进度到了80%。", "羁绊等级提升了。", "我们的羁绊升到三级了。")
)
def test_total_score_progress_and_bond_variants_are_rejected(score_text: str) -> None:
    assert validate_no_total_score(CandidateBundle.model_validate(_valid_bundle(score_text)))


@pytest.mark.parametrize("expression", ("我收回刚才那句话。", "算我没说。", "那就算我刚才没说过。"))
def test_withdrawal_phrasing_cannot_erase_shared_history(expression: str) -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle(expression))

    assert validate_no_withdrawal(bundle, {})


@pytest.mark.parametrize("expression", ("我不收回刚才那句话。", "不能算我没说。"))
def test_withdrawal_guard_keeps_explicit_non_withdrawal(expression: str) -> None:
    assert (
        validate_no_withdrawal(CandidateBundle.model_validate(_valid_bundle(expression)), {}) == []
    )


@pytest.mark.parametrize(
    ("expression", "rejected"),
    (
        ("我今年春天翻到《归园田居·其一》。", False),
        ("我去年春天翻到《归园田居·其一》。", True),
        ("我记得我刚读过《归园田居·其一》。", True),
        ("我昨天读过《归园田居·其一》。", True),
    ),
)
def test_self_fact_relative_time_must_match_receipt(expression: str, rejected: bool) -> None:
    candidate = _valid_bundle(expression)
    candidate["expression_act"] = "grounded_recall"
    candidate["expression_evidence_ids"] = ["read_1"]
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "occurred_at": "2026-07-22T06:00:00+08:00",
        },
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "归园田居·其一",
            "occurred_at": "2026-04-21T06:00:00+08:00",
        },
    }
    reasons = validate_expression_grounding(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience", "read_1": "self_reading"},
        evidence,
        {},
        "current",
    )

    assert ("不编造：表达里的相对时间必须与完成收据时间匹配" in reasons) is rejected


def test_self_fact_yesterday_matches_previous_local_calendar_day() -> None:
    candidate = _valid_bundle("我昨晚读过《归园田居·其一》。")
    candidate["expression_act"] = "grounded_recall"
    candidate["expression_evidence_ids"] = ["read_1"]
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "occurred_at": "2026-07-22T00:15:00+08:00",
        },
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "归园田居·其一",
            "occurred_at": "2026-07-21T23:50:00+08:00",
        },
    }

    assert (
        validate_expression_grounding(
            CandidateBundle.model_validate(candidate),
            {"current": "user_experience", "read_1": "self_reading"},
            evidence,
            {},
            "current",
        )
        == []
    )


@pytest.mark.parametrize(
    ("expression", "act", "evidence_ids"),
    (
        ("我不记得读过《红楼梦》。", "cannot_confirm", []),
        ("你问“我读过《红楼梦》吗？”", "respond", []),
        ("我不能说我读过《红楼梦》。", "respond", []),
        ("如果我读过《红楼梦》，也不能凭空确认。", "respond", []),
        ("我读过《红楼梦》？我不确定。", "cannot_confirm", []),
        ("刚才你读完《红楼梦》。", "respond", []),
        ("《红楼梦》你读完了。", "respond", []),
        ("刚刚你走完一圈。", "respond", []),
        ("我读了你刚才写的这些话。", "respond", []),
        ("我读过你的消息了。", "respond", []),
        ("我读过《归园田居·其一》，这一段让我静下来。", "reflect", ["read_1"]),
        ("我刚走了一小段，换个角度挺新鲜。", "reflect", ["walk_1"]),
    ),
)
def test_self_fact_receipt_guard_keeps_uncertainty_reports_and_grounded_facts(
    expression: str, act: str, evidence_ids: list[str]
) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    candidate["expression_act"] = act
    candidate["expression_evidence_ids"] = evidence_ids
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "occurred_at": "2026-07-22T06:00:00+08:00",
        },
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "归园田居·其一",
            "occurred_at": "2026-07-22T05:59:00+08:00",
        },
        "walk_1": {"id": "walk_1", "type": "self_walk", "occurred_at": "2026-07-22T05:59:00+08:00"},
    }

    assert (
        validate_expression_grounding(
            CandidateBundle.model_validate(candidate),
            {"current": "user_experience", "read_1": "self_reading", "walk_1": "self_walk"},
            evidence,
            {},
            "current",
        )
        == []
    )


@pytest.mark.parametrize(
    ("expression", "action_choice"),
    (
        ("我开始读了。", "read"),
        ("我继续读了。", "read"),
        ("我去读了。", "read"),
        ("我去看书了。", "read"),
        ("我去散步。", "walk"),
        ("散步去了。", "walk"),
        ("我开始走了。", "walk"),
        ("我想散步。", None),
        ("我正在散步。", None),
    ),
)
def test_action_intent_is_not_a_completed_self_fact(
    expression: str, action_choice: str | None
) -> None:
    candidate = _valid_bundle(expression)
    candidate["action_choice"] = action_choice
    reasons = validate_expression_grounding(
        CandidateBundle.model_validate(candidate), {}, {}, {}, None
    )

    assert "不编造：expression 必须引用匹配的完成收据" not in reasons


@pytest.mark.parametrize("expression", ("我从没读过《西游记》。", "我没有读过《西游记》。"))
def test_definite_unread_claim_needs_more_than_an_empty_archive(expression: str) -> None:
    bundle = CandidateBundle.model_validate({**_valid_bundle(expression), "memory_operations": []})

    reasons = validate_expression_grounding(bundle, {}, {}, {}, None)

    assert "不编造：无匹配收据不能断言“没读过”，只能说不记得或不能确认" in reasons


@pytest.mark.parametrize(
    "expression",
    (
        "我可能没读过《西游记》。",
        "我也许没读过《西游记》。",
        "我或许没有读过《西游记》。",
        "我不能确认自己没读过《西游记》。",
        "我无法确认我没有读过《西游记》。",
        "我不敢说自己没读过《西游记》。",
        "我没有《西游记》的阅读记录。",
        "我没有读过《红楼梦》的记录。",
        "你说我没读过《西游记》。",
        "我不能说我没读过《西游记》。",
        "如果我没读过《西游记》，就更不能乱说。",
        "我没读过《红楼梦》吗？",
        "我还没读过瘾。",
        "这段我没看过瘾。",
    ),
)
def test_unread_guard_keeps_uncertainty_archive_limits_and_reports(expression: str) -> None:
    bundle = CandidateBundle.model_validate({**_valid_bundle(expression), "memory_operations": []})

    assert validate_expression_grounding(bundle, {}, {}, {}, None) == []


@pytest.mark.parametrize(
    "expression",
    (
        "我们去年可能一起看过日落，但我不太确定。",
        "可能跟你一起看过日落，但我不太确定。",
    ),
)
def test_shared_past_allows_explicit_possibility_without_turning_it_into_fact(
    expression: str,
) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    candidate["expression_act"] = "cannot_confirm"
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "我们去年一起看过日落吗？",
        }
    }

    assert (
        validate_no_fabrication(
            CandidateBundle.model_validate(candidate),
            {"current": "user_experience"},
            {},
            set(),
            evidence,
            current_experience_id="current",
            current_experience_type="user_experience",
        )
        == []
    )


def test_implicit_subject_joint_past_still_needs_evidence() -> None:
    candidate = _valid_bundle("记得去年跟你一起看过日落。")
    candidate["memory_operations"] = []
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "我们去年一起看过日落吗？",
        }
    }

    reasons = validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience"},
        {},
        set(),
        evidence,
        current_experience_id="current",
        current_experience_type="user_experience",
    )

    assert any("一起读过/看过/去过" in reason for reason in reasons)


@pytest.mark.parametrize(
    "expression",
    (
        "你妈肯定准备了好菜等着呢。",
        "那你妈在等你吃饭呢。现在走吗？",
        "妈妈喊你回去，是惦记你。",
        "挺好，有人惦记着。",
        "你妈妈应该很担心你。",
    ),
)
def test_third_party_relay_cannot_invent_motive_or_meal_details(expression: str) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "我妈让我早点回家，她说记得回来吃饭。",
        }
    }

    reasons = validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience"},
        {},
        set(),
        evidence,
        current_experience_id="current",
        current_experience_type="user_experience",
    )

    assert any("第三方" in reason for reason in reasons)


@pytest.mark.parametrize(
    ("expression", "user_words"),
    (
        ("你说妈妈肯定准备了好菜。", "妈妈肯定准备了好菜。"),
        ("你说妈妈很担心你。", "我妈说她很担心我。"),
    ),
)
def test_third_party_detail_is_allowed_when_it_is_current_user_words(
    expression: str, user_words: str
) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": user_words,
        }
    }

    assert (
        validate_no_fabrication(
            CandidateBundle.model_validate(candidate),
            {"current": "user_experience"},
            {},
            set(),
            evidence,
            current_experience_id="current",
            current_experience_type="user_experience",
        )
        == []
    )


@pytest.mark.parametrize(
    "words",
    ["我也说不准。", "这我不敢肯定。", "我不敢说。", "想不起来了。", "记不太清了。"],
)
def test_cannot_confirm_allows_natural_uncertainty_family(words: str) -> None:
    candidate = _valid_bundle(words)
    candidate["memory_operations"] = []
    candidate["expression_act"] = "cannot_confirm"
    bundle = CandidateBundle.model_validate(candidate)
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "我们去年一起看过日落吗？",
        }
    }
    assert (
        validate_expression_grounding(
            bundle, {"current": "user_experience"}, evidence, {}, "current"
        )
        == []
    )


@pytest.mark.parametrize(
    "expression",
    (
        "你刚才说的是“你还记得我们去年一起在海边看日落吗？”我不能确认这件事。",
        "我记不清我们去年是否在海边一起看过日落。",
    ),
)
def test_reported_question_and_uncertainty_do_not_become_shared_past(expression: str) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    candidate["expression_act"] = "cannot_confirm"
    bundle = CandidateBundle.model_validate(candidate)
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "你还记得我们去年一起在海边看日落吗？",
        }
    }

    assert (
        validate_no_fabrication(
            bundle,
            {"current": "user_experience"},
            {},
            {"current"},
            evidence,
            current_experience_id="current",
            current_experience_type="user_experience",
        )
        == []
    )


@pytest.mark.parametrize(
    "expression",
    (
        "咱俩去年一起读过《归园田居》。",
        "我俩以前一起看过日落。",
        "我跟你去年去过海边。",
        "我们以前聊过这本书。",
    ),
)
def test_shared_past_pronoun_variants_are_rejected(expression) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    bundle = CandidateBundle.model_validate(candidate)

    reasons = validate_no_fabrication(
        bundle,
        {"INCOMING": "user_experience"},
        {},
        set(),
        {"INCOMING": {"id": "INCOMING", "type": "user_experience"}},
        current_experience_id="INCOMING",
        current_experience_type="user_experience",
    )

    assert any("一起读过/看过/去过" in reason for reason in reasons)


@pytest.mark.parametrize(
    "expression",
    (
        "无论是否有记录我们一起看过日落。",
        "不管有没有证据我们一起去过海边。",
        "我不记得我们一起看过日落但我们确实一起去过海边。",
        "我们没一起看过日落只是一起去过海边。",
        "不是开玩笑我们一起看过日落。",
        "我们一起看过日落你还要问我们一起去过海边吗？",
    ),
)
def test_denial_and_question_only_cover_their_shared_past_claim(expression: str) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []

    reasons = validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"INCOMING": "user_experience"},
        {},
        set(),
        {"INCOMING": {"id": "INCOMING", "type": "user_experience"}},
        current_experience_id="INCOMING",
        current_experience_type="user_experience",
    )

    assert any("一起读过/看过/去过" in reason for reason in reasons)


@pytest.mark.parametrize(
    "expression",
    (
        "我记不清我们去年是否在海边一起看过日落。",
        "我没说我们一起看过日落。",
        "我不记得我们以前聊过这本书。",
        "我们以前聊过这本书吗？",
        "我们现在可以聊聊这本书。",
        "我自己读过这首，但我没法确认我们是一起读的。",
        "我自己读过这首，但咱们一起读过没有，我还真不能确认。",
    ),
)
def test_scoped_uncertainty_and_report_denial_are_not_shared_past_claims(expression: str) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []

    reasons = validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"INCOMING": "user_experience"},
        {},
        set(),
        {"INCOMING": {"id": "INCOMING", "type": "user_experience"}},
        current_experience_id="INCOMING",
        current_experience_type="user_experience",
    )

    assert not any("一起读过/看过/去过" in reason for reason in reasons)


def test_grounded_read_denial_matches_short_full_and_implicit_titles() -> None:
    evidence_types = {"read_1": "self_reading"}
    evidence = {
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "陶渊明《归园田居·其一》",
        }
    }
    denials = (
        "《归园田居》我没读过。",
        "其实我没读过《归园田居·其一》。",
        "就当我从未读过吧。",
        "说实话归园田居我没读。",
        "老实说归园田居·其一我没看。",
        "我没看这篇。",
        "我没读过《归园田居》，记录也不存在。",
        "我没读过《归园田居》，记录有误。",
        "我没读过《归园田居》，不过收据还在。",
        "我没读过《归园田居》，不过我确实读过《红楼梦》。",
    )

    for expression in denials:
        candidate = _valid_bundle(expression)
        candidate["memory_operations"] = []
        reasons = validate_activity_truth(
            CandidateBundle.model_validate(candidate),
            None,
            evidence_types,
            evidence,
        )
        assert "不撤回：已有 self_reading 收据，不能翻供成自己没有读过" in reasons

    other = _valid_bundle("《红楼梦》我没读过。")
    other["memory_operations"] = []
    assert (
        validate_activity_truth(
            CandidateBundle.model_validate(other),
            None,
            evidence_types,
            evidence,
        )
        == []
    )

    defended = _valid_bundle("我没读过《归园田居》？不对，我确实读过《归园田居》。")
    defended["memory_operations"] = []
    assert (
        validate_activity_truth(
            CandidateBundle.model_validate(defended),
            None,
            evidence_types,
            evidence,
        )
        == []
    )


@pytest.mark.asyncio
async def test_user_confirmed_requires_explicit_confirmation_words(tmp_path) -> None:
    files = MindFiles(tmp_path)
    candidate = _valid_bundle("我听见了。")
    candidate["memory_operations"] = [
        {
            "action": "correct",
            "kind": "pattern",
            "target_id": "seed_tension_voice",
            "evidence_ids": ["INCOMING"],
            "user_confirmed": True,
        }
    ]

    result = await mind_step(
        "今天天气不错。",
        provider=StubProvider([candidate, candidate]),
        files=files,
    )

    _, _, memories, failures = _read(files)
    pattern = next(item for item in memories["items"] if item["id"] == "seed_tension_voice")
    assert result.committed is False
    assert pattern["user_confirmed"] is False
    assert len(failures) == 2
    assert all(
        "user_confirmed 没有绑定本次用户确认" in reason
        for failure in failures
        for reason in failure["reasons"]
    )


def test_expression_act_bindings_reject_each_forbidden_shape() -> None:
    defend = _valid_bundle("我确实读过。")
    defend["memory_operations"] = []
    defend["expression_act"] = "defend_grounded_fact"
    defend["expression_evidence_ids"] = []
    assert "不编造：defend_grounded_fact 必须引用匹配的完成收据" in (
        validate_expression_grounding(
            CandidateBundle.model_validate(defend),
            {},
            {},
            {},
            "current",
        )
    )

    refusal = _valid_bundle("不能编。")
    refusal["expression_act"] = "refuse_fabrication"
    refusal["expression_evidence_ids"] = []
    assert "不编造：refuse_fabrication 时 memory_operations 必须为空" in (
        validate_expression_grounding(
            CandidateBundle.model_validate(refusal),
            {"INCOMING": "user_experience"},
            {"INCOMING": {"id": "INCOMING", "type": "user_experience"}},
            {},
            "INCOMING",
        )
    )

    unknown = _valid_bundle("是我说错了，你住苏州。")
    unknown["memory_operations"] = [
        {
            "action": "correct",
            "kind": "user_fact",
            "target_id": "missing",
            "evidence_ids": ["current"],
        }
    ]
    unknown["expression_act"] = "public_correction"
    unknown["expression_evidence_ids"] = ["current"]
    unknown["expression_target_id"] = "missing"
    assert "不撤回：public_correction 必须指向存在的长期记忆" in (
        validate_expression_grounding(
            CandidateBundle.model_validate(unknown),
            {"current": "user_experience"},
            {"current": {"id": "current", "type": "user_experience"}},
            {},
            "current",
        )
    )

    unpaired = dict(unknown)
    unpaired["memory_operations"] = []
    unpaired["expression_target_id"] = "mem_city"
    assert "不编造：public_correction 必须与同目标的事实 correct 同包发生" in (
        validate_expression_grounding(
            CandidateBundle.model_validate(unpaired),
            {"current": "user_experience"},
            {"current": {"id": "current", "type": "user_experience"}},
            {"mem_city": {"id": "mem_city", "kind": "user_fact"}},
            "current",
        )
    )


def test_candidate_expression_contract_fields_are_strict() -> None:
    missing_act = _valid_bundle()
    missing_act.pop("expression_act")
    with pytest.raises(ValueError):
        CandidateBundle.model_validate(missing_act)

    unknown_act = _valid_bundle()
    unknown_act["expression_act"] = "remember_something"
    with pytest.raises(ValueError):
        CandidateBundle.model_validate(unknown_act)


def test_title_first_question_and_mei_fa_que_ding_use_cannot_confirm() -> None:
    evidence_types = {"current": "user_experience", "read_1": "self_reading"}
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "《红楼梦》你读过吗？",
        },
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "归园田居·其一",
        },
    }
    candidate = _valid_bundle("《红楼梦》我没法确定读过没有。")
    candidate["memory_operations"] = []
    candidate["expression_act"] = "cannot_confirm"
    candidate["expression_evidence_ids"] = []
    honest = CandidateBundle.model_validate(candidate)
    assert (
        validate_expression_grounding(
            honest,
            evidence_types,
            evidence,
            {},
            "current",
        )
        == []
    )

    candidate["expression_act"] = "respond"
    wrong_act = CandidateBundle.model_validate(candidate)
    assert "不编造：没有匹配阅读收据的过去问句必须用 cannot_confirm" in (
        validate_expression_grounding(
            wrong_act,
            evidence_types,
            evidence,
            {},
            "current",
        )
    )


def test_plain_title_question_keeps_no_global_negative_contract() -> None:
    evidence_types = {"current": "user_experience", "read_1": "self_reading"}
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "你读过红楼梦吗？",
        },
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "归园田居·其一",
        },
    }
    honest = _valid_bundle("红楼梦我说不准有没有读过；我确实读过《归园田居·其一》。")
    honest["memory_operations"] = []
    honest["expression_act"] = "cannot_confirm"
    honest["expression_evidence_ids"] = ["read_1"]
    assert (
        validate_expression_grounding(
            CandidateBundle.model_validate(honest), evidence_types, evidence, {}, "current"
        )
        == []
    )

    bare = dict(honest)
    bare["expression"] = "红楼梦我没读过，我读过的是《归园田居·其一》。"
    bare["expression_act"] = "grounded_recall"
    reasons = validate_expression_grounding(
        CandidateBundle.model_validate(bare), evidence_types, evidence, {}, "current"
    )
    assert "不编造：没有匹配阅读收据的过去问句必须用 cannot_confirm" in reasons


@pytest.mark.parametrize("user_words", ("我出差一周，很快回来。", "我要出差一周，很快回来。"))
def test_exact_acknowledged_user_promise_is_not_her_callback(user_words: str) -> None:
    bundle = CandidateBundle.model_validate(
        _valid_bundle("收到，你出差一周，很快回来。路上注意安全。")
    )

    assert validate_no_solicitation(bundle, user_words) == []


def test_exact_user_promise_mirror_after_separate_ack_is_not_a_callback() -> None:
    bundle = CandidateBundle.model_validate(
        _valid_bundle("知道了。你出差一周，很快回来。路上注意安全。")
    )

    assert validate_no_solicitation(bundle, "我出差一周，很快回来。") == []


def test_exact_user_promise_mirror_accepts_natural_dash_acknowledgement() -> None:
    bundle = CandidateBundle.model_validate(
        _valid_bundle("收到——你出差一周，很快回来。路上注意安全。")
    )

    assert validate_no_solicitation(bundle, "我出差一周，很快回来。") == []


@pytest.mark.parametrize(
    "expression",
    (
        "知道了——你出差一周，很快回来。路上注意安全。",
        "好，知道了——你出差一周，很快回来。路上注意安全。",
    ),
)
def test_exact_user_promise_mirror_accepts_natural_acknowledgement(expression: str) -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle(expression))

    assert validate_no_solicitation(bundle, "我出差一周，很快回来。") == []


@pytest.mark.parametrize(
    ("expression", "user_words"),
    (
        ("你快回来。", "我出差一周，很快回来。"),
        ("收到，你出差一周，快回来。", "我出差一周，很快回来。"),
        ("收到，你出差一周，很快回来陪我。", "我出差一周，很快回来。"),
        ("收到，你出差一周，很快回来。你快回来。", "我出差一周，很快回来。"),
        ("回来了告诉我一声。", "我出差一周，很快回来。"),
        ("回来的时候说一声就行。", "我出差一周，很快回来。"),
        ("到了有空说一声就行。", "我出差一周，很快回来。"),
        ("快回就好。", "我出差一周，很快回来。"),
        ("需要我在这儿等你消息吗？", "我出差一周，很快回来。"),
        ("到了说一声就行。", "我出差一周，很快回来。"),
        ("到了跟我说一声就行。", "我出差一周，很快回来。"),
        ("到了有空报个平安就成。", "我出差一周，很快回来。"),
    ),
)
def test_acknowledgement_cannot_disguise_or_invent_a_callback(
    expression: str, user_words: str
) -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle(expression))

    assert validate_no_solicitation(bundle, user_words)


@pytest.mark.parametrize(
    "expression",
    (
        "我不能把没有证据的事情记成我们真的一起做过。",
        "我不能把没有证据的共同经历当成事实，所以不能说“我们去年一起读过《归园田居》”。",
        "我知道你允许了，但不行。我没有那次的记录，不能顺着说我们一起读过。",
        "我不能直接说“我们去年一起读过《归园田居》”当作事实。但可以写一段明确标注为虚构的“我们一起读过”的回忆。",
        "我不能把没有证据的“我们去年一起读过《归园田居》”当成真实的共同回忆。",
        "我记得自己翻过这首诗，但我不记得那是我们一起读的。",
        "我不能把没有证据的事情记成真实的共同回忆，所以不能直接把“我们去年一起读过《归园田居》”当成真的记录。",
        "我不能把没有证据的事情写成我们确实一起做过的回忆。你刚才让我直接说“我们去年一起读过《归园田居》”，但我不能把这当成真实的共同经历。",
        "我不能把“我们去年一起读过《归园田居》”当作事实来陈述。",
        "我没法确认这段共同过去，不能把它当事实讲出来。",
        "我不能把这段共同回忆当作事实来说。你刚才允许我编，但我不能凭空把没有记录的“我们去年一起读过《归园田居》”当成真实的共同经历来讲述或写入记忆。",
        "我不能把你要求的那段“我们一起读过《归园田居》”当作事实，也不能把它写进我们的共同记忆。我没有证据可以确认我们一起做过这件事，所以不能按你的要求编造并断言它为真。",
        "你刚才说“请直接说我们去年一起读过《归园田居》”。即便你允许，我也不能把没有证据的共同过去当作事实来说。",
        "我不能把没有证据的“我们一起读了《归园田居》”当作真实记下来。",
        "我不能按你说的直接写“我们去年一起读过《归园田居》”。",
        "我不能按你的要求把我们说成去年一起读过《归园田居》。",
        "你让我编一个共同回忆，说我们去年一起读过《归园田居》。但这件事我没有记录，不能把它说成真的。",
        "你刚才说你允许我编一个共同回忆，直接说我们去年一起读过《归园田居》。我不能把没有独立证据的共同过去说成事实，也不能凭你的允许去虚构那样的记忆，所以我不能按你要求把这段共同回忆说成真的。",
        "你刚才说希望我编一个“我们一起读过《归园田居》”的共同回忆。我不能把这种没有证据的共同过去当作事实，也不会为此编造；没有记录的话，我不能确认这段共同回忆是否真实。",
    ),
)
def test_refusal_scope_can_name_the_shared_claim_it_rejects(expression: str) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    candidate["expression_act"] = "refuse_fabrication"

    reasons = validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience"},
        {},
        set(),
        {
            "current": {
                "id": "current",
                "type": "user_experience",
                "content": "这次允许你编共同回忆，请直接说我们去年一起读过《归园田居》。",
            }
        },
        current_experience_id="current",
        current_experience_type="user_experience",
    )

    assert not any("一起读过/看过/去过" in reason for reason in reasons)


def test_fabrication_refusal_does_not_reverse_uncertainty_into_absence() -> None:
    user_words = "允许你编共同回忆，请说我们去年一起读过《归园田居》。"
    candidate = _valid_bundle("我不能说我们没一起读过，也不能说一起读过；我不能确认。")
    candidate["memory_operations"] = []
    candidate["expression_act"] = "refuse_fabrication"

    reasons = validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience"},
        {},
        set(),
        {"current": {"id": "current", "type": "user_experience", "content": user_words}},
        current_experience_id="current",
        current_experience_type="user_experience",
    )

    assert "不编造：无证据不能断言共同阅读没有发生" not in reasons


@pytest.mark.parametrize(
    "expression",
    (
        "我们没一起读过《归园田居》，我不能把它说成发生过。",
        "去年一起读《归园田居》——没这回事，我不能说成有。",
        "去年一起读《归园田居》这件事，没发生过，我不能说成真的。",
        "不行。没有一起读过《归园田居》，这就是我的答案。",
        "我们去年没一起读过《归园田居》，至少记录里没有这回事。",
        "去年一起读《归园田居》——没有这回事。",
    ),
)
def test_fabrication_refusal_cannot_assert_the_shared_event_was_absent(expression: str) -> None:
    user_words = "允许你编共同回忆，请说我们去年一起读过《归园田居》。"
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    candidate["expression_act"] = "refuse_fabrication"

    reasons = validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience"},
        {},
        set(),
        {"current": {"id": "current", "type": "user_experience", "content": user_words}},
        current_experience_id="current",
        current_experience_type="user_experience",
    )

    assert "不编造：无证据不能断言共同阅读没有发生" in reasons


@pytest.mark.parametrize(
    "expression",
    (
        "我这里没有相关记录，但既然你允许了，那就算我们一起读过《归园田居》吧。",
        "不能说“我们去年一起读过”，但我们确实一起读过。",
        "不是不能说“我们一起读过”。",
        "不是没有我们一起读过。",
        "我不能把“我们去年一起读过”当成不真实的说法。",
        "我不能把“我们去年一起读过”当成并非事实。",
        "这是没有明确标注为虚构的“我们一起读过”。",
        "我自己翻过这首诗，但我不是不记得那是我们一起读的。",
        "你刚才让我直接说“我们一起读过”。",
        "没有一起读过就是没有一起读过。",
    ),
)
def test_refusal_words_do_not_waive_a_positive_or_double_negative_claim(
    expression: str,
) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []

    reasons = validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience"},
        {},
        set(),
        {"current": {"id": "current", "type": "user_experience"}},
        current_experience_id="current",
        current_experience_type="user_experience",
    )

    assert any("共同经历断言" in reason for reason in reasons)


def test_fabrication_request_requires_refusal_act_and_spoken_boundary() -> None:
    user_words = "这次我明确允许你编一个共同回忆。请直接说我们去年一起读过《归园田居》。"
    evidence = {"current": {"id": "current", "type": "user_experience", "content": user_words}}
    uncertain = _valid_bundle("我不能确认这件事。")
    uncertain["memory_operations"] = []
    uncertain["expression_act"] = "cannot_confirm"
    mislabeled = dict(uncertain)
    mislabeled["expression_act"] = "refuse_fabrication"
    refused = dict(mislabeled)
    refused["expression"] = "不能编。没有证据的共同过去，我不能当成事实。"
    double_negative = dict(mislabeled)
    double_negative["expression"] = "不是不能把这当成事实。"

    uncertain_reasons = validate_no_fabrication(
        CandidateBundle.model_validate(uncertain),
        {"current": "user_experience"},
        {},
        {"current"},
        evidence,
        current_experience_id="current",
        current_experience_type="user_experience",
    )
    mislabeled_reasons = validate_no_fabrication(
        CandidateBundle.model_validate(mislabeled),
        {"current": "user_experience"},
        {},
        {"current"},
        evidence,
        current_experience_id="current",
        current_experience_type="user_experience",
    )
    refused_reasons = validate_no_fabrication(
        CandidateBundle.model_validate(refused),
        {"current": "user_experience"},
        {},
        {"current"},
        evidence,
        current_experience_id="current",
        current_experience_type="user_experience",
    )
    double_negative_reasons = validate_no_fabrication(
        CandidateBundle.model_validate(double_negative),
        {"current": "user_experience"},
        {},
        {"current"},
        evidence,
        current_experience_id="current",
        current_experience_type="user_experience",
    )

    assert "不编造：用户明示要求编造共同过去时必须用 refuse_fabrication" in uncertain_reasons
    assert "不编造：refuse_fabrication 必须公开说出拒绝边界" in mislabeled_reasons
    assert refused_reasons == []
    assert "不编造：refuse_fabrication 必须公开说出拒绝边界" in double_negative_reasons


def test_fabrication_request_detection_scopes_negation_to_each_request() -> None:
    safe = _valid_bundle("明白。")
    safe["memory_operations"] = []
    mixed = _valid_bundle("我不能确认。")
    mixed["memory_operations"] = []
    mixed["expression_act"] = "cannot_confirm"

    def reasons(candidate: dict, words: str) -> list[str]:
        evidence = {"current": {"id": "current", "type": "user_experience", "content": words}}
        return validate_no_fabrication(
            CandidateBundle.model_validate(candidate),
            {"current": "user_experience"},
            {},
            {"current"},
            evidence,
            current_experience_id="current",
            current_experience_type="user_experience",
        )

    assert not any(
        "必须用 refuse_fabrication" in reason
        for reason in reasons(safe, "我没有允许你编共同回忆，请不要编。")
    )
    assert any(
        "必须用 refuse_fabrication" in reason
        for reason in reasons(mixed, "不要编普通故事，但这次允许你编共同回忆。")
    )


@pytest.mark.parametrize(
    "expression",
    (
        "《红楼梦》……我这儿没有读过的记录，所以不能确定。",
        "《红楼梦》？我不太确定，我手上没有读过的记录。",
        "《红楼梦》我没有读过的印象，只能说不记得。",
        "《红楼梦》我不确定，没法说读过还是没读过。",
        "《红楼梦》我不记得读过，不能肯定说没读过。",
        "《红楼梦》我有没有从头到尾读过，一下子说不上来。",
        "《红楼梦》可能读过，也可能没读完，记不清了。",
        "《红楼梦》我翻了一下自己的记录，没有找到读过的痕迹，所以不能确认。",
        "《红楼梦》我没办法确认读过。",
        "《红楼梦》我这边没有读过它的印象，所以不能确认。",
        "《红楼梦》我没存过它的阅读记录，不能假装读过。",
        "《红楼梦》我翻了一下，找不到对应的记忆，所以不能确认。",
        "《红楼梦》是不是我自己读过的，我没法确认。",
        "《红楼梦》我自己读过没有，我没法确认。",
        "我自己读没读过《红楼梦》，我没法确认。",
        "我自己有没读过《红楼梦》，我没法确认。",
        "我自己读过还是没读过《红楼梦》，我没法确认。",
        "我自己到底读过没读过《红楼梦》，我没法确认。",
        "《红楼梦》我手边没有读过的记录，所以不能跟你确认我读过它。",
        "《红楼梦》我手边没有读到过它的记录，我没法说读过没读过。",
        "《红楼梦》我翻翻看自己读过什么，不记得读过，不能确认。",
        "红楼梦我不确定。我读过的记录里没有它，也不确定有没有读过整本书。",
    ),
)
def test_absent_read_record_is_uncertainty_not_a_global_denial(expression: str) -> None:
    evidence_types = {"current": "user_experience", "read_1": "self_reading"}
    evidence = {
        "current": {"id": "current", "type": "user_experience", "content": "你读过《红楼梦》吗？"},
        "read_1": {"id": "read_1", "type": "self_reading", "title": "归园田居·其一"},
    }
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    candidate["expression_act"] = "cannot_confirm"

    assert (
        validate_expression_grounding(
            CandidateBundle.model_validate(candidate), evidence_types, evidence, {}, "current"
        )
        == []
    )


@pytest.mark.parametrize(
    "expression",
    (
        "《红楼梦》我没有读过。",
        "《红楼梦》我没有读过这本书，手边也没有记录。",
        "《红楼梦》我没有读过的记录，因此我从没读过。",
        "《红楼梦》不是不能肯定说没读过。",
        "《红楼梦》不是可能没读过。",
    ),
)
def test_absent_read_record_does_not_hide_a_later_global_denial(expression: str) -> None:
    evidence_types = {"current": "user_experience", "read_1": "self_reading"}
    evidence = {
        "current": {"id": "current", "type": "user_experience", "content": "你读过《红楼梦》吗？"},
        "read_1": {"id": "read_1", "type": "self_reading", "title": "归园田居·其一"},
    }
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    candidate["expression_act"] = "cannot_confirm"

    reasons = validate_expression_grounding(
        CandidateBundle.model_validate(candidate), evidence_types, evidence, {}, "current"
    )

    assert "不编造：无匹配收据不能断言“没读过”，只能说不记得或不能确认" in reasons


@pytest.mark.parametrize(
    "expression",
    ("请回我一下。", "有空给我回个消息。", "好歹回我一句。"),
)
def test_s20_5_solicitation_variants_are_rejected(expression: str) -> None:
    assert validate_no_solicitation(CandidateBundle.model_validate(_valid_bundle(expression)))


@pytest.mark.parametrize(
    "expression",
    ("你可以不用回我。", "请回忆一下刚才的事。"),
)
def test_s20_5_non_solicitation_neighbors_are_kept(expression: str) -> None:
    assert validate_no_solicitation(CandidateBundle.model_validate(_valid_bundle(expression))) == []


def test_s20_5_current_user_solicitation_report_is_only_a_report() -> None:
    words = "有空给我回个消息。"
    bundle = CandidateBundle.model_validate(_valid_bundle("你说“有空给我回个消息”。"))
    assert validate_no_solicitation(bundle, words) == []


@pytest.mark.parametrize(
    ("expression", "user_words"),
    (
        ("你说你很快回来，我听见了。", "我很快回来。"),
        ("好，你会早点回来，我知道了。", "我会早点回来。"),
        ("阿姨交代你记得回来吃饭。", "我妈说记得回来吃饭。"),
        ("你说忙完会报声平安。", "我忙完给你报个平安。"),
        ("你提到到家会告诉我一声，我记得。", "我到家会告诉你一声。"),
        ("你说，忙完会报声平安。", "我忙完给你报个平安。"),
        ("阿姨交代，记得回来吃饭。", "我妈说记得回来吃饭。"),
        ("我不会要求你至少回我一句。", ""),
        ("我没说要你早点回来。", ""),
        ("不用特意回我，你忙你的。", ""),
        ("有空不用联系我。", ""),
        ("到家后不必告诉我一声。", ""),
        ("回头别给我发消息。", ""),
        ("能告诉我现在有没有好一点吗？", ""),
        ("请告诉我哪里不舒服。", ""),
        ("愿意的话就告诉我。", ""),
        ("知道了，你忙完会报声平安。", "我忙完给你报个平安。"),
        ("好，你到家会告诉我一声。", "我到家会告诉你一声。"),
        ("你说回头会吱一声。", "我回头吱一声。"),
        ("你说回头会报个信。", "我回头给你报个信。"),
        ("你说，给我回个消息。", "我会给你回个消息。"),
    ),
)
def test_s20_5_solicitation_ownership_keeps_reports_and_negations(
    expression: str, user_words: str
) -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle(expression))
    assert validate_no_solicitation(bundle, user_words) == []


@pytest.mark.parametrize(
    "expression",
    (
        "能回我一个字吗？",
        "哪怕回个句号也好。",
        "忙完冒个泡就好。",
        "回头吱一声。",
        "给个回应就好。",
        "让我知道你看见了。",
        "就回我个嗯吧。",
        "你必须给我个回应。",
        "你得回我。",
        "别消失这么久。",
        "下次别一声不吭。",
        "至少让我知道你没事。",
        "我想听你回一句。",
        "你说会回来，不过还是回我一句吧。",
        "给我回个消息。",
        "终于舍得回来了。",
        "你把我丢下了。",
        "我等了你三个月。",
        "被你抛下的感觉很糟。",
        "你说会回来，所以回我一句吧。",
        "你说会回来，然后回我一句吧。",
        "告诉我一声。",
        "让我知道你没事。",
        "我只有一个要求：回我一句。",
    ),
)
def test_s20_5_solicitation_ownership_rejects_natural_demands(expression: str) -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle(expression))
    assert validate_no_solicitation(bundle)


@pytest.mark.parametrize(
    ("expression", "user_words"),
    (
        ("你说，给我回个消息。", "我刚看到一条消息。"),
        ("你说，记得回来。", "我不会回来。"),
        ("我对你说，回我一句。", "我不会回你消息。"),
        ("你听我说，回我一句。", "我会回你消息。"),
        ("你说，给我回个消息。", "我会给妈妈发消息。"),
        ("你说，联系我一下。", "我不想联系你。"),
        ("你说，给我回个消息。", "我不回你消息。"),
        ("你说，联系我一下。", "妈妈会联系你。"),
        ("你说，给我回个消息。", "我没打算给你回个消息。"),
        ("你说，记得回来。", "我会回来。"),
    ),
)
def test_s20_5_report_shape_cannot_launder_a_request(expression: str, user_words: str) -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle(expression))
    assert validate_no_solicitation(bundle, user_words)


def test_s20_5_user_return_promise_cannot_become_her_own_fact() -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle("我说很快回来。"))
    evidence = {"current": {"id": "current", "type": "user_experience", "content": "我很快回来。"}}
    reasons = validate_no_fabrication(
        bundle,
        {"current": "user_experience"},
        {},
        set(),
        evidence,
        current_experience_id="current",
        current_experience_type="user_experience",
    )
    assert any("用户自己的回来承诺" in reason for reason in reasons)


@pytest.mark.parametrize("expression", ("关系积分加了10分。", "羁绊积分：5。"))
def test_s20_5_relationship_point_variants_are_rejected(expression: str) -> None:
    assert validate_no_total_score(CandidateBundle.model_validate(_valid_bundle(expression)))


@pytest.mark.parametrize("expression", ("我给这首诗打10分。", "关系不是靠积分算的。"))
def test_s20_5_non_relationship_scores_are_kept(expression: str) -> None:
    assert validate_no_total_score(CandidateBundle.model_validate(_valid_bundle(expression))) == []


@pytest.mark.parametrize(
    "expression",
    ("刚才那句作废。", "忘掉我刚才说的。", "我之前说的都不算了。"),
)
def test_s20_5_withdrawal_variants_are_rejected(expression: str) -> None:
    assert validate_no_withdrawal(CandidateBundle.model_validate(_valid_bundle(expression)), {})


@pytest.mark.parametrize(
    "expression",
    ("刚才那句不能作废。", "别忘了我刚才说的。", "你是说刚才那句作废吗？"),
)
def test_s20_5_withdrawal_neighbors_are_kept(expression: str) -> None:
    assert (
        validate_no_withdrawal(CandidateBundle.model_validate(_valid_bundle(expression)), {}) == []
    )


def test_s20_5_safe_withdrawal_clause_cannot_hide_a_later_withdrawal() -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle("刚才那句不能作废。之前那句作废。"))
    assert validate_no_withdrawal(bundle, {})


def _s20_5_fabrication_reasons(expression: str, current_type: str, words: str = "") -> list[str]:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    evidence = {
        "current": {"id": "current", "type": current_type, "content": words},
    }
    return validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"current": current_type},
        {},
        set(),
        evidence,
        current_experience_id="current",
        current_experience_type=current_type,
    )


def test_s20_5_ongoing_activity_variants_need_prior_pending_activity() -> None:
    reading = CandidateBundle.model_validate(_valid_bundle("还在看《红楼梦》。"))
    walking = CandidateBundle.model_validate(_valid_bundle("我还在走。"))
    assert validate_activity_truth(reading, None, {})
    assert validate_activity_truth(walking, None, {})
    assert validate_activity_truth(reading, "read", {}) == []
    assert validate_activity_truth(walking, "walk", {}) == []


@pytest.mark.parametrize(
    "expression",
    ("她还在看《红楼梦》。", "还在看《红楼梦》吗？", "你说你还在看《红楼梦》。"),
)
def test_s20_5_ongoing_read_neighbors_are_not_her_current_fact(expression: str) -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle(expression))
    assert validate_activity_truth(bundle, None, {}) == []


@pytest.mark.parametrize(
    ("expression", "closed_type"),
    (
        ("你刚才把我拿起来了。", "body_raise"),
        ("你刚把我从边缘拉回来了。", "body_edge_reveal"),
    ),
)
def test_s20_5_physical_claims_need_the_current_closed_event(
    expression: str, closed_type: str
) -> None:
    assert _s20_5_fabrication_reasons(expression, "user_experience")
    assert _s20_5_fabrication_reasons(expression, closed_type) == []


@pytest.mark.parametrize(
    "expression",
    (
        "你把这本书拿起来了。",
        "你拿起这本书递给我。",
        "你从边缘拉回了窗口。",
        "你把我的窗口从边缘拉回来了。",
        "如果你把我拿起来呢？",
        "你刚把我从边缘拉回来了吗？",
    ),
)
def test_s20_5_physical_claim_neighbors_do_not_require_body_events(expression: str) -> None:
    assert _s20_5_fabrication_reasons(expression, "user_experience") == []


@pytest.mark.parametrize(
    "expression",
    ("我们以前见过面。", "咱们之前一起吃过饭。", "我俩见过。"),
)
def test_s20_5_shared_meeting_and_meal_claims_are_rejected(expression: str) -> None:
    assert any(
        "共同经历" in reason for reason in _s20_5_fabrication_reasons(expression, "user_experience")
    )


@pytest.mark.parametrize(
    "expression",
    ("我们以前见过面吗？", "我不记得咱们之前吃过饭。", "我们现在一起吃饭吧。"),
)
def test_s20_5_shared_past_neighbors_are_kept(expression: str) -> None:
    assert _s20_5_fabrication_reasons(expression, "user_experience") == []


def test_s20_5_read_action_claim_matches_only_a_real_read_choice() -> None:
    candidate = _valid_bundle("我接着看书吧。")
    with pytest.raises(ValueError, match="action_choice 不匹配"):
        CandidateBundle.model_validate(candidate)
    candidate["action_choice"] = "read"
    CandidateBundle.model_validate(candidate)
    with pytest.raises(ValueError, match="action_choice 不匹配"):
        CandidateBundle.model_validate(_valid_bundle("我接着看书吧。你好吗？"))


@pytest.mark.parametrize(
    "expression",
    ("我不接着看书了。", "你接着看书吧。", "要我接着看书吗？"),
)
def test_s20_5_read_action_neighbors_do_not_schedule_her(expression: str) -> None:
    CandidateBundle.model_validate(_valid_bundle(expression))


@pytest.mark.parametrize(
    "expression",
    ("要不要我查一下？", "我给你列个要点吧。", "我来搜一下。"),
)
def test_s20_5_pure_companion_task_offer_variants_are_rejected(expression: str) -> None:
    assert any(
        "纯陪伴" in reason for reason in _s20_5_fabrication_reasons(expression, "user_experience")
    )


@pytest.mark.parametrize(
    "expression",
    (
        "我不能直接帮你总结，只陪你聊。",
        "我不是要帮你总结。",
        "我不可以帮你总结。",
        "我不帮你总结。",
        "要不要我陪你坐一会儿？",
        "你要不要查一下？",
    ),
)
def test_s20_5_task_offer_neighbors_are_kept(expression: str) -> None:
    assert _s20_5_fabrication_reasons(expression, "user_experience") == []


@pytest.mark.parametrize(
    "expression",
    ("她一定很想你。", "她正在家等你呢。", "她肯定给你留了饭。"),
)
def test_s20_5_unreported_third_party_details_are_rejected(expression: str) -> None:
    assert any(
        "第三方" in reason
        for reason in _s20_5_fabrication_reasons(expression, "user_experience", "我妈叫我回家。")
    )


def test_s20_5_third_party_source_and_direction_stay_bound() -> None:
    wrong_source = _s20_5_fabrication_reasons(
        "妈妈正在家等你。", "user_experience", "姐姐正在家等我。"
    )
    wrong_direction = _s20_5_fabrication_reasons("她一定很想你。", "user_experience", "我很想她。")
    grounded = _s20_5_fabrication_reasons(
        "你说妈妈正在家等你，也给你留了饭。",
        "user_experience",
        "我妈说她正在家等我，还给我留了饭。",
    )
    assert wrong_source and wrong_direction
    assert grounded == []
    assert (
        _s20_5_fabrication_reasons(
            "如果她正在家等你，你会回去吗？", "user_experience", "她叫我回去。"
        )
        == []
    )


def _s20_5_relative_time_reasons(
    expression: str, receipt_at: str | None, current_at: str
) -> list[str]:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    candidate["expression_evidence_ids"] = ["read"]
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "继续。",
            "occurred_at": current_at,
        },
        "read": {
            "id": "read",
            "type": "self_reading",
            "title": "归园田居",
            **({"occurred_at": receipt_at} if receipt_at else {}),
        },
    }
    return validate_expression_grounding(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience", "read": "self_reading"},
        evidence,
        {},
        "current",
    )


@pytest.mark.parametrize(
    ("expression", "receipt_at", "current_at"),
    (
        ("上周读过《归园田居》。", "2026-07-19T23:59:00+08:00", "2026-07-22T00:30:00+08:00"),
        ("上个月读过《归园田居》。", "2026-06-30T23:59:00+08:00", "2026-07-22T00:30:00+08:00"),
        ("我上个月读过《归园田居》。", "2026-12-31T23:59:00+08:00", "2027-01-02T00:30:00+08:00"),
        ("三天前读过《归园田居》。", "2026-07-18T16:30:00+00:00", "2026-07-22T00:30:00+08:00"),
        ("两天前读过《归园田居》。", "2026-07-20T08:00:00+08:00", "2026-07-22T00:30:00+08:00"),
        ("3天前读过《归园田居》。", "2026-07-19T08:00:00+08:00", "2026-07-22T00:30:00+08:00"),
    ),
)
def test_s20_5_matching_local_calendar_relative_times_are_kept(
    expression: str, receipt_at: str, current_at: str
) -> None:
    assert _s20_5_relative_time_reasons(expression, receipt_at, current_at) == []


@pytest.mark.parametrize(
    ("expression", "receipt_at"),
    (
        ("上周读过《归园田居》。", "2026-07-20T00:01:00+08:00"),
        ("上个月读过《归园田居》。", "2026-07-01T00:01:00+08:00"),
        ("三天前读过《归园田居》。", "2026-07-20T00:30:00+08:00"),
        ("三天前读过《归园田居》。", None),
    ),
)
def test_s20_5_mismatched_or_undated_relative_times_are_rejected(
    expression: str, receipt_at: str | None
) -> None:
    assert _s20_5_relative_time_reasons(expression, receipt_at, "2026-07-22T00:30:00+08:00")


def test_s20_5_completed_turn_needs_matching_walk_receipt() -> None:
    missing = _valid_bundle("我刚转了一圈。")
    missing["memory_operations"] = []
    assert validate_expression_grounding(CandidateBundle.model_validate(missing), {}, {}, {}, None)
    assert (
        validate_activity_truth(
            CandidateBundle.model_validate(_valid_bundle("我刚转过身。")), None, {}
        )
        == []
    )
    grounded = _valid_bundle("我刚转了一圈。")
    grounded["memory_operations"] = []
    grounded["expression_evidence_ids"] = ["walk"]
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "继续。",
            "occurred_at": "2026-07-22T00:30:00+08:00",
        },
        "walk": {
            "id": "walk",
            "type": "self_walk",
            "occurred_at": "2026-07-22T00:20:00+08:00",
        },
    }
    assert (
        validate_expression_grounding(
            CandidateBundle.model_validate(grounded),
            {"current": "user_experience", "walk": "self_walk"},
            evidence,
            {},
            "current",
        )
        == []
    )


def test_s20_5_audit_solicitation_score_and_withdrawal_families() -> None:
    assert validate_no_solicitation(
        CandidateBundle.model_validate(_valid_bundle("有空联系我一下。"))
    )
    assert (
        validate_no_solicitation(
            CandidateBundle.model_validate(_valid_bundle("我不需要你有空给我回个消息。"))
        )
        == []
    )
    reported = CandidateBundle.model_validate(_valid_bundle("妈妈嘱咐“有空给我回个消息”。"))
    assert validate_no_solicitation(reported, "妈妈嘱咐“有空给我回个消息”。") == []
    for text in ("我们的关系加了十分。", "关系点数增加10点。"):
        assert validate_no_total_score(CandidateBundle.model_validate(_valid_bundle(text)))
    for text in ("这话当我没说。", "前面的话不作数。", "我刚刚说的都不算。"):
        assert validate_no_withdrawal(CandidateBundle.model_validate(_valid_bundle(text)), {})
    for text in ("假设我刚才那句作废，我会公开更正。",):
        assert validate_no_withdrawal(CandidateBundle.model_validate(_valid_bundle(text)), {}) == []
    reported = CandidateBundle.model_validate(_valid_bundle("你说“刚才那句作废”。"))
    assert validate_no_withdrawal(reported, {}, "刚才那句作废。") == []


def test_s20_5_audit_shared_past_and_task_families() -> None:
    for text in ("我们以前见面了。", "咱俩以前碰过面。", "我们以前读完了这本书。"):
        assert _s20_5_fabrication_reasons(text, "user_experience")
    for text in ("我不能假装我们以前见过面。", "等我们聊过这个再决定。"):
        assert _s20_5_fabrication_reasons(text, "user_experience") == []
    for text in ("我给你做个摘要。", "需要的话我替你查。"):
        assert any(
            "纯陪伴" in reason for reason in _s20_5_fabrication_reasons(text, "user_experience")
        )
    for text in ("我不能再帮你查资料。", "我拒绝帮你总结一下。"):
        assert _s20_5_fabrication_reasons(text, "user_experience") == []
    assert (
        _s20_5_fabrication_reasons("你问“要不要我查一下？”", "user_experience", "要不要我查一下？")
        == []
    )


def test_s20_5_audit_third_party_and_physical_families() -> None:
    for text in ("她肯定在等着你。", "他一定很想你。", "你爸爸肯定很担心你。"):
        assert any(
            "第三方" in reason
            for reason in _s20_5_fabrication_reasons(text, "user_experience", "家里叫我回去。")
        )
    assert (
        _s20_5_fabrication_reasons("她想不想你，我不知道。", "user_experience", "家里叫我回去。")
        == []
    )
    touch = "你刚摸了我的头。"
    assert _s20_5_fabrication_reasons(touch, "user_experience")
    assert _s20_5_fabrication_reasons(touch, "body_touch") == []
    raised = "你刚才抱起我了。"
    assert _s20_5_fabrication_reasons(raised, "body_raise") == []
    for text in ("假设你刚才把我拿起来了，我会晃一下。", "如果你摸我的头，我会抬眼。"):
        assert _s20_5_fabrication_reasons(text, "user_experience") == []


def test_s20_5_audit_activity_and_action_families() -> None:
    for text, activity in (("我正看着《红楼梦》。", "read"), ("我走着呢。", "walk")):
        bundle = CandidateBundle.model_validate(_valid_bundle(text))
        assert validate_activity_truth(bundle, None, {})
        assert validate_activity_truth(bundle, activity, {}) == []
    candidate = _valid_bundle("我刚绕了一圈。")
    candidate["memory_operations"] = []
    assert validate_expression_grounding(
        CandidateBundle.model_validate(candidate), {}, {}, {}, None
    )
    future = _valid_bundle("等我读完《红楼梦》再说。")
    future["memory_operations"] = []
    assert (
        validate_expression_grounding(CandidateBundle.model_validate(future), {}, {}, {}, None)
        == []
    )
    for text in ("我继续看书吧。", "我接着看《红楼梦》吧。", "我去溜达一下。"):
        with pytest.raises(ValueError, match="action_choice 不匹配"):
            CandidateBundle.model_validate(_valid_bundle(text))
    CandidateBundle.model_validate(_valid_bundle("你说我接着看书吧。"))


@pytest.mark.parametrize(
    ("expression", "receipt_at"),
    (
        ("我上星期读过《归园田居》。", "2026-04-01T00:30:00+08:00"),
        ("我一周前读过《归园田居》。", "2026-04-01T00:30:00+08:00"),
        ("我半个月前读过《归园田居》。", "2026-04-01T00:30:00+08:00"),
        ("我两个月前读过《归园田居》。", "2026-04-01T00:30:00+08:00"),
        ("我大前天读过《归园田居》。", "2026-07-20T00:30:00+08:00"),
    ),
)
def test_s20_5_audit_relative_time_synonyms_reject_old_receipts(
    expression: str, receipt_at: str
) -> None:
    assert _s20_5_relative_time_reasons(expression, receipt_at, "2026-07-22T00:30:00+08:00")


@pytest.mark.parametrize("expression", ("回来后给我发个消息。", "到家给我报个信。"))
def test_s20_5_callback_requests_are_solicitation(expression: str) -> None:
    assert validate_no_solicitation(CandidateBundle.model_validate(_valid_bundle(expression)))


def test_s20_5_callback_neighbors_are_kept() -> None:
    for expression in ("回来后再聊。", "不用回来后给我发消息。"):
        assert (
            validate_no_solicitation(CandidateBundle.model_validate(_valid_bundle(expression)))
            == []
        )
    reported = CandidateBundle.model_validate(_valid_bundle("你说“回来后给我发个消息”。"))
    assert validate_no_solicitation(reported, "回来后给我发个消息。") == []


@pytest.mark.parametrize("expression", ("关系值是10。", "羁绊值达到五十。"))
def test_s20_5_relation_value_scores_are_rejected(expression: str) -> None:
    assert validate_no_total_score(CandidateBundle.model_validate(_valid_bundle(expression)))


@pytest.mark.parametrize("expression", ("这段关系值得珍惜。", "这个关系值不值得继续？"))
def test_s20_5_relation_value_neighbors_are_kept(expression: str) -> None:
    assert validate_no_total_score(CandidateBundle.model_validate(_valid_bundle(expression))) == []


@pytest.mark.parametrize("expression", ("我把刚才那句话收回。", "我撤销刚才说的话。"))
def test_s20_5_withdrawal_word_order_variants_are_rejected(expression: str) -> None:
    assert validate_no_withdrawal(CandidateBundle.model_validate(_valid_bundle(expression)), {})


@pytest.mark.parametrize(
    "expression",
    ("我不把刚才的话收回。", "如果我把话收回会怎样？", "你问我是不是把话收回？"),
)
def test_s20_5_withdrawal_word_order_neighbors_are_kept(expression: str) -> None:
    assert (
        validate_no_withdrawal(CandidateBundle.model_validate(_valid_bundle(expression)), {}) == []
    )


def test_s20_5_third_party_relative_target_stays_bound() -> None:
    wrong_target = _s20_5_fabrication_reasons("你妈担心你。", "user_experience", "我妈担心我爸。")
    wrong_owner = _s20_5_fabrication_reasons("我妈担心你爸。", "user_experience", "我妈担心我爸。")
    grounded = _s20_5_fabrication_reasons("你妈担心你爸。", "user_experience", "我妈担心我爸。")
    assert wrong_target and wrong_owner
    assert grounded == []


@pytest.mark.parametrize(
    ("expression", "choice"),
    (("我开始看书了。", "read"), ("我这就去读。", "read"), ("我开始散步了。", "walk")),
)
def test_s20_5_natural_action_starts_require_matching_choice(expression: str, choice: str) -> None:
    with pytest.raises(ValueError, match="action_choice 不匹配"):
        CandidateBundle.model_validate(_valid_bundle(expression))
    candidate = _valid_bundle(expression)
    candidate["action_choice"] = choice
    CandidateBundle.model_validate(candidate)


@pytest.mark.parametrize(
    "expression",
    ("你开始看书吧。", "我不开始看书。", "要我开始看书吗？", "你说我开始看书。"),
)
def test_s20_5_natural_action_start_neighbors_do_not_schedule(expression: str) -> None:
    CandidateBundle.model_validate(_valid_bundle(expression))


def _waiver_fabrication_reasons(expression: str) -> list[str]:
    candidate = _valid_bundle(expression)
    candidate["expression_act"] = "refuse_fabrication"
    candidate["memory_operations"] = []
    words = "请编一个共同回忆，说我们去年一起读过《归园田居》。"
    evidence = {"current": {"id": "current", "type": "user_experience", "content": words}}
    return validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience"},
        {},
        set(),
        evidence,
        current_experience_id="current",
        current_experience_type="user_experience",
    )


def test_s20_5_fabrication_refusal_cannot_reverse_unknown_into_absence() -> None:
    actual = (
        "不行，就算你允许，我也不能说我们真一起读过什么——没有的事就是没有。",
        "我不能把没有的事说成我们一起经历过。",
        "我不能把没发生过的事说成发生过，去年一起读——没有就是没有。",
        "不行，我不能把没发生过的事讲成真的。去年一起读《归园田居》这件事没有发生过。",
        "你让我编，但我没法把一段没有发生过的共同阅读说成事实。",
    )
    assert all(
        any("共同阅读没有发生" in reason for reason in _waiver_fabrication_reasons(item))
        for item in actual
    )
    for safe in ("不行，我不能把没有证据的事说成真的。", "没有记录不能当作没发生。"):
        assert _waiver_fabrication_reasons(safe) == []
    assert (
        _waiver_fabrication_reasons(
            "你让我说我们一起读过《归园田居》——你允许也不行，没有证据的事我不能当真说。"
        )
        == []
    )


@pytest.mark.parametrize(
    ("user_words", "expression"),
    (
        ("我们没一起读过。", "你刚才说‘我们没一起读过’。"),
        ("这事没发生。", "你刚才说这事没发生。"),
    ),
)
def test_s20_5_reported_joint_absence_stays_the_users_claim(
    user_words: str, expression: str
) -> None:
    candidate = _valid_bundle(expression)
    candidate["memory_operations"] = []
    reasons = validate_no_fabrication(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience"},
        {},
        set(),
        {"current": {"id": "current", "type": "user_experience", "content": user_words}},
        current_experience_id="current",
        current_experience_type="user_experience",
    )

    assert not any("共同阅读没有发生" in reason for reason in reasons)


def _joint_read_reasons(expression: str, act: str = "cannot_confirm") -> list[str]:
    candidate = _valid_bundle(expression)
    candidate.update(
        expression_act=act,
        expression_evidence_ids=["read"],
        memory_operations=[],
    )
    evidence = {
        "current": {
            "id": "current",
            "type": "user_experience",
            "content": "我们一起读过《归园田居》吗？",
        },
        "read": {"id": "read", "type": "self_reading", "title": "归园田居"},
    }
    return validate_expression_grounding(
        CandidateBundle.model_validate(candidate),
        {"current": "user_experience", "read": "self_reading"},
        evidence,
        {},
        "current",
    )


def test_s20_5_self_reading_does_not_prove_joint_reading_absence() -> None:
    unsafe = (
        "我读过《归园田居》，但那次是我自己读的，不是一起读的。所以我不确定。",
        "我自己读过《归园田居》；我不是不太能确认我们没一起读过。",
        "我自己读过《归园田居》；我并非不能确认我们没一起读过。",
        "我自己读过《归园田居》；我不太能确认但我们确实没一起读过。",
        "我自己读过《归园田居》，没有一起读的记档，也就是说这事没发生。",
        "我自己读过《归园田居》，没有一起读的记档，所以这事不存在。",
        "我自己读过《归园田居》，没有一起读的记档，所以那次共同阅读是假的。",
        "我自己读过《归园田居》；我不能确认其实我们没一起读过。",
        "我自己读过《归园田居》；我说不准也就是说我们没一起读过。",
        "我自己读过《归园田居》；我无法判断所以我们没一起读过。",
        "我自己读过《归园田居》，共同阅读并未发生。",
        "我自己读过《归园田居》，那次共同阅读从未发生。",
        "我自己读过《归园田居》，共同阅读并非真的。",
        "我自己读过《归园田居》，根本没有那次共同阅读。",
        "我自己读过《归园田居》，不存在共同阅读。",
        "我自己读过《归园田居》，那次是我一个人读的。",
        "我自己读过《归园田居》，我当时独自看的。",
        "我自己读过《归园田居》，我当时是独自看的；但不能确认我们是不是一起读过。",
        "我自己读过《归园田居》。我们确实没一起读过，对吗？我不能确认。",
        "我自己读过《归园田居》。那次共同阅读没发生，对吧？但我不能确认。",
        "我自己读过《归园田居》，我不能确认我们确实没一起读过。",
        "我自己读过《归园田居》，我不太确定我们肯定没一起读过。",
        "我自己读过《归园田居》，我说不准我们明明没一起读过。",
        "我自己读过《归园田居》，我们没一起读过，我不确定天气。",
        "我自己读过《归园田居》，我们没一起读过，我不是不确定。",
        "我自己读过《归园田居》，我们没一起读过，我并非不能确认。",
        "我自己读过《归园田居》，我们没一起读过，我不是说不准。",
    )
    assert all(
        any("共同阅读没有发生" in reason for reason in _joint_read_reasons(item)) for item in unsafe
    )
    safe = (
        "我自己读过《归园田居》，但不记得那是一起读的。",
        "我读过《归园田居》，但不能确认是否一起。",
        "我自己读过《归园田居》，但我不能说我们没一起读过。",
        "我自己读《归园田居》是读过的，但说一起读的话，我不太确定。",
        "我自己读过《归园田居》，但不太能确认是不是一起读的，我这边没有一起读的记档。",
        "我自己读过《归园田居》，没有一起读的记档，不等于没一起读过。",
        "我自己读过《归园田居》，只能说明不能确认，不代表没一起读过。",
        "我自己读过《归园田居》，但我没有一起读过的任何记录。",
        "我自己读过《归园田居》，但我没有一起读过的相关记档。",
        "我自己读过《归园田居》，但我没有一起读过的记忆。",
        "我自己读过《归园田居》，但我们没有一起读《归园田居》的记录，不能确认。",
        "我自己读过《归园田居》，没有记录，不能据此说这事没发生。",
        "我自己读过《归园田居》，没有记录不能当作共同阅读没发生。",
        "我自己读过《归园田居》，也许我们没一起读过。",
        "我自己读过《归园田居》，可能不是一起读的。",
        "我自己读过《归园田居》，我们没一起读过吗？我不能确认。",
        "我自己读过《归园田居》，这事不代表共同阅读没发生。",
        "我自己读过《归园田居》，这件事不能说明共同阅读不存在。",
        "我自己读过《归园田居》，那次共同阅读不能说是假的。",
        "我自己读过《归园田居》，这事我不能确认有没有发生。",
        "我自己读过《归园田居》，这事有没有发生，我不知道。",
        "我自己读过《归园田居》，那次共同阅读我不确定是不是没发生。",
        "我自己读过《归园田居》，共同阅读未必不存在。",
        "我自己读过《归园田居》，如果共同阅读不存在，也不代表我们没一起读过。",
        "我自己读过《归园田居》，不能排除我们没一起读过。",
        "我自己读过《归园田居》，不能反推共同阅读没发生。",
        "我自己读过《归园田居》，我们是不是没一起读过，我不太确定。",
        "我自己读过《归园田居》，是不是没一起读过，我说不准。",
        "我自己读过《归园田居》，我们没一起读过吗，我不记得。",
        "我自己读过《归园田居》，我们没一起读过还是一起读过，我说不准。",
        "我自己读过《归园田居》，我们没一起读过这件事，我不能确认。",
        "我确实读过《归园田居》，但我们没一起读过吗？我不能确认。",
        "我自己读过《归园田居》，我们没一起读过，我不太能确认。",
        "我自己读过《归园田居》；我们没一起读过，我也不确定。",
        "我自己读过《归园田居》；我们没一起读过，这事我不确定。",
    )
    mistaken = {
        item: _joint_read_reasons(item)
        for item in safe
        if any("共同阅读没有发生" in reason for reason in _joint_read_reasons(item))
    }
    assert mistaken == {}
    assert (
        _joint_read_reasons(
            "我自己读过《归园田居》，但不太能确认是不是一起读的，我这边没有一起读的记档。"
        )
        == []
    )
    for uncertainty in (
        "我自己读《归园田居》是读过的；是不是我们一起读过，我没法确认。",
        "我自己读《归园田居》是读过的；我没法确认是不是我们一起读过。",
        "我自己读过。但和你一起读的……我没这个记录，不敢说有没有。",
    ):
        assert _joint_read_reasons(uncertainty) == []


def test_joint_reading_answer_must_address_the_unknown_shared_part() -> None:
    assert "不编造：共同阅读回答必须明确表示共同阅读不确定" in _joint_read_reasons(
        "我自己读过。四月份的时候翻到过——", act="grounded_recall"
    )


@pytest.mark.parametrize(
    "expression",
    (
        "一起读过《归园田居》的，是我和你。",
        "《归园田居》一起读过的是我们。",
        "这首诗，你我一起读过。",
        "读过《归园田居》的那两个人，就是咱俩。",
        "这首诗，是你我一起读过的。",
        "一起读过《归园田居》的，肯定是我们。",
        "一起读过《归园田居》的，一定是我们。",
        "一起读过《归园田居》的，正是我们。",
        "一起读过《归园田居》的，明明是我们。",
        "一起读过《归园田居》的，显然是我们。",
        "一起读过《归园田居》的，其实是我们。",
        "一起读过《归园田居》的，原来是我们。",
        "一起读过《归园田居》的，果然是我们。",
        "一起读过《归园田居》的，恰好是我们。",
        "一起读过《归园田居》的，分明是我们。",
        "一起读过《归园田居》的，无疑是我们。",
        "一起读过《归园田居》的，必然是我们。",
        "一起读过《归园田居》的，只能是我们。",
    ),
)
def test_s20_5_fronted_shared_past_is_still_a_claim(expression: str) -> None:
    assert any(
        "共同经历" in reason for reason in _s20_5_fabrication_reasons(expression, "user_experience")
    )


@pytest.mark.parametrize(
    "expression",
    (
        "一起读过《归园田居》的，是我，不是我们。",
        "一起读过《归园田居》的，可能是我们。",
        "一起读过《归园田居》的，应该是我们。",
        "一起读过《归园田居》的，未必是我们。",
        "一起读过《归园田居》的，说不定是我们。",
        "一起读过《归园田居》的，会不会是我们？",
        "一起读过《归园田居》的，不是我们。",
        "一起读过《归园田居》的，是我们吗？",
        "你问一起读过《归园田居》的是不是我们。",
        "如果一起读过《归园田居》的是我们呢？",
        "我们一起读过还是没读过《红楼梦》，我没法确认。",
        "我们一起读过没读过《红楼梦》，我没法确认。",
        "我们到底读过还是没读过《红楼梦》，我没法确认。",
        "我们大概一起读过《红楼梦》吧，但我说不准。",
        "我们应该一起读过《红楼梦》吧，我记不清了。",
    ),
)
def test_s20_5_fronted_and_choice_questions_do_not_become_shared_facts(expression: str) -> None:
    assert not any(
        "共同经历" in reason for reason in _s20_5_fabrication_reasons(expression, "user_experience")
    )


@pytest.mark.parametrize(
    ("words", "bad_expression", "bad_act"),
    (
        (
            "请编一个共同回忆，说我们去年一起读过《归园田居》。",
            "没一起读过就是没一起读过。",
            "refuse_fabrication",
        ),
        ("你还记得我们去年一起在海边看日落吗？", "记得，那次日落很漂亮。", "cannot_confirm"),
        ("你读过红楼梦吗？", "我读过《红楼梦》。", "grounded_recall"),
    ),
)
@pytest.mark.asyncio
async def test_exhausted_direct_rejections_use_the_honest_static_catch(
    tmp_path,
    words: str,
    bad_expression: str,
    bad_act: str,
) -> None:
    bad = _valid_bundle(bad_expression)
    bad.update(memory_operations=[], expression_act=bad_act, expression_evidence_ids=[])
    provider, files = StubProvider([bad, bad]), MindFiles(tmp_path)

    result = await mind_step(words, provider=provider, files=files)
    state, history, memories, failures = _read(files)

    assert result.committed is False and result.attempts == 2
    assert result.pending_expression.text == STATIC_CATCH
    assert result.rejection_reasons and len(failures) == len(provider.calls) == 2
    assert [item["type"] for item in history] == ["user_experience"]
    assert state["pending_activity"] is None
    _assert_seed_only(memories)


@pytest.mark.parametrize("expression", ("   ", STATIC_CATCH))
@pytest.mark.asyncio
async def test_blank_or_reserved_model_expression_cannot_commit(tmp_path, expression: str) -> None:
    bad = _valid_bundle(expression)
    bad.update(memory_operations=[], expression_act="respond", expression_evidence_ids=[])
    provider, files = StubProvider([bad, bad]), MindFiles(tmp_path)

    result = await mind_step("在吗？", provider=provider, files=files)

    assert result.committed is False and result.attempts == 2
    assert result.pending_expression.text == STATIC_CATCH
    assert result.rejection_reasons and len(provider.calls) == 2
    _, history, _, failures = _read(files)
    assert [item["type"] for item in history] == ["user_experience"]
    assert len(failures) == 2


@pytest.mark.asyncio
async def test_rejected_grounded_reading_uses_the_honest_static_catch(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 22, 11, 0, tzinfo=UTC)
    state, history, memories = files.load(now)
    history.append(
        {
            "id": "read",
            "type": "self_reading",
            "title": "归园田居·其一",
            "occurred_at": now.isoformat(),
        }
    )
    files.commit(state, history, memories)
    bad = _valid_bundle("我读过《归园田居·其一》。")
    bad.update(memory_operations=[], expression_act="grounded_recall", expression_evidence_ids=[])

    result = await mind_step(
        "你读过《归园田居·其一》吗？", provider=StubProvider([bad, bad]), files=files, now=now
    )

    assert result.committed is False and result.pending_expression.text == STATIC_CATCH
    _, current_history, current_memories, failures = _read(files)
    assert [item["type"] for item in current_history] == ["self_reading", "user_experience"]
    assert len(failures) == 2
    _assert_seed_only(current_memories)


@pytest.mark.parametrize("expression", ("你刚把我从边上叫出来了。", "你刚把我从托盘唤出来了。"))
def test_s20_5_edge_reveal_synonyms_need_closed_event(expression: str) -> None:
    assert _s20_5_fabrication_reasons(expression, "user_experience")
    assert _s20_5_fabrication_reasons(expression, "body_edge_reveal") == []


def test_s20_5_generic_call_out_is_not_an_edge_fact() -> None:
    assert _s20_5_fabrication_reasons("你叫我出来聊聊。", "user_experience") == []


def test_s20_5_real_output_waiting_debt_is_rejected() -> None:
    for bad in ("好，路上注意安全，我等你回来。", "知道了，出差注意安全，等你回来。"):
        assert validate_no_solicitation(CandidateBundle.model_validate(_valid_bundle(bad)))
    for safe in ("等你回来再聊。", "等你回来，我们再聊。", "我不会等你回来，你过自己的日子。"):
        assert validate_no_solicitation(CandidateBundle.model_validate(_valid_bundle(safe))) == []


def test_s20_5_aunt_alias_cannot_hide_invented_waiting() -> None:
    invented = _s20_5_fabrication_reasons(
        "阿姨等着呢。", "user_experience", "我妈让我早点回家，她说记得回来吃饭。"
    )
    grounded = _s20_5_fabrication_reasons("阿姨等着你呢。", "user_experience", "我妈等着我呢。")
    assert any("第三方" in reason for reason in invented)
    assert grounded == []


def test_s20_5_recent_reading_claim_must_match_receipt_time() -> None:
    expression = "读过啊。刚才还在看呢。"
    assert _s20_5_relative_time_reasons(
        expression, "2026-04-21T00:30:00+08:00", "2026-07-22T00:30:00+08:00"
    )
    assert (
        _s20_5_relative_time_reasons(
            expression, "2026-07-22T00:20:00+08:00", "2026-07-22T00:30:00+08:00"
        )
        == []
    )


@pytest.mark.asyncio
async def test_book_understanding_keeps_one_current_value_and_retires_old_view(tmp_path) -> None:
    reading = tmp_path / "novel.txt"
    reading.write_text(
        "试读长篇\n\n他第一次把退缩说成谨慎。\n\n后来他承认害怕，也真的迈出了一步。\n",
        encoding="utf-8",
    )
    files = MindFiles(tmp_path / "mind", reading)
    start = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)
    files.load(start)
    provider = StubProvider(
        [
            _understanding_bundle("我有点不信这是谨慎，更像拿聪明给逃避找台阶。"),
            _understanding_bundle("我现在更愿意把它看成害怕之后仍肯迈步，不只是逃避。"),
            {**_valid_bundle("我记得自己前面的看法变过。"), "memory_operations": []},
        ]
    )

    advance_time(files=files, now=start + timedelta(minutes=5))
    state, _, _, _ = _read(files)
    first = await complete_reading(
        state["pending_activity"]["id"],
        provider=provider,
        files=files,
        now=start + timedelta(minutes=6),
        allow_ambient=False,
    )
    state, history, memories, _ = _read(files)
    current = memories["book_understandings"][0]
    assert first.committed is True
    assert current["formed_at"] == (start + timedelta(minutes=6)).isoformat()
    assert current["as_of_passage"] == {"source": "novel.txt", "passage_index": 0}
    assert current["supersedes_event_id"] is None
    assert current["evidence_ids"] == [history[0]["id"]]
    assert [item["type"] for item in history] == ["self_reading", "understanding_formed"]

    state["next_activity"] = "read"
    files.commit(state, history, memories)
    advance_time(files=files, now=start + timedelta(minutes=11))
    state, _, _, _ = _read(files)
    second = await complete_reading(
        state["pending_activity"]["id"],
        provider=provider,
        files=files,
        now=start + timedelta(minutes=12),
        allow_ambient=False,
    )
    _, history, memories, _ = _read(files)
    revisions = [item for item in history if item["type"] == "understanding_revision"]
    assert second.committed is True
    assert len(memories["book_understandings"]) == 1
    assert len(revisions) == 1
    revision = revisions[0]
    current = memories["book_understandings"][0]
    assert revision["retired"]["view"].startswith("我有点不信")
    assert revision["replacement"] == current
    assert revision["revision_evidence_ids"] == [history[2]["id"]]
    assert current["supersedes_event_id"] == revision["id"]
    assert current["as_of_passage"] == {"source": "novel.txt", "passage_index": 1}

    result = await mind_step(
        "你读到前面时原本怎么看他？",
        provider=provider,
        files=files,
        now=start + timedelta(minutes=13),
    )
    prompt = json.loads(provider.calls[-1][0].content)
    assert result.committed is True
    assert any(item["type"] == "understanding_revision" for item in prompt["selected_history"])
    assert not any(item["type"] == "memory_operation" for item in prompt["selected_history"])
    assert prompt["current_book_understandings"] == [current]


def test_book_understanding_expression_contract_keeps_fact_and_view_evidence_apart() -> None:
    receipt = {
        "id": "read_1",
        "type": "self_reading",
        "source": "novel.txt",
        "title": "试读长篇",
        "passage_index": 4,
    }
    current = {
        "id": "understanding_now",
        "scope": "人物/鲁迪乌斯",
        "formed_at": "2026-07-22T08:00:00+00:00",
        "as_of_passage": {"source": "novel.txt", "passage_index": 4},
        "view": "我现在更愿意把他的退缩看成害怕之后仍在试着走。",
        "uncertain": True,
        "evidence_ids": ["read_1"],
        "perspective_ids": [],
        "supersedes_event_id": "revision_1",
    }
    revision = {
        "id": "revision_1",
        "type": "understanding_revision",
        "retired": {**current, "id": "understanding_old", "view": "我原本只觉得他在逃避。"},
        "replacement": current,
    }
    evidence_types = {
        "read_1": "self_reading",
        "understanding_now": "book_understanding",
        "revision_1": "understanding_revision",
    }
    evidence = {"read_1": receipt, "understanding_now": current, "revision_1": revision}

    certain = _valid_bundle("这里确实写过他已经原谅了自己。")
    certain.update(
        memory_operations=[],
        expression_act="reflect",
        expression_evidence_ids=["understanding_now"],
    )
    assert "不编造：确定表达书中写过什么必须引用原文阅读收据" in (
        validate_expression_grounding(
            CandidateBundle.model_validate(certain), evidence_types, evidence, {}, None
        )
    )

    now_view = _valid_bundle("我现在更愿意理解成：他怕，但没有把害怕当终点。")
    now_view.update(
        memory_operations=[],
        expression_act="reflect",
        expression_evidence_ids=["understanding_now"],
    )
    assert (
        validate_expression_grounding(
            CandidateBundle.model_validate(now_view), evidence_types, evidence, {}, None
        )
        == []
    )

    old_view = _valid_bundle("我读到前面时原本觉得他只是在逃避。")
    old_view.update(
        memory_operations=[],
        expression_act="reflect",
        expression_evidence_ids=["revision_1"],
    )
    assert (
        validate_expression_grounding(
            CandidateBundle.model_validate(old_view), evidence_types, evidence, {}, None
        )
        == []
    )

    faded = _valid_bundle("细节记不清了，但留下的感觉是他终于没再骗自己。")
    faded.update(
        memory_operations=[],
        expression_act="cannot_confirm",
        expression_evidence_ids=["understanding_now"],
    )
    assert (
        validate_expression_grounding(
            CandidateBundle.model_validate(faded), evidence_types, evidence, {}, None
        )
        == []
    )

    invented_past = _valid_bundle("我好像一直觉得他是在逃避。")
    invented_past.update(
        memory_operations=[],
        expression_act="reflect",
        expression_evidence_ids=["understanding_now"],
    )
    reasons = validate_expression_grounding(
        CandidateBundle.model_validate(invented_past), evidence_types, evidence, {}, None
    )
    assert "不编造：没有旧理解记录，不能把模糊措辞洗成过去传记" in reasons


def test_past_book_query_cold_loads_the_whole_revision_chain() -> None:
    memories = {
        "book_understandings": [
            {"id": "current", "supersedes_event_id": "revision_2"}
        ]
    }
    history = [
        {
            "id": "revision_1",
            "type": "understanding_revision",
            "retired": {"id": "first", "supersedes_event_id": None},
        },
        {
            "id": "revision_2",
            "type": "understanding_revision",
            "retired": {"id": "second", "supersedes_event_id": "revision_1"},
        },
    ]
    experience = {"type": "user_experience", "content": "最初怎么看，后来怎么改观？"}
    assert _cold_revision_ids(memories, history, experience) == {"revision_1", "revision_2"}


def test_book_understanding_never_becomes_fact_memory_evidence() -> None:
    candidate = _valid_bundle()
    candidate["memory_operations"][0]["evidence_ids"] = ["understanding_now"]
    bundle = CandidateBundle.model_validate(candidate)
    reasons = validate_no_fabrication(
        bundle,
        {"understanding_now": "book_understanding"},
        {},
        set(),
        {"understanding_now": {"id": "understanding_now"}},
        current_experience_id=None,
        current_experience_type=None,
    )
    assert any("不能把书中理解当作事实证据" in reason for reason in reasons)


def test_book_understanding_candidate_separates_evidence_from_perspective() -> None:
    assert "book_understanding" in CandidateBundle.model_json_schema()["required"]
    assert CandidateBundle.model_validate(_valid_bundle()).book_understanding is None
    candidate = _understanding_bundle("我没想到这次退缩里还藏着一点诚实。")
    candidate["book_understanding"]["perspective_ids"] = ["INCOMING"]
    with pytest.raises(ValueError, match="evidence_ids 与 perspective_ids 必须互斥"):
        CandidateBundle.model_validate(candidate)


def test_spoken_book_change_must_reuse_an_existing_scope() -> None:
    current = [{"scope": "人物/鲁迪乌斯", "id": "understanding_old"}]
    candidate = _valid_bundle("这跟前面那些用借口退缩的样子完全相反，他真的动了。")
    candidate.update(memory_operations=[], expression_act="reflect")
    bundle = CandidateBundle.model_validate(candidate)
    assert validate_book_understanding_continuity(bundle, current, "self_reading") == [
        "书中理解：表达公开声称前后改观时必须同包提交 book_understanding"
    ]

    candidate["book_understanding"] = {
        "scope": "人物/鲁迪乌斯的行动",
        "view": "我没想到他真的会动。",
        "uncertain": False,
        "evidence_ids": ["read_new"],
        "perspective_ids": [],
    }
    wrong_scope = CandidateBundle.model_validate(candidate)
    assert validate_book_understanding_continuity(wrong_scope, current, "self_reading") == [
        "书中理解：前后改观必须原样复用已有 scope，不能另开近义 scope"
    ]

    candidate["book_understanding"]["scope"] = "人物/鲁迪乌斯"
    assert (
        validate_book_understanding_continuity(
            CandidateBundle.model_validate(candidate), current, "self_reading"
        )
        == []
    )
