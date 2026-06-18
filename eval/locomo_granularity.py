"""LoCoMo 检索粒度对比实验(攻 c3 的"根本 miss"短板)。

c3 用 turn 级索引,single-hop H@5 仅 0.53。猜想:turn 太碎(短对话句难匹配问题),
改成 chunk / session 级索引能抬升召回。

公平性:不同粒度一个单元覆盖的 turn 数差异很大(session≈18 turn,turn=1),
所以**在同一"上下文预算 budget(turn 数)"下比证据覆盖率**——按排名累计取回单元,
直到累计 turn 数达 budget 为止,再看证据 turn 是否被覆盖。回答:同样喂 ~B turn 的
上下文,哪种粒度把证据带回来得最多。

用法:
    uv run python eval/locomo_granularity.py --samples 2 --budget 20
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.locomo_eval import CATEGORY, DATA, SKIP_CATEGORIES, _attach_semantic, _avg
from mybuddy._time import utcnow
from mybuddy.config import load_config
from mybuddy.memory import LongTermMemory


def _index(conv: dict, persist_dir: Path, granularity: str, chunk_size: int):
    """按粒度建库,返回 (ltm, unit_turns: uid -> [dia_id...])。"""
    cfg = load_config("config.yaml")
    ltm = LongTermMemory(persist_dir=str(persist_dir), embedding_model=cfg.memory.embedding_model)
    sess_ids = sorted(
        (k for k in conv if k.startswith("session") and isinstance(conv[k], list)),
        key=lambda k: int(k.split("_")[1]),
    )
    unit_turns: dict[str, list[str]] = {}
    now = utcnow()
    n = len(sess_ids)

    def fmt(t: dict) -> str:
        return f"{t['speaker']}: {(t.get('text') or '').strip()}"

    for idx, sid in enumerate(sess_ids):
        ts = (now - timedelta(days=(n - idx) * 3)).isoformat(timespec="seconds")
        meta = {"observed_at": ts, "created_at": ts, "last_seen_at": ts}
        turns = [t for t in conv[sid] if (t.get("text") or "").strip()]
        if granularity == "turn":
            units = [(t["dia_id"], [t]) for t in turns]
        elif granularity == "session":
            units = [(sid, turns)] if turns else []
        elif granularity == "chunk":
            units = [
                (f"{sid}#c{ci // chunk_size}", turns[ci : ci + chunk_size])
                for ci in range(0, len(turns), chunk_size)
            ]
        else:
            raise ValueError(granularity)
        for uid, grp in units:
            if not grp:
                continue
            ltm.add("\n".join(fmt(t) for t in grp), mem_type="memory", uid=uid,
                    session_id=sid, extra_meta=dict(meta))
            unit_turns[uid] = [t["dia_id"] for t in grp]
    return ltm, unit_turns


def _score(ltm, unit_turns, qa_list, budget):
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
        acc = 0
        for h in hits:
            turns = unit_turns.get(h["id"], [])
            covered.update(turns)
            acc += len(turns)
            if acc >= budget:
                break
        rec(CATEGORY.get(cat, str(cat)), 1.0 if ev & covered else 0.0, len(ev & covered) / len(ev))
    return buckets


def run(samples: int, budget: int, grans: list[str]) -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))[:samples]
    results: dict[str, dict] = {}
    for gran in grans:
        merged: dict[str, dict[str, list[float]]] = {}
        unit_counts = []
        for sample in data:
            with tempfile.TemporaryDirectory() as d:
                ltm, unit_turns = _index(sample["conversation"], Path(d), gran, _CHUNK)
                if not _attach_semantic(ltm, Path(d)):
                    print("[!] 语义召回不可用,退出。")
                    return
                unit_counts.append(ltm.count())
                b = _score(ltm, unit_turns, sample["qa"], budget)
                for key, d2 in b.items():
                    m = merged.setdefault(key, {"hit": [], "recall": []})
                    m["hit"] += d2["hit"]
                    m["recall"] += d2["recall"]
        results[gran] = merged
        print(f"  [{gran}] 平均单元数 {sum(unit_counts)//len(unit_counts)}")

    print(f"\n=== LoCoMo 粒度对比(同上下文预算 ~{budget} turn,{samples} 样本) ===")
    cats = ["ALL", "single-hop", "multi-hop", "temporal"]
    print(f"{'粒度':<12}" + "".join(f"{c:>13}" for c in cats))
    for gran in grans:
        cells = ""
        for c in cats:
            b = results[gran].get(c)
            cells += f"{(_avg(b['hit']) if b else 0):>13}"
        print(f"{gran:<12}" + cells)
    print("(数值=该上下文预算内证据被覆盖的比例 Hit)")


_CHUNK = 4


def main() -> None:
    global _CHUNK
    if not DATA.exists():
        print(f"未找到数据集:{DATA}(见 locomo_eval.py 顶部下载说明)")
        sys.exit(1)
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=2)
    ap.add_argument("--budget", type=int, default=20, help="上下文预算(turn 数)")
    ap.add_argument("--chunk", type=int, default=4, help="chunk 粒度的每块 turn 数")
    ap.add_argument("--grans", type=str, default="turn,chunk,session")
    args = ap.parse_args()
    _CHUNK = args.chunk
    run(args.samples, args.budget, args.grans.split(","))


if __name__ == "__main__":
    main()
