"""长期记忆召回评测。

对同一份记忆 corpus,在两档检索下跑同一批 query,量化召回质量:
  - lexical:纯词法(默认,离线可跑)
  - hybrid :词法 + 语义 RRF 融合(需 embedding,联网)

指标:Hit@1 / Hit@3 / MRR,总分并按 kind(direct / paraphrase / temporal)分桶。
这套消融用来回答:语义召回到底补回了多少"换词召回"。

用法:
    uv run python eval/memory_eval.py --mode lexical          # 离线基线
    uv run python eval/memory_eval.py --mode both --topk 5    # 对照(hybrid 需联网)
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mybuddy.config import load_config
from mybuddy.memory import LongTermMemory

DATA = Path(__file__).parent / "data" / "memory_zh.json"
TOPK = 5


def _build_ltm(corpus: list[dict], persist_dir: Path) -> LongTermMemory:
    cfg = load_config("config.yaml")
    ltm = LongTermMemory(persist_dir=str(persist_dir), embedding_model=cfg.memory.embedding_model)
    for card in corpus:
        ltm.add(card["content"], mem_type=card["type"], uid=card["id"], session_id="eval")
    return ltm


def _attach_semantic(ltm: LongTermMemory, persist_dir: Path) -> bool:
    """挂载语义召回(复刻 manager 的装配),返回是否成功。"""
    cfg = load_config("config.yaml")
    emb = cfg.memory.embedding
    effective = emb
    if not emb.api_key:
        effective = emb.model_copy(
            update={"api_key": cfg.llm.api_key, "base_url": cfg.llm.base_url or emb.base_url}
        )
    if not effective.enabled:
        effective = effective.model_copy(update={"enabled": True})
    from mybuddy.memory.semantic import SemanticRecall

    sem = SemanticRecall(effective, persist_dir / "vectors.db")
    if not sem.enabled:
        return False
    ltm.attach_semantic(sem)
    n = ltm.reconcile_semantic()  # 把全部 corpus 卡嵌入向量索引
    print(f"  [hybrid] 语义索引就绪,嵌入 {n} 张卡")
    return True


def _metrics(ltm: LongTermMemory, queries: list[dict], *, use_semantic: bool) -> dict:
    buckets: dict[str, list[float]] = {}
    rows = []
    for item in queries:
        hits = ltm.search(item["q"], top_k=TOPK, use_semantic=use_semantic)
        ids = [h["id"] for h in hits]
        gold = set(item["gold"])
        rank = next((i + 1 for i, cid in enumerate(ids) if cid in gold), 0)
        hit1 = 1.0 if rank == 1 else 0.0
        hit3 = 1.0 if 0 < rank <= 3 else 0.0
        mrr = 1.0 / rank if rank else 0.0
        rows.append({"q": item["q"], "kind": item["kind"], "rank": rank, "top": ids[:3]})
        for key in ("ALL", item["kind"]):
            buckets.setdefault(key, []).append(mrr)
        buckets.setdefault(f"{item['kind']}__hit1", []).append(hit1)
        buckets.setdefault("ALL__hit1", []).append(hit1)
        buckets.setdefault(f"{item['kind']}__hit3", []).append(hit3)
        buckets.setdefault("ALL__hit3", []).append(hit3)

    def avg(key: str) -> float:
        vals = buckets.get(key, [])
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    kinds = sorted({q["kind"] for q in queries})
    summary = {
        "ALL": {"n": len(queries), "hit1": avg("ALL__hit1"), "hit3": avg("ALL__hit3"), "mrr": avg("ALL")},
    }
    for k in kinds:
        summary[k] = {
            "n": sum(1 for q in queries if q["kind"] == k),
            "hit1": avg(f"{k}__hit1"),
            "hit3": avg(f"{k}__hit3"),
            "mrr": avg(k),
        }
    return {"summary": summary, "rows": rows}


def _print_summary(title: str, summary: dict) -> None:
    print(f"\n=== {title} ===")
    print(f"{'bucket':<12}{'n':>4}{'Hit@1':>9}{'Hit@3':>9}{'MRR':>8}")
    for key, m in summary.items():
        print(f"{key:<12}{m['n']:>4}{m['hit1']:>9}{m['hit3']:>9}{m['mrr']:>8}")


def run(mode: str) -> dict:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    corpus, queries = data["corpus"], data["queries"]
    out: dict = {}

    if mode in ("lexical", "both"):
        with tempfile.TemporaryDirectory() as d:
            ltm = _build_ltm(corpus, Path(d))
            res = _metrics(ltm, queries, use_semantic=False)
            _print_summary("lexical(纯词法)", res["summary"])
            out["lexical"] = res["summary"]

    if mode in ("hybrid", "both"):
        with tempfile.TemporaryDirectory() as d:
            ltm = _build_ltm(corpus, Path(d))
            if _attach_semantic(ltm, Path(d)):
                res = _metrics(ltm, queries, use_semantic=True)
                _print_summary("hybrid(词法+语义RRF)", res["summary"])
                out["hybrid"] = res["summary"]
            else:
                print("\n[hybrid] 语义召回不可用(embedding 未就绪/无网络),跳过。")

    if "lexical" in out and "hybrid" in out:
        d_all = out["hybrid"]["ALL"]["mrr"] - out["lexical"]["ALL"]["mrr"]
        d_par = out["hybrid"].get("paraphrase", {}).get("mrr", 0) - out["lexical"].get("paraphrase", {}).get("mrr", 0)
        print(f"\nΔMRR 总体: {d_all:+.3f}   ΔMRR 换词类: {d_par:+.3f}")
    return out


def main() -> None:
    global TOPK
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["lexical", "hybrid", "both"], default="both")
    ap.add_argument("--topk", type=int, default=TOPK)
    args = ap.parse_args()
    TOPK = args.topk
    run(args.mode)


if __name__ == "__main__":
    main()
