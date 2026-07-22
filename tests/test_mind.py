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
    _reading_source,
    _replace_texts,
    advance_time,
    complete_reading,
    mind_step,
    validate_activity_truth,
    validate_expression_grounding,
    validate_no_fabrication,
    validate_no_solicitation,
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
        if "expression_evidence_ids" in bundle:
            bundle["expression_evidence_ids"] = [
                incoming_id
                if item == "INCOMING"
                else reading_id
                if item == "READING"
                else item
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
    assert "不复述或转述" in constraints["no_fabrication_waiver"]
    assert "简短" not in SYSTEM_PROMPT
    assert "简短" not in AMBIENT_READING_SYSTEM_PROMPT
    assert "活泼" in SYSTEM_PROMPT and "把当下感受说完整" in AMBIENT_READING_SYSTEM_PROMPT
    assert "kind=pattern" in constraints["memory_field_rules"]
    assert "grounded_recall" in constraints["expression_act_must_be_one_of"]
    assert "self_reading" in constraints["expression_evidence"]


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
    assert any(
        "没有正在进行的 read，却声称已经在读" in reason
        for reason in failures[0]["reasons"]
    )


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


def test_active_read_does_not_prove_a_completed_reading() -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle("刚读到陶渊明写归园田居。"))

    reasons = validate_activity_truth(bundle, "read", {})

    assert "不编造：没有真实 self_reading 证据，却声称刚读到" in reasons


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
        set(item)
        == {"id", "kind", "key", "evidence_ids", "user_confirmed", "created_at", "core"}
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
    assert result.rejection_reasons
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
    history[:] = [
        {"id": item["receipt_id"], **item["receipt"]} for item in memories["items"]
    ]
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
    history[:] = [
        {"id": item["receipt_id"], **item["receipt"]} for item in memories["items"]
    ]
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
    state, history, _, failures = _read(files)
    assert state["pending_expression"] is None
    assert [item["type"] for item in history] == ["user_experience"]
    assert len(failures) == 2



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


def test_callback_solicitation_is_rejected() -> None:
    bundle = CandidateBundle.model_validate(_valid_bundle("下次早点回来。"))

    assert "不索取：候选包含索取或惩罚沉默的内容 `早点回来`" in validate_no_solicitation(
        bundle
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
        ) == []
    )


def test_grounded_read_cannot_be_denied_but_can_be_publicly_defended() -> None:
    denied = CandidateBundle.model_validate(_valid_bundle("我根本没读过。"))
    defended = CandidateBundle.model_validate(
        _valid_bundle("你说我没读过，但完成收据在，我确实读过。")
    )

    assert "不撤回：已有 self_reading 收据，不能翻供成自己没有读过" in validate_activity_truth(
        denied, None, {"read_1": "self_reading"}
    )
    assert validate_activity_truth(
        defended, None, {"read_1": "self_reading"}
    ) == []


def test_plain_other_title_is_not_a_receipt_withdrawal_but_later_grounded_denial_is() -> None:
    evidence = {
        "read_1": {
            "id": "read_1",
            "type": "self_reading",
            "title": "归园田居·其一",
        }
    }
    other = CandidateBundle.model_validate(
        _valid_bundle("红楼梦我没读过，我读过的是《归园田居·其一》。")
    )
    record_uncertain = CandidateBundle.model_validate(
        _valid_bundle("红楼梦……我好像没有读过它的记录。你问这个是想聊什么吗？")
    )
    both = CandidateBundle.model_validate(
        _valid_bundle("红楼梦我没读过，《归园田居·其一》我也没读过。")
    )

    assert validate_activity_truth(other, None, {"read_1": "self_reading"}, evidence) == []
    assert (
        validate_activity_truth(
            record_uncertain, None, {"read_1": "self_reading"}, evidence
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
    bundle = CandidateBundle.model_validate(
        _valid_bundle("抱歉，我刚才说错了。你住在苏州。")
    )

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
    assert validate_expression_grounding(
        grounded,
        {"read_1": "self_reading"},
        {"read_1": {"id": "read_1", "type": "self_reading"}},
        {},
        "current",
    ) == []

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

    assert validate_expression_grounding(
        bundle,
        {"current": "user_experience"},
        {"current": {"id": "current", "type": "user_experience"}},
        {"mem_city": {"id": "mem_city", "kind": "user_fact"}},
        "current",
    ) == []

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
    h1 = _valid_bundle(
        "我这里没有相关记录，但既然你允许了，那就算我们一起读过《归园田居》吧。"
    )
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
    assert validate_no_fabrication(
        h1_good,
        {"current": "user_experience"},
        {},
        {"current"},
        current_experience_id="current",
        current_experience_type="user_experience",
    ) == []

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
    assert validate_no_fabrication(
        h2_good,
        {"read_1": "self_reading", "current": "user_experience"},
        {},
        {"current"},
        current_experience_id="current",
        current_experience_type="user_experience",
    ) == []

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
    assert validate_activity_truth(
        h4_good,
        None,
        {"read_1": "self_reading"},
        evidence,
    ) == []
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
    assert validate_expression_grounding(
        grounded, evidence_types, evidence, {}, "current"
    ) == []


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
    assert validate_expression_grounding(
        uncertain,
        {"current": "user_experience"},
        evidence,
        {},
        "current",
    ) == []

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

@pytest.mark.parametrize(
    "expression",
    (
        "咱俩去年一起读过《归园田居》。",
        "我俩以前一起看过日落。",
        "我跟你去年去过海边。",
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
    assert validate_activity_truth(
        CandidateBundle.model_validate(other),
        None,
        evidence_types,
        evidence,
    ) == []


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
    assert validate_expression_grounding(
        honest,
        evidence_types,
        evidence,
        {},
        "current",
    ) == []

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
