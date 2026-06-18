"""LoCoMo 公开基准——检索召回评测(retrieval recall)。

LoCoMo(Snap Research,arXiv:2402.17753)是长期对话记忆的标准集:每个样本是一段
横跨多个 session 的长对话,QA 带 category 与 evidence(证据 turn 的 dia_id)。

本脚本测的是**检索召回**:把对话每个 turn 作为一张记忆卡灌入 LongTermMemory,
对每个问题跑 ltm.search,看证据 turn 是否进了 top_k。指标 Recall@k / Hit@k,按
category 分桶。

⚠️ 口径说明:这测的是"检索层把证据找回来的能力",**不是** LoCoMo 排行榜上的
端到端问答分(那是 LLM-judge 打的答案正确率)。两者不可直接比;但检索召回是
端到端问答的上界与前提,可与文献里报告的 retrieval recall 对标。
对话为英文(测的是检索引擎本身;embedding 多语种可用)。category 5(对抗/不可答)
考的是"该不该拒答",非检索任务,默认排除。

数据获取(不入库):
    mkdir -p eval/data/external
    curl -L -o eval/data/external/locomo10.json \\
      https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

用法:
    uv run python eval/locomo_eval.py --samples 2 --topk 5
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mybuddy._time import utcnow
from mybuddy.config import load_config
from mybuddy.memory import LongTermMemory

DATA = Path(__file__).parent / "data" / "external" / "locomo10.json"
CATEGORY = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
SKIP_CATEGORIES = {5}  # 对抗/不可答:考拒答而非检索


def _index_conversation(conv: dict, persist_dir: Path) -> LongTermMemory:
    cfg = load_config("config.yaml")
    ltm = LongTermMemory(persist_dir=str(persist_dir), embedding_model=cfg.memory.embedding_model)
    sess_ids = sorted(
        (k for k in conv if k.startswith("session") and isinstance(conv[k], list)),
        key=lambda k: int(k.split("_")[1]),
    )
    now = utcnow()
    n = len(sess_ids)
    for idx, sid in enumerate(sess_ids):
        # 用 session 顺序设时间戳:越靠后的 session 越新,供时态重排利用
        ts = (now - timedelta(days=(n - idx) * 3)).isoformat(timespec="seconds")
        for turn in conv[sid]:
            text = (turn.get("text") or "").strip()
            if not text:
                continue
            ltm.add(
                f"{turn['speaker']}: {text}",
                mem_type="memory",
                uid=turn["dia_id"],
                session_id=sid,
                extra_meta={"observed_at": ts, "created_at": ts, "last_seen_at": ts},
            )
    return ltm


def _attach_semantic(ltm: LongTermMemory, persist_dir: Path) -> bool:
    cfg = load_config("config.yaml")
    emb = cfg.memory.embedding
    eff = emb if emb.api_key else emb.model_copy(
        update={"api_key": cfg.llm.api_key, "base_url": cfg.llm.base_url or emb.base_url}
    )
    if not eff.enabled:
        eff = eff.model_copy(update={"enabled": True})
    from mybuddy.memory.semantic import SemanticRecall

    sem = SemanticRecall(eff, persist_dir / "vectors.db")
    if not sem.enabled:
        return False
    ltm.attach_semantic(sem)
    ltm.reconcile_semantic()
    return True


def _avg(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 3) if xs else 0.0


def run(samples: int, tiers: list[int]) -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    data = data[:samples]
    max_k = max(tiers)
    # buckets[cat][k] = {"hit": [...], "recall": [...]}
    buckets: dict[str, dict[int, dict[str, list[float]]]] = {}

    def record(cat_name: str, got_ranked: list[str], ev: set[str]) -> None:
        for key in ("ALL", cat_name):
            per_k = buckets.setdefault(key, {})
            for k in tiers:
                got = set(got_ranked[:k])
                b = per_k.setdefault(k, {"hit": [], "recall": []})
                b["hit"].append(1.0 if (got & ev) else 0.0)
                b["recall"].append(len(got & ev) / len(ev))

    for i, sample in enumerate(data):
        with tempfile.TemporaryDirectory() as d:
            ltm = _index_conversation(sample["conversation"], Path(d))
            if not _attach_semantic(ltm, Path(d)):
                print("[!] 语义召回不可用(embedding 未就绪/无网络),退出。")
                return
            n_cards, n_q = ltm.count(), 0
            for qa in sample["qa"]:
                cat = qa.get("category")
                if cat in SKIP_CATEGORIES:
                    continue
                evidence = {e for e in (qa.get("evidence") or []) if isinstance(e, str)}
                if not evidence:
                    continue
                n_q += 1
                hits = ltm.search(qa["question"], top_k=max_k, use_semantic=True)
                record(CATEGORY.get(cat, str(cat)), [h["id"] for h in hits], evidence)
            print(f"  样本{i+1} {sample.get('sample_id','')}: {n_cards} 卡 / {n_q} 题")

    hdr = "".join(f"{'H@'+str(k):>7}{'R@'+str(k):>7}" for k in tiers)
    print(f"\n=== LoCoMo 检索召回({len(data)} 个样本) ===")
    print(f"{'类别':<13}{'n':>5}{hdr}")
    for key in ["ALL", "single-hop", "multi-hop", "temporal", "open-domain"]:
        if key not in buckets:
            continue
        per_k = buckets[key]
        n = len(per_k[tiers[0]]["hit"])
        cells = "".join(f"{_avg(per_k[k]['hit']):>7}{_avg(per_k[k]['recall']):>7}" for k in tiers)
        print(f"{key:<13}{n:>5}{cells}")


def main() -> None:
    if not DATA.exists():
        print(f"未找到数据集:{DATA}\n请先按脚本顶部说明下载 locomo10.json。")
        sys.exit(1)
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=2, help="评测前 N 个对话样本(控成本)")
    ap.add_argument("--tiers", type=str, default="5,10,20", help="逗号分隔的 top-k 档位")
    args = ap.parse_args()
    run(args.samples, [int(x) for x in args.tiers.split(",")])


if __name__ == "__main__":
    main()
