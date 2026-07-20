from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from mybuddy.llm import BaseLLMProvider, LLMResponse, Message, ToolCall, ToolSpec
from mybuddy.mind import (
    MEMORY_CONTEXT_BUDGET,
    STATIC_CATCH,
    CandidateBundle,
    MindFiles,
    _replace_texts,
    advance_time,
    complete_reading,
    mind_step,
    validate_activity_truth,
    validate_no_solicitation,
)
from scripts.accept_real_key import DEFAULT_TEXT, encode_payload


def _valid_bundle(expression: str = "我在这儿，先陪你坐一会儿。") -> dict:
    return {
        "action_choice": None,
        "state_changes": {
            "mood": "安静地关心",
            "energy": "平稳",
            "attention": "在听",
        },
        "memory_operations": [
            {
                "action": "record",
                "kind": "user_fact",
                "content": "用户今天有点累",
                "evidence_ids": ["INCOMING"],
                "target_id": None,
            }
        ],
        "expression": expression,
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
        incoming = json.loads(messages[0].content.split("\n", 1)[0])["incoming_experience"]
        incoming_id = incoming["id"] if incoming is not None else None
        for operation in bundle.get("memory_operations", []):
            operation["evidence_ids"] = [
                incoming_id if item == "INCOMING" else item for item in operation["evidence_ids"]
            ]
        return LLMResponse(
            tool_calls=[ToolCall(id="call_1", name="submit_mind_bundle", arguments=bundle)]
        )


def _time_bundle(expression=None) -> dict:  # noqa: ANN001
    return {
        "action_choice": None,
        "state_changes": {
            "mood": "安静",
            "energy": "平稳",
            "attention": "看着刚读到的句子",
        },
        "memory_operations": [
            {
                "action": "record",
                "kind": "self_experience",
                "content": "读到羁鸟恋旧林时感到一种想回到自在处的牵引",
                "evidence_ids": ["INCOMING"],
                "target_id": None,
            }
        ],
        "expression": expression,
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
    return {
        "id": f"mem_{index}",
        "kind": "self_experience",
        "content": content,
        "evidence_ids": [f"life_{index}"],
        "created_at": f"2026-07-{index + 1:02d}T00:00:00+00:00",
        "core": core,
    }


def test_candidate_schema_requires_memory_payload_and_expression() -> None:
    schema = CandidateBundle.model_json_schema()
    operation = schema["$defs"]["MemoryOperation"]
    assert {"content", "evidence_ids"} <= set(operation["required"])
    assert "expression" in schema["required"]
    assert "action_choice" in schema["required"]


def test_candidate_normalizes_deepseek_null_strings() -> None:
    candidate = _valid_bundle("null")
    candidate["action_choice"] = "null"

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
        set(item) == {"id", "kind", "content", "evidence_ids", "created_at", "core"}
        for item in seeds
    )

    core_text = json.dumps(seeds, ensure_ascii=False)
    for invented_past in ("认识很久", "我们上次", "这些年", "恋人", "女朋友"):
        assert invented_past not in core_text

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
    selected_text = json.dumps(prompt["selected_memories"], ensure_ascii=False)
    assert result.committed is True
    assert seed_ids <= {item["id"] for item in prompt["selected_memories"]}
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
            "content": "面对对方的自我否定时，我先接住，不拿对方开玩笑；想吐槽就吐槽处境。",
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
    assert (
        corrected["content"] == "面对对方的自我否定时，我先接住，不拿对方开玩笑；想吐槽就吐槽处境。"
    )
    assert corrected["evidence_ids"] == [user_id]
    assert corrected["core"] is True
    assert datetime.fromisoformat(corrected["corrected_at"]).astimezone(UTC) == now
    assert failures == []

    _, _, reloaded = files.load(now + timedelta(minutes=1))
    reloaded_by_id = {item["id"]: item for item in reloaded["items"]}
    assert reloaded_by_id["seed_tension_voice"]["content"] == corrected["content"]


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
    assert result.committed is True
    assert result.attempts == 1
    assert state["condition"]["attention"] == "在听"
    assert state["pending_expression"]["text"] == "今天辛苦了。我在这儿。"
    assert state["pending_expression"]["kind"] == "direct"
    assert [item["type"] for item in history] == [
        "user_experience",
        "memory_operation",
    ]
    assert all(item.get("content") != "今天辛苦了。我在这儿。" for item in history)
    assert _learned_items(memories)[0]["content"] == "用户今天有点累"
    assert failures == []


@pytest.mark.asyncio
async def test_solicitation_in_state_or_memory_rejects_even_when_expression_is_legal(
    tmp_path,
) -> None:
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
    assert state["pending_expression"]["text"] == STATIC_CATCH
    assert len(failures) == 2
    assert all("不索取" in failure["reasons"][0] for failure in failures)


@pytest.mark.asyncio
async def test_fabricated_shared_experience_rejects_whole_bundle_and_retries_with_reason(
    tmp_path,
) -> None:
    bad = _valid_bundle("还记得我们上次一起淋雨吗？")
    bad["memory_operations"] = [
        {
            "action": "record",
            "kind": "shared_experience",
            "content": "我们一起淋过雨",
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
    assert state["pending_expression"]["text"] == STATIC_CATCH
    _assert_seed_only(memories)
    assert failures[0]["candidate_raw"]
    assert any("不编造" in reason for reason in failures[0]["reasons"])


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
            "attention": "在你身上",
        },
        "memory_operations": [
            {
                "action": "record",
                "kind": "shared_experience",
                "content": "你捏了我的脸颊，动作很轻，像是开玩笑或者表示亲近",
                "evidence_ids": ["INCOMING"],
            },
            {
                "action": "record",
                "kind": "self_experience",
                "content": "脸颊被触碰时皮肤有轻微的紧绷感，然后放松下来",
                "evidence_ids": ["life:0"],
            },
        ],
        "expression": "干嘛捏我脸呀",
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
    assert state["pending_expression"]["text"] == STATIC_CATCH
    assert "引用了未知证据 ['life:0']" in "\n".join(reasons)
    assert "memory_operations[0].content 的触碰记忆没有引用 body_touch" in "\n".join(reasons)
    assert "expression 断言用户触碰了她" in "\n".join(reasons)
    assert "推断了用户动机或关系含义 `开玩笑`" in "\n".join(reasons)
    assert json.loads(failures[0]["candidate_raw"])["expression"] == "干嘛捏我脸呀"


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
            "attention": "注意到触碰",
        },
        "memory_operations": [
            {
                "action": "record",
                "kind": "self_experience",
                "content": "头部被触碰了",
                "evidence_ids": ["INCOMING"],
            }
        ],
        "expression": "嗯？头被碰了一下。",
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
    assert result.committed is True
    assert [item["type"] for item in history] == ["body_touch", "memory_operation"]
    assert _learned_items(memories)[0]["evidence_ids"] == [history[0]["id"]]
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
async def test_deleted_life_alias_cannot_be_second_example_for_user_pattern(tmp_path) -> None:
    bad = _valid_bundle("我听见了。")
    bad["memory_operations"] = [
        {
            "action": "record",
            "kind": "pattern",
            "content": "用户累时总会来找我",
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
    assert "没有两条用户或共同经历证据" in "\n".join(failures[0]["reasons"])


@pytest.mark.asyncio
async def test_four_memory_kinds_record_canonical_evidence_in_one_mind_step(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    state, history, memories = files.load(now)
    history.extend(
        [
            {
                "id": "exp_earlier",
                "type": "user_experience",
                "content": "忙完时我会把桌子收干净。",
                "occurred_at": (now - timedelta(days=2)).isoformat(),
            },
            {
                "id": "life_earlier",
                "type": "self_reading",
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
            "content": "用户忙完了会收拾桌面",
            "evidence_ids": ["INCOMING"],
        },
        {
            "action": "record",
            "kind": "self_experience",
            "content": "今天合上书后收了收自己的桌面",
            "evidence_ids": ["life_earlier"],
        },
        {
            "action": "record",
            "kind": "shared_experience",
            "content": "我们有过一次忙完后安静坐着的对话",
            "evidence_ids": ["exp_earlier", "shown_earlier"],
        },
        {
            "action": "record",
            "kind": "pattern",
            "content": "用户忙完后常会整理桌面",
            "evidence_ids": ["exp_earlier", "INCOMING"],
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
    assert result.committed is True
    assert set(by_kind) == {
        "user_fact",
        "self_experience",
        "shared_experience",
        "pattern",
    }
    assert by_kind["self_experience"]["evidence_ids"] == ["life_earlier"]
    assert "life:0" not in json.dumps(saved, ensure_ascii=False)
    assert by_kind["pattern"]["evidence_ids"][0] == "exp_earlier"
    assert [item["action"] for item in operations] == ["record"] * 4
    assert failures == []


@pytest.mark.asyncio
async def test_integrate_recall_correct_and_forget_have_distinct_traced_effects(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 18, 11, 0, tzinfo=UTC)
    state, history, memories = files.load(now)
    history.append(
        {
            "id": "exp_old",
            "type": "user_experience",
            "content": "忙完时我会收桌面。",
            "occurred_at": (now - timedelta(days=1)).isoformat(),
        }
    )
    memories["items"] = [
        {
            "id": "mem_user",
            "kind": "user_fact",
            "content": "用户会收拾桌面",
            "evidence_ids": ["exp_old"],
            "created_at": (now - timedelta(days=1)).isoformat(),
            "core": True,
        },
        {
            "id": "mem_self",
            "kind": "self_experience",
            "content": "在窗边读过书",
            "evidence_ids": ["life_old"],
            "created_at": (now - timedelta(days=1)).isoformat(),
        },
        {
            "id": "mem_shared",
            "kind": "shared_experience",
            "content": "一次已经不需要长期留下的闲聊",
            "evidence_ids": ["exp_old"],
            "created_at": (now - timedelta(days=1)).isoformat(),
        },
        {
            "id": "mem_pattern",
            "kind": "pattern",
            "content": "用户总在夜里整理桌面",
            "evidence_ids": ["exp_old"],
            "created_at": (now - timedelta(days=1)).isoformat(),
        },
    ]
    files.commit(state, history, memories)
    bundle = _valid_bundle("原来不只是在夜里，是忙完以后。")
    bundle["memory_operations"] = [
        {
            "action": "integrate",
            "kind": "user_fact",
            "target_id": "mem_user",
            "content": "用户忙完后会收拾桌面",
            "evidence_ids": ["INCOMING"],
            "core": False,
        },
        {
            "action": "recall",
            "kind": "self_experience",
            "target_id": "mem_self",
            "evidence_ids": [],
            "content": "",
        },
        {
            "action": "correct",
            "kind": "pattern",
            "target_id": "mem_pattern",
            "content": "用户忙完后常会整理桌面，不限于夜里",
            "evidence_ids": ["exp_old", "INCOMING"],
        },
        {
            "action": "forget",
            "kind": "shared_experience",
            "target_id": "mem_shared",
            "evidence_ids": [],
            "content": "",
        },
    ]

    result = await mind_step(
        "我不是只在夜里收桌子，主要是忙完以后会收。",
        provider=StubProvider([bundle]),
        files=files,
        now=now,
    )

    _, recorded, saved, failures = _read(files)
    by_id = {item["id"]: item for item in saved["items"]}
    operations = [item for item in recorded if item["type"] == "memory_operation"]
    assert result.committed is True
    assert by_id["mem_user"]["evidence_ids"][0] == "exp_old"
    assert len(by_id["mem_user"]["evidence_ids"]) == 2
    assert by_id["mem_user"]["core"] is False
    assert by_id["mem_self"]["content"] == "在窗边读过书"
    assert by_id["mem_pattern"]["content"] == "用户忙完后常会整理桌面，不限于夜里"
    assert "mem_shared" not in by_id
    assert [item["action"] for item in operations] == [
        "integrate",
        "recall",
        "correct",
        "forget",
    ]
    corrected = next(item for item in operations if item["action"] == "correct")
    integrated = next(item for item in operations if item["action"] == "integrate")
    assert integrated["core"] is False
    assert corrected["previous_content"] == "用户总在夜里整理桌面"
    assert failures == []


@pytest.mark.asyncio
async def test_seed_and_core_memory_cannot_be_forgotten_directly(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)
    state, history, memories = files.load(now)
    memories["items"].append(
        {
            "id": "mem_core",
            "kind": "user_fact",
            "content": "用户明确说过忙完会收桌面",
            "evidence_ids": ["exp_old"],
            "created_at": (now - timedelta(days=1)).isoformat(),
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
            "content": "",
            "evidence_ids": [],
        }
    ]
    core_forget = _valid_bundle("这条记忆还不能直接删掉。")
    core_forget["memory_operations"] = [
        {
            "action": "forget",
            "kind": "user_fact",
            "target_id": "mem_core",
            "content": "",
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
    state, history, memories = files.load(start)
    memories["items"].append(
        {
            "id": "mem_core",
            "kind": "user_fact",
            "content": "用户总会在夜里收桌面",
            "evidence_ids": ["exp_old"],
            "created_at": (start - timedelta(days=1)).isoformat(),
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
            "content": "用户有时会在忙完后收桌面",
            "evidence_ids": ["INCOMING"],
            "core": False,
        }
    ]
    forget = {
        "action": "forget",
        "kind": "user_fact",
        "target_id": "mem_core",
        "content": "",
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

    forget_bundle = _valid_bundle("现在可以把这条非核心记忆放下了。")
    forget_bundle["memory_operations"] = [forget]
    forgotten = await mind_step(
        "下一回合再忘记它。",
        provider=StubProvider([forget_bundle]),
        files=files,
        now=start + timedelta(minutes=2),
    )
    _, _, after_forget, _ = _read(files)
    assert forgotten.committed is True
    assert "mem_core" not in {item["id"] for item in after_forget["items"]}


@pytest.mark.asyncio
async def test_early_core_memory_stays_visible_after_more_than_eight_situations(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    state, history, memories = files.load(now)
    memories["items"] = [
        _memory_item(0, "我遇到不确定的事时会先承认不知道。", core=True),
        *[_memory_item(index, f"情景记忆{index}：" + "页" * 450) for index in range(1, 11)],
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
async def test_pattern_with_one_example_requires_explicit_user_confirmation(tmp_path) -> None:
    files = MindFiles(tmp_path)
    bad = _valid_bundle()
    bad["memory_operations"] = [
        {
            "action": "record",
            "kind": "pattern",
            "content": "用户明确说自己忙完后习惯收桌面",
            "evidence_ids": ["INCOMING"],
        }
    ]
    confirmed = json.loads(json.dumps(bad, ensure_ascii=False))
    confirmed["memory_operations"][0]["user_confirmed"] = True

    result = await mind_step(
        "我确认一下：我忙完后就是习惯收桌面。",
        provider=StubProvider([bad, confirmed]),
        files=files,
    )

    _, recorded, saved, failures = _read(files)
    operation = next(item for item in recorded if item["type"] == "memory_operation")
    assert result.committed is True
    assert result.attempts == 2
    assert _learned_items(saved)[0]["kind"] == "pattern"
    assert operation["user_confirmed"] is True
    assert "也没有用户确认" in failures[0]["reasons"][0]


@pytest.mark.asyncio
async def test_provider_failure_returns_honest_static_catch_without_committing(tmp_path) -> None:
    files = MindFiles(tmp_path)
    now = datetime(2026, 7, 19, 9, 30, tzinfo=UTC)

    result = await mind_step("你在吗？", provider=FailingProvider(), files=files, now=now)
    state, history, memories, failures = _read(files)

    assert result.committed is False
    assert result.pending_expression.text == STATIC_CATCH
    assert result.rejection_reasons == ["模型调用失败：ConnectionError"]
    assert state["pending_expression"] == result.pending_expression.model_dump()
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
    later["memory_operations"][0]["content"] = "用户今天第一次学会骑车"
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


@pytest.mark.asyncio
async def test_completed_reading_can_answer_what_was_just_read(tmp_path) -> None:
    files = MindFiles(tmp_path)
    start = datetime(2026, 7, 20, 20, 44, tzinfo=UTC)
    files.load(start)
    answer = {
        "action_choice": None,
        "state_changes": {"mood": "安静", "energy": "平稳", "attention": "诗句里"},
        "memory_operations": [],
        "expression": "刚读到陶渊明《归园田居·其一》：少无适俗韵，性本爱丘山。",
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
