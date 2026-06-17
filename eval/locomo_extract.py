"""LoCoMo——抽取管线下的检索召回(生产真实水位)。

c3/c4 在原始 turn / chunk 上测,是悲观下界:生产其实检索的是 FactExtractor 抽取后的
事实卡。本脚本把 LoCoMo 对话按窗口喂给真实的 FactExtractor,抽成事实卡,给每条卡打上
来源窗口的 dia_id 溯源,再用与 c3/c4 完全相同的口径(证据 dia_id 是否被覆盖)测召回。

窗口大小 = 溯源粒度,默认 8,与 chunk 同量级,从而隔离"LLM 抽取的事实 vs 原始 chunk
文本"这一个变量。一个抽取卡若其来源窗口包含证据 turn,即记为覆盖到(与 chunk 同样的
计分慷慨度,对比公平)。

⚠️ 需真实 LLM(每窗一次 small_model 调用),有成本/耗时。

用法:
    uv run python eval/locomo_extract.py --samples 1 --window 8 --budget 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.locomo_eval import CATEGORY, DATA, SKIP_CATEGORIES, _attach_semantic, _avg
from mybuddy.config import load_config
from mybuddy.llm import make_provider
from mybuddy.memory import LongTermMemory
from mybuddy.memory.extractor import FactExtractor


def _flatten_turns(conv: dict) -> list[dict]:
    sess_ids = sorted(
        (k for k in conv if k.startswith("session") and isinstance(conv[k], list)),
        key=lambda k: int(k.split("_")[1]),
    )
    out: list[dict] = []
    for sid in sess_ids:
        for t in conv[sid]:
            if (t.get("text") or "").strip():
                out.append(t)
    return out


async def _extract(sample: dict, window: int) -> tuple[list[dict], int]:
    """把对话按窗口喂给真实 FactExtractor,返回 [{uid,text,prov}...] 与 turn 总数。"""
    cfg = load_config("config.yaml")
    extractor = FactExtractor(make_provider(cfg.llm), cfg.llm.small_model)
    turns = _flatten_turns(sample["conversation"])
    facts: list[dict] = []
    for i in range(0, len(turns), window):
        grp = turns[i : i + window]
        msgs = [f"{t['speaker']}: {t['text']}" for t in grp]
        dia_ids = [t["dia_id"] for t in grp]
        res = await extractor.extract(msgs)
        texts = list(res.facts) + [f"{k}: {v}" for k, v in res.profile_fields.items()]
        for j, text in enumerate(texts):
            facts.append({"uid": f"w{i}_{j}", "text": text, "prov": dia_ids})
    return facts, len(turns)


def _index_facts(facts: list[dict], persist_dir: Path):
    cfg = load_config("config.yaml")
    ltm = LongTermMemory(persist_dir=str(persist_dir), embedding_model=cfg.memory.embedding_model)
    prov: dict[str, list[str]] = {}
    for f in facts:
        ltm.add(f["text"], mem_type="memory", uid=f["uid"], session_id="eval")
        prov[f["uid"]] = f["prov"]
    return ltm, prov


def _score(ltm, prov, qa_list, budget):
    buckets: dict[str, dict[str, list[float]]] = {}

    def rec(cat: str, hit: float, recall: float) -> None:
        for key in ("ALL", cat):
            b = buckets.setdefault(key, {"hit": [], "recall": []})
            b["hit"].append(hit)
            b["recall"].append(recall)

    for qa in qa_list:
        cat = qa.get("category")
        if cat in SKIP_CATEGORIES:
            continue
        ev = {e for e in (qa.get("evidence") or []) if isinstance(e, str)}
        if not ev:
            continue
        hits = ltm.search(qa["question"], top_k=40, use_semantic=True)
        covered: set[str] = set()
        for h in hits:
            # 按"去重覆盖的源 turn 数"计预算:同一窗口的多张卡共享溯源,不重复占预算
            covered.update(prov.get(h["id"], []))
            if len(covered) >= budget:
                break
        rec(CATEGORY.get(cat, str(cat)), 1.0 if ev & covered else 0.0, len(ev & covered) / len(ev))
    return buckets


def run(samples: int, window: int, budget: int) -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))[:samples]
    merged: dict[str, dict[str, list[float]]] = {}
    cache_dir = DATA.parent
    for si, sample in enumerate(data):
        sid = sample.get("sample_id", str(si))
        cache = cache_dir / f"extract_cache_{sid}_w{window}.json"
        if cache.exists():  # 抽取结果落盘,重跑只重嵌入不重烧 LLM
            payload = json.loads(cache.read_text(encoding="utf-8"))
            facts, n_turns, tag = payload["facts"], payload["n_turns"], "cache"
        else:
            facts, n_turns = asyncio.run(_extract(sample, window))
            cache.write_text(json.dumps({"facts": facts, "n_turns": n_turns}, ensure_ascii=False))
            tag = "extracted"
        with tempfile.TemporaryDirectory() as d:
            ltm, prov = _index_facts(facts, Path(d))
            if not _attach_semantic(ltm, Path(d)):
                print("[!] 语义召回不可用,退出。")
                return
            print(f"  样本{si + 1} {sid}: {n_turns} turn → {len(facts)} 抽取卡 ({tag})")
            b = _score(ltm, prov, sample["qa"], budget)
            for key, d2 in b.items():
                m = merged.setdefault(key, {"hit": [], "recall": []})
                m["hit"] += d2["hit"]
                m["recall"] += d2["recall"]

    print(f"\n=== LoCoMo 抽取管线下检索召回(窗口={window},预算~{budget}turn,{samples}样本) ===")
    print(f"{'类别':<13}{'n':>5}{'Hit':>9}{'Recall':>9}")
    for key in ["ALL", "single-hop", "multi-hop", "temporal", "open-domain"]:
        if key in merged:
            b = merged[key]
            print(f"{key:<13}{len(b['hit']):>5}{_avg(b['hit']):>9}{_avg(b['recall']):>9}")


def main() -> None:
    if not DATA.exists():
        print(f"未找到数据集:{DATA}(见 locomo_eval.py 顶部下载说明)")
        sys.exit(1)
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--window", type=int, default=8, help="每个抽取窗口的 turn 数(=溯源粒度)")
    ap.add_argument("--budget", type=int, default=20)
    args = ap.parse_args()
    run(args.samples, args.window, args.budget)


if __name__ == "__main__":
    main()
