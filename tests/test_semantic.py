"""可选语义召回(API embedding + 旁路向量索引)测试。

用注入的假 embedder 验证:
  - 关闭时 search 行为与纯词法一致;
  - 开启时语义召回能找回"换了说法、词法零重叠"的同义卡;
  - 旁路向量索引持久化 + prune;
  - embedder 失败时静默降级为纯词法。
"""

from __future__ import annotations

from mybuddy.config import EmbeddingConfig
from mybuddy.memory.long_term import LongTermMemory
from mybuddy.memory.semantic import SemanticRecall, VectorIndex


class ConceptEmbedder:
    """把"同概念、不同措辞"的文本映射到同一向量(模拟语义)。"""

    # 每组关键词 → 一个维度;命中即该维=1。
    GROUPS = [
        ("工作", "公司", "跳槽", "离职", "上班", "辞职"),
        ("咖啡", "美式", "拿铁", "手冲"),
        ("拖延", "报告", "开头", "不想写"),
    ]
    ready = True

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * (len(self.GROUPS) + 1)
            hit = False
            for i, kws in enumerate(self.GROUPS):
                if any(k in t for k in kws):
                    vec[i] = 1.0
                    hit = True
            if not hit:
                vec[-1] = 1.0  # 兜底,避免零向量
            out.append(vec)
        return out


class BrokenEmbedder:
    ready = True

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []  # 模拟 API 失败


def _ltm(tmp_path) -> LongTermMemory:
    d = tmp_path / "mem"
    d.mkdir()
    return LongTermMemory(persist_dir=str(d))


def _enabled_cfg() -> EmbeddingConfig:
    return EmbeddingConfig(enabled=True, api_key="test-key")


def test_semantic_recalls_paraphrase_that_lexical_misses(tmp_path) -> None:
    ltm = _ltm(tmp_path)
    # 一张"工作/公司"概念的卡,措辞与待测 query 词法零重叠。
    ltm.add("对目前公司的氛围越来越不满意", mem_type="memory")

    sem = SemanticRecall(_enabled_cfg(), tmp_path / "vectors.db", client=ConceptEmbedder())
    ltm.attach_semantic(sem)
    assert ltm.reconcile_semantic() == 1

    query = "我最近特别想跳槽"
    # 纯词法:词法零重叠 → 找不到
    assert ltm.search(query, mem_type="memory") == []
    # 混合:语义召回把它捞回来
    hybrid = ltm.search(query, mem_type="memory", use_semantic=True)
    assert any("公司" in h["content"] for h in hybrid)


def test_disabled_semantic_is_pure_lexical(tmp_path) -> None:
    ltm = _ltm(tmp_path)
    ltm.add("对目前公司的氛围越来越不满意", mem_type="memory")
    # enabled=False:即便挂了召回器,use_semantic 也不生效
    sem = SemanticRecall(
        EmbeddingConfig(enabled=False, api_key="x"),
        tmp_path / "vectors.db",
        client=ConceptEmbedder(),
    )
    ltm.attach_semantic(sem)
    assert sem.enabled is False
    assert ltm.reconcile_semantic() == 0
    assert ltm.search("我最近特别想跳槽", mem_type="memory", use_semantic=True) == []


def test_hybrid_keeps_lexical_hits(tmp_path) -> None:
    ltm = _ltm(tmp_path)
    ltm.add("用户小明喜欢喝美式咖啡", mem_type="memory")
    sem = SemanticRecall(_enabled_cfg(), tmp_path / "vectors.db", client=ConceptEmbedder())
    ltm.attach_semantic(sem)
    ltm.reconcile_semantic()
    # 词法本就能命中"咖啡",混合后仍在结果里
    hits = ltm.search("咖啡", mem_type="memory", use_semantic=True)
    assert any("美式" in h["content"] for h in hits)


def test_vector_index_persists_and_prunes(tmp_path) -> None:
    ltm = _ltm(tmp_path)
    a = ltm.add("对目前公司的氛围越来越不满意", mem_type="memory")
    ltm.add("用户喜欢手冲咖啡", mem_type="memory")
    sem = SemanticRecall(_enabled_cfg(), tmp_path / "vectors.db", client=ConceptEmbedder())
    ltm.attach_semantic(sem)
    assert ltm.reconcile_semantic() == 2

    index = VectorIndex(tmp_path / "vectors.db")
    assert len(index.hashes()) == 2

    # 删卡后重建:索引应 prune 掉
    ltm.delete(a)
    ltm.reconcile_semantic()
    assert len(VectorIndex(tmp_path / "vectors.db").hashes()) == 1


def test_broken_embedder_degrades_to_lexical(tmp_path) -> None:
    ltm = _ltm(tmp_path)
    ltm.add("用户小明喜欢喝美式咖啡", mem_type="memory")
    sem = SemanticRecall(_enabled_cfg(), tmp_path / "vectors.db", client=BrokenEmbedder())
    ltm.attach_semantic(sem)

    assert ltm.reconcile_semantic() == 0  # embed 失败,不写向量
    # 混合检索:语义路返回空,词法仍正常工作
    hits = ltm.search("咖啡", mem_type="memory", use_semantic=True)
    assert any("美式" in h["content"] for h in hits)


def test_reconcile_is_idempotent_on_unchanged(tmp_path) -> None:
    ltm = _ltm(tmp_path)
    ltm.add("用户喜欢手冲咖啡", mem_type="memory")
    embedder = ConceptEmbedder()
    sem = SemanticRecall(_enabled_cfg(), tmp_path / "vectors.db", client=embedder)
    ltm.attach_semantic(sem)

    assert ltm.reconcile_semantic() == 1
    calls_after_first = embedder.calls
    # 无变更:第二次 reconcile 不应再调用 embed(只比对 hash)
    assert ltm.reconcile_semantic() == 0
    assert embedder.calls == calls_after_first
