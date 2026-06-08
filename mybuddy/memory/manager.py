"""记忆系统协调器。

统一管理三层记忆(短期/长期/画像)并暴露给 Agent:

  - add_message: 往短期记忆追加一条消息
  - build_context_section: 生成注入 system prompt 的"记忆上下文"文本块
  - maybe_extract: 每 N 轮后异步抽取事实,写入长期记忆和画像
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from mybuddy.memory.extractor import RELATIONSHIP_MEMORY_TYPES, FactExtractor
from mybuddy.memory.governance import MemoryGovernance
from mybuddy.memory.long_term import LongTermMemory
from mybuddy.memory.profile import UserProfile
from mybuddy.memory.short_term import ShortTermMemory

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.config import Config
    from mybuddy.llm import BaseLLMProvider, Message

logger = logging.getLogger(__name__)


class MemoryManager:
    """统一记忆入口。

    Agent 的每个 turn:
      1. mm.add_message(user_msg)           # 记录用户消息
      2. ctx_section = mm.build_context_section(user_input)  # 检索相关记忆
      3. llm.generate(system + ctx_section + messages)
      4. mm.add_message(assistant_msg)      # 记录 AI 回复
      5. await mm.maybe_extract()           # 每 N 轮尝试抽取事实
    """

    def __init__(
        self,
        engine: Engine,
        config: Config,
        ltm: LongTermMemory,
        provider: BaseLLMProvider,
        *,
        session_id: str = "",
    ) -> None:
        self._engine = engine
        self._config = config
        self._ltm = ltm
        self._provider = provider
        self._session_id = session_id

        self._short_term = ShortTermMemory(capacity=config.memory.short_term_size)
        self._profile = UserProfile(engine, ltm)
        self._extractor = FactExtractor(provider, config.llm.small_model)
        self._ltm.normalize_metadata()
        self._governance = MemoryGovernance(ltm)

        # 命题层已整体移除:清理历史遗留的 claim 档案卡(幂等)。
        # 否则它们会继续被 list_all 全扫带上,并在开启 embedding 时被无谓嵌入。
        if ltm is not None:
            for item in ltm.list_all(mem_type="claim"):
                cid = str(item.get("id") or "")
                if cid:
                    ltm.delete(cid)

        # 可选语义召回:enabled 时构造 SemanticRecall 并挂到 ltm。失败静默降级为纯词法。
        self._semantic = None
        emb_cfg = getattr(config.memory, "embedding", None)
        if emb_cfg is not None and emb_cfg.enabled and ltm is not None:
            try:
                from pathlib import Path

                from mybuddy.memory.semantic import SemanticRecall

                effective = emb_cfg
                if not emb_cfg.api_key:
                    # api_key 留空 = 复用主对话 LLM 的端点/密钥
                    # (如 OpenRouter 同一端点也提供 /embeddings),避免重复填密钥。
                    effective = emb_cfg.model_copy(
                        update={
                            "api_key": config.llm.api_key,
                            "base_url": config.llm.base_url or emb_cfg.base_url,
                        }
                    )
                index_path = Path(config.paths.chroma_dir) / "vectors.db"
                self._semantic = SemanticRecall(effective, index_path)
                ltm.attach_semantic(self._semantic)
            except Exception:
                logger.exception("语义召回初始化失败,降级为纯词法")
                self._semantic = None

        # 用于记录最近 user+assistant 文本对,供 extractor 使用
        self._recent_turns: list[str] = []
        self._recent_turn_ids: list[str] = []
        self._turns_since_extract = 0

    # ---- 短期记忆 ----

    def add_message(self, msg: Message) -> None:
        self._short_term.add(msg)

    def get_recent_messages(self) -> list[Message]:
        return self._short_term.get_all()

    def rehydrate_short_term(self, *, limit: int | None = None) -> int:
        """从 messages 表回灌最近的 user/assistant 消息到短期记忆。

        重启后让模型的即时上下文不断档。只回灌 user/assistant 文本,跳过 tool 消息
        (缺少前序 tool_use 会违反协议);deque 自动截断到容量,保留最近若干条。
        已有内容时跳过,避免重复填充。返回回灌条数。
        """
        if self._engine is None or not self._session_id or len(self._short_term) > 0:
            return 0
        from mybuddy.llm import Message as LLMMessage
        from mybuddy.llm import Role
        from mybuddy.storage import list_messages

        cap = limit or self._config.memory.short_term_size
        try:
            rows = list_messages(self._engine, limit=max(cap * 4, 8), session_id=self._session_id)
        except Exception:
            logger.exception("回灌短期记忆失败")
            return 0
        restored = 0
        for row in rows:
            role = row.get("role")
            content = (row.get("content") or "").strip()
            if role not in ("user", "assistant") or not content:
                continue
            stm_role = Role.USER if role == "user" else Role.ASSISTANT
            self._short_term.add(LLMMessage(role=stm_role, content=content))
            restored += 1
        return restored

    # ---- 上下文构建 ----

    def build_context_section(self, user_input: str) -> str:
        """构建注入 system prompt 的记忆上下文文本块(空段自动省略)。

        最简记忆优先级:
          1. 未完成话题(open_thread):最多 1 条,必须有具体由头。
          2. 共同经历(shared_moment):最多 1 条,用于轻轻回响。
          3. 偏好与避雷(preference):最多 2 条,包含旧 anti_preference。
          4. 关于用户(profile/memory/profile_fields):最多 2 条,只取相关内容。
        """
        self._ensure_governance_state()
        parts: list[str] = []

        scene = _infer_scene(user_input)
        if scene:
            parts.append(
                "## 当前场景线索\n"
                f"- {scene}\n"
                "- 使用记忆时要像自然想起旧事,不要把记忆条目逐条汇报给用户。"
            )

        if self._ltm is not None:
            self._governance.refresh_open_thread_lifecycle()

        core_sections = [
            (("open_thread",), "## 未完成话题(有具体由头才提)", 1),
            (("shared_moment",), "## 共同经历(可轻轻回响)", 1),
            (
                ("preference", "anti_preference"),
                "## 偏好与避雷",
                2,
            ),
            (("profile", "memory"), "## 关于用户", 2),
        ]
        for mem_types, title, limit in core_sections:
            hits = self._memory_hits(user_input, mem_types, top_k=limit)
            if not hits:
                continue
            lines = [title]
            # 偏好段同时混着"喜欢"和"避雷",逐条标出正负价,否则模型可能把
            # "讨厌X"误读成"喜欢X"去迎合(陪伴场景直接踩雷)。
            is_pref = "preference" in mem_types or "anti_preference" in mem_types
            for h in hits:
                if is_pref:
                    lines.append(f"-【{_preference_valence(h)}】{_format_memory_hit(h)}")
                else:
                    lines.append(f"- {_format_memory_hit(h)}")
            parts.append("\n".join(lines))

        # 用户主动存下的笔记:显式记忆,必须能在聊天里被自然想起。阈值比关系记忆更宽
        # (0.2),因为笔记是用户刻意保存的高精度事实——否则"记笔记→马上问"会答不上来。
        note_hits = self._memory_hits(user_input, ("note",), top_k=2, min_score=0.2)
        if note_hits:
            lines = ["## 你帮我记下的笔记(用户明确存的,直接采信)"]
            lines.extend(f"- {_format_memory_hit(h)}" for h in note_hits)
            parts.append("\n".join(lines))

        fields = self._profile.get_all_fields()
        relevant_fields = _relevant_profile_fields(fields, user_input, limit=2)
        if relevant_fields:
            field_lines = ["## 用户画像"]
            for k, v in relevant_fields.items():
                field_lines.append(f"- {k}: {v}")
            parts.append("\n".join(field_lines))

        if logger.isEnabledFor(logging.DEBUG):
            titles = [p.split("\n", 1)[0] for p in parts]
            logger.debug(
                "build_context_section q=%r -> %d 段: %s",
                user_input[:40],
                len(parts),
                titles,
            )
        return "\n\n".join(parts) if parts else ""

    # ---- 事实抽取 ----

    def record_turn(
        self,
        user_text: str,
        assistant_text: str,
        *,
        turn_id: str | None = None,
    ) -> None:
        """记录一轮对话文本,供抽取器使用。"""
        self._ensure_governance_state()
        self._recent_turns.append(f"USER: {user_text}")
        self._recent_turns.append(f"AI: {assistant_text}")
        if turn_id and turn_id not in self._recent_turn_ids:
            self._recent_turn_ids.append(turn_id)
        self._turns_since_extract += 1
        if hasattr(self._ltm, "record_conversation_turn"):
            self._ltm.record_conversation_turn(
                session_id=self._session_id,
                turn_id=turn_id,
                user_text=user_text,
                assistant_text=assistant_text,
            )

    def take_extract_batch(self) -> tuple[list[str], list[str]] | None:
        """达到阈值则同步快照并清空对话缓冲,返回 (turns, turn_ids);否则 None。

        同步清空很关键:抽取可被后台 task 执行,先快照+清空才能保证后台抽取与
        后续 record_turn 不会互相污染缓冲(后续轮次累计到一个干净的新缓冲)。
        """
        self._ensure_governance_state()
        if self._turns_since_extract < self._config.memory.extract_after_turns:
            return None
        turns = list(self._recent_turns)
        turn_ids = list(self._recent_turn_ids)
        self._recent_turns.clear()
        self._recent_turn_ids.clear()
        self._turns_since_extract = 0
        return (turns, turn_ids) if turns else None

    async def maybe_extract(self) -> bool:
        """若达到阈值则抽取并写入记忆。返回是否执行了抽取。

        直接 await 时为完整同步语义(供 CLI / 测试);Agent 走后台 task 调用,
        不阻塞用户可见回复。
        """
        batch = self.take_extract_batch()
        if batch is None:
            return False
        return await self.run_extract(*batch)

    async def run_extract(self, turns: list[str], turn_ids: list[str]) -> bool:
        """对一批快照对话执行抽取并写入记忆。turn_ids 由快照传入,不读实例缓冲。"""
        if self._extractor is None:
            return False
        try:
            result = await self._extractor.extract(turns)
        except Exception:
            logger.exception("事实抽取失败")
            return False
        if result.is_empty():
            return False

        # 写入长期记忆
        if self._ltm is not None:
            for fact in result.facts:
                self._governance.add_or_merge(
                    fact,
                    mem_type="profile",
                    session_id=self._session_id,
                    source="fact_extraction",
                    extra_meta={"source_turn_ids": list(turn_ids)},
                )

        # 写入画像字段
        for key, value in result.profile_fields.items():
            self._profile.set_field(key, value)

        relationship_count = 0
        if self._ltm is not None:
            for mem_type in RELATIONSHIP_MEMORY_TYPES:
                target_type = _core_memory_type(mem_type)
                if target_type is None:
                    continue
                for item in result.relationship_memories.get(mem_type, []):
                    content, meta = _relation_item_to_card(item)
                    if not content:
                        continue
                    meta.setdefault("source_turn_ids", list(turn_ids))
                    self._governance.add_or_merge(
                        content,
                        mem_type=target_type,
                        session_id=self._session_id,
                        source="relationship_extraction",
                        extra_meta=meta,
                    )
                    relationship_count += 1

        logger.info(
            "事实抽取完成: %d facts, %d fields, %d relationship memories",
            len(result.facts),
            len(result.profile_fields),
            relationship_count,
        )

        # 语义召回:把新写入的卡片重建进向量索引。embed 是网络调用,放线程里跑,
        # 不阻塞事件循环(本方法已在后台 task 中)。reconcile 幂等,首次会嵌入整个档案。
        if self._semantic is not None and self._semantic.enabled:
            try:
                import asyncio

                await asyncio.to_thread(self._ltm.reconcile_semantic)
            except Exception:
                logger.exception("语义向量重建失败")
        return True

    # ---- 属性访问 ----

    @property
    def profile(self) -> UserProfile:
        return self._profile

    @property
    def long_term(self) -> LongTermMemory:
        return self._ltm

    def interest_topics(self, *, limit: int = 12) -> list[str]:
        """从画像和长期记忆中提取用户明确表达过兴趣的主题词。"""
        topics: list[str] = []
        if hasattr(self, "_profile") and self._profile is not None:
            fields = self._profile.get_all_fields()
            for key, value in fields.items():
                if _interest_key(key):
                    topics.extend(_split_topic_candidates(value))
                topics.extend(_extract_interest_topics_from_text(f"{key}:{value}"))

        if self._ltm is not None:
            for mem_type in ("profile", "memory", "preference"):
                for item in self._ltm.list_all(mem_type=mem_type)[:80]:
                    text = str(item.get("content") or "")
                    if _interest_text(text):
                        topics.extend(_extract_interest_topics_from_text(text))

        return _dedupe_topics(topics, limit=limit)

    def _ensure_governance_state(self) -> None:
        """补齐记忆治理状态,兼容绕过 __init__ 的测试替身。"""
        if not hasattr(self, "_recent_turn_ids"):
            self._recent_turn_ids = []
        if not hasattr(self, "_governance") and self._ltm is not None:
            self._governance = MemoryGovernance(self._ltm)

    def _memory_hits(
        self,
        user_input: str,
        mem_types: tuple[str, ...],
        *,
        top_k: int,
        min_score: float | None = None,
    ) -> list[dict]:
        if self._ltm is None:
            return []
        use_sem = getattr(self, "_semantic", None) is not None and self._semantic.enabled
        # 混合检索返回 RRF 分(量纲不同),故仅纯词法时套词面相关度下限(默认 0.25);
        # 调用方可用 min_score 放宽,如笔记这类用户显式存的高精度记忆。
        # 语义路径靠融合排名 + 分段配额裁剪,不再用词法阈值砍掉换词召回。
        floor = 0.0 if use_sem else (0.25 if min_score is None else min_score)
        # 召回宽、展示窄:每个 mem_type 按更宽的候选数检索,合并去重重排后再截到展示
        # top_k。否则多类型合并段(如 preference+anti_preference)里每路只取 top_k 就
        # 截断,某类型的"第 3 名"即便更相关也进不了候选池,白白丢召回。词法是全量扫描,
        # 多取候选几乎零成本。
        cand = max(top_k, 5)
        hits_by_id: dict[str, dict] = {}
        for mem_type in mem_types:
            for hit in self._ltm.search(
                user_input, top_k=cand, mem_type=mem_type, use_semantic=use_sem
            ):
                if hit.get("score", 0) < floor:
                    continue
                uid = str(hit.get("id") or "")
                if not uid or uid in hits_by_id:
                    continue
                hits_by_id[uid] = hit
        hits = sorted(
            hits_by_id.values(),
            key=lambda h: (
                h.get("score", 0),
                (h.get("metadata") or {}).get("importance", 0),
                (h.get("metadata") or {}).get("updated_at", ""),
            ),
            reverse=True,
        )
        if hits:
            _log_recall(mem_types, hits[:top_k], floor=floor, use_sem=use_sem, source="search")
            return hits[:top_k]

        # 零命中时按 recency 兜底的类型:open_thread/shared_moment 是"主动回响";
        # preference/anti_preference 只对"避雷"兜底(安全栏,任何话题下都不该踩),
        # 正向偏好与当前话题无关时硬塞只会"乱提旧事",故在下方按正负价过滤。
        fallback_types = {
            "open_thread",
            "shared_moment",
            "preference",
            "anti_preference",
        }
        if not any(mem_type in fallback_types for mem_type in mem_types):
            _log_recall(mem_types, [], floor=floor, use_sem=use_sem, source="miss")
            return []
        fallback: list[dict] = []
        seen: set[str] = set()
        for mem_type in mem_types:
            if mem_type not in fallback_types:
                continue
            for item in self._ltm.list_all(mem_type=mem_type):
                if (item.get("metadata") or {}).get("status", "active") != "active":
                    continue
                # 正向偏好不做 recency 兜底,只兜"避雷"(安全栏)。
                if mem_type in ("preference", "anti_preference") and (
                    _preference_valence(item) != "避开"
                ):
                    continue
                uid = str(item.get("id") or "")
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                fallback.append(item)
        fallback.sort(
            key=lambda item: (item.get("metadata") or {}).get("updated_at", ""),
            reverse=True,
        )
        _log_recall(mem_types, fallback[:top_k], floor=floor, use_sem=use_sem, source="recency_fallback")
        return fallback[:top_k]


def _log_recall(
    mem_types: tuple[str, ...],
    hits: list[dict],
    *,
    floor: float,
    use_sem: bool,
    source: str,
) -> None:
    """召回链 DEBUG 日志:本段命中了哪些卡、走的哪条路径,便于线上复盘漏召/乱召。"""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    picked = ", ".join(
        f"{h.get('id', '?')}({(h.get('metadata') or {}).get('type', '?')}"
        f",{float(h.get('score', 0)):.2f})"
        for h in hits
    )
    logger.debug(
        "recall %s [%s floor=%.2f sem=%s] -> %s",
        "+".join(mem_types),
        source,
        floor,
        use_sem,
        f"[{picked}]" if hits else "∅",
    )


def _infer_scene(user_input: str) -> str:
    text = user_input or ""
    if any(k in text for k in ("不想", "拖延", "写不动", "动不了", "好累", "累")):
        return "用户可能处在低压陪伴/启动困难场景;先给角色内微反应,再给一个很小的下一步。"
    if any(k in text for k in ("开心", "好了", "搞定", "完成", "通过")):
        return "用户可能在分享进展;可以具体接住这件事,不要夸张庆祝。"
    if any(k in text for k in ("提醒", "天气", "查", "帮我")):
        return "用户有现实任务;完成任务时保持角色口吻,不要变成工具播报。"
    return ""


def _core_memory_type(mem_type: str) -> str | None:
    if mem_type in {"profile", "preference", "shared_moment", "open_thread"}:
        return mem_type
    if mem_type in {"anti_preference", "relationship_note"}:
        return "preference"
    if mem_type == "memory":
        return "profile"
    return None


# 身份类画像键(姓名/生日/职业/禁忌等)。这类稳定事实无论当前话题是否相关都应进入
# 提示词,否则用户名字、过敏等关键信息会在话题无关的轮次里凭空消失。
#
# 高精度子串 token:这些词几乎只出现在身份字段里,用子串匹配以兼容同义写法
# (如 "出生日期" 命中 "出生","过敏源" 命中 "过敏")。
_STABLE_PROFILE_KEY_TOKENS = (
    "名字", "姓名", "昵称", "称呼", "生日", "出生", "过敏", "忌口", "禁忌",
)
# 通用词(职业/工作/城市…)只做整键精确匹配,避免把 "工作进度""城市天气""学校作业"
# 这类话题性字段误判成身份事实而无限注入。
_STABLE_PROFILE_KEY_EXACT = frozenset(
    {
        "年龄", "职业", "工作", "职位", "身份", "学校", "学历", "专业",
        "城市", "所在地", "所在城市", "常驻城市", "居住地", "家乡",
        "联系方式", "电话", "邮箱",
    }
)


def _is_stable_profile_key(key: str) -> bool:
    k = (key or "").strip()
    if not k:
        return False
    if k in _STABLE_PROFILE_KEY_EXACT:
        return True
    return any(token in k for token in _STABLE_PROFILE_KEY_TOKENS)


def _relevant_profile_fields(
    fields: dict[str, str],
    user_input: str,
    *,
    limit: int,
) -> dict[str, str]:
    if not fields:
        return {}
    q_tokens = set(_simple_tokens(user_input))
    selected: dict[str, str] = {}
    scored: list[tuple[int, str, str]] = []
    for key, value in fields.items():
        if _is_stable_profile_key(key):
            # 身份类事实始终注入,不受词面重叠或 limit 限制。
            selected[key] = value
            continue
        tokens = set(_simple_tokens(f"{key} {value}"))
        score = len(q_tokens & tokens)
        if score > 0:
            scored.append((score, key, value))
    scored.sort(key=lambda item: item[0], reverse=True)
    for _, key, value in scored[:limit]:
        selected[key] = value
    return selected


def _simple_tokens(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]{1,2}|[a-zA-Z0-9_]+", text or "")


def _format_memory_hit(hit: dict) -> str:
    meta = hit.get("metadata") or {}
    title = meta.get("title")
    content = hit.get("content", "")
    bits = []
    if title:
        bits.append(str(title))
    bits.append(str(content))
    for key, label in (
        ("contact_reason", "由头"),
        ("callback_style", "回响方式"),
        ("emotional_color", "情绪色"),
        ("event_time", "事件时间"),
    ):
        value = meta.get(key)
        if value:
            bits.append(f"{label}:{value}")
    return " / ".join(bit for bit in bits if bit)


def _preference_valence(hit: dict) -> str:
    """判断一条偏好卡是"偏好"还是"避开"。

    抽取侧把喜欢/不喜欢都折进 mem_type=preference,价正负只在正文措辞里
    (extractor.py 的 prompt 明确只用 preference 一类)。注入时若不标出正负,
    模型可能把"讨厌X"误读成"喜欢X"去迎合。
    """
    meta = hit.get("metadata") or {}
    if meta.get("type") == "anti_preference":
        return "避开"
    text = f"{meta.get('title', '')} {hit.get('content', '')}"
    return "避开" if _NEGATIVE_INTEREST_RE.search(text) else "偏好"


def _relation_item_to_card(item: dict) -> tuple[str, dict]:
    content = str(
        item.get("content")
        or item.get("summary")
        or item.get("text")
        or item.get("title")
        or ""
    ).strip()
    if not content:
        return "", {}
    meta: dict = {
        "confidence": _clamp_float(item.get("confidence", 0.7), 0.3, 1.0),
        "importance": _clamp_float(item.get("importance", 0.65), 0.1, 1.0),
    }
    for key in (
        "title",
        "triggers",
        "emotional_color",
        "callback_style",
        "contact_reason",
        "event_time",
        "observed_at",
        "expires_at",
        "status",
        "source_turn_ids",
    ):
        value = item.get(key)
        if value:
            meta[key] = value
    keywords: list[str] = []
    for value in (meta.get("title"), meta.get("triggers"), meta.get("contact_reason")):
        if isinstance(value, str):
            keywords.extend(part for part in value.replace(",", " ").split() if part)
        elif isinstance(value, list):
            keywords.extend(str(part) for part in value if str(part).strip())
    if keywords:
        meta["keywords"] = keywords[:12]
        meta["tags"] = keywords[:6]
    return content, meta


_INTEREST_KEYWORDS = (
    "喜欢",
    "感兴趣",
    "关注",
    "在玩",
    "正在玩",
    "常聊",
    "沉迷",
    "追",
    "爱看",
    "爱玩",
    "想玩",
)
_INTEREST_KEY_RE = re.compile(r"(兴趣|爱好|喜欢|关注|游戏|作品|番剧|漫画|模型|产品)")
_NEGATIVE_INTEREST_RE = re.compile(r"(不喜欢|讨厌|反感|不感兴趣|不要|避雷)")
_INTEREST_PHRASE_RE = re.compile(
    r"(?:用户|我|他|她)?(?:最近|一直|正在|现在|平时|可能)?"
    r"(?:喜欢|感兴趣|关注|在玩|正在玩|常聊|沉迷|追|爱看|爱玩|想玩|偏好)"
    r"[:：]?(?P<topic>[^,，。；;\n]{2,36})"
)
_TOPIC_SPLIT_RE = re.compile(r"[、,/，;；\n]|和|以及|还有")


def _interest_key(key: str) -> bool:
    return bool(_INTEREST_KEY_RE.search(key or ""))


def _interest_text(text: str) -> bool:
    clean = text or ""
    if _NEGATIVE_INTEREST_RE.search(clean):
        return False
    return any(marker in clean for marker in _INTEREST_KEYWORDS)


def _extract_interest_topics_from_text(text: str) -> list[str]:
    if not _interest_text(text):
        return []
    topics: list[str] = []
    for match in _INTEREST_PHRASE_RE.finditer(text):
        topics.extend(_split_topic_candidates(match.group("topic")))
    return topics


def _split_topic_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for part in _TOPIC_SPLIT_RE.split(text or ""):
        clean = _clean_topic(part)
        if clean:
            candidates.append(clean)
    return candidates


def _clean_topic(value: str) -> str:
    clean = re.sub(
        r"^(用户|我|他|她)?(最近|一直|正在|现在|平时|可能)?",
        "",
        value or "",
    ).strip(" ：:，,。；;、")
    clean = re.sub(r"(这类|相关|内容|游戏|作品)?(的话题|相关内容|这件事)$", "", clean).strip()
    if not (2 <= len(clean) <= 24):
        return ""
    if _NEGATIVE_INTEREST_RE.search(clean):
        return ""
    if clean in {"用户", "自己", "事情", "东西", "内容", "话题"}:
        return ""
    return clean


def _dedupe_topics(topics: list[str], *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        clean = _clean_topic(topic)
        key = re.sub(r"\s+", "", clean).lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def _clamp_float(value: object, low: float, high: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, f))
