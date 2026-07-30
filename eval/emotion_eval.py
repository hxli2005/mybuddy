"""情绪 15 类识别评测(真实调用 LLM,无上下文单条分类)。

对 eval/data/emotion_zh.json(15 类 x 20 条,均衡分层,含难例)逐条跑
EmotionDetector.classify,量化情绪信号源头的可靠度:

指标:
  - valence 三分类准确率(附 Wilson 95% CI)
  - category top-1 准确率(总体 + 逐类,附 Wilson 95% CI)
  - 难例(hard) vs 易例(easy)准确率
  - intensity 落带率(预测 intensity 落入 gold band:1-2 / 3 / 4-5)
  - 15x15 混淆矩阵(写入报告文件;终端打印 top 混淆对)

注意:gold 标签为 AI 草案、待人工审校(见数据文件 _meta 与
docs/DATASET_METHODOLOGY.md §三.4),本脚本产出的数字应按草案口径引用。

用法:
    uv run python eval/emotion_eval.py                          # 全量 300 条
    uv run python eval/emotion_eval.py --limit 10               # 冒烟
    uv run python eval/emotion_eval.py --report eval/RESULTS_EMOTION.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mybuddy.config import load_config
from mybuddy.emotion.detector import VALID_CATEGORIES, EmotionDetector
from mybuddy.llm import make_provider

DATA = Path(__file__).parent / "data" / "emotion_zh.json"
CATEGORIES = [
    "anxiety", "sadness", "anger", "fatigue", "loneliness",
    "stress", "guilt", "shame", "fear", "disappointment",
    "boredom", "calm", "joy", "gratitude", "excitement",
]
NONE_LABEL = "(none)"  # classify 失败 / 解析失败 / 非法类别时的占位


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% 置信区间(比例指标)。返回 (lo, hi)。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def band_of(intensity: int) -> str:
    if intensity <= 2:
        return "1-2"
    if intensity == 3:
        return "3"
    return "4-5"


def fmt_pct(k: int, n: int, *, ci: bool = False) -> str:
    if n == 0:
        return "-"
    s = f"{k / n:.1%} ({k}/{n})"
    if ci:
        lo, hi = wilson(k, n)
        s += f" [CI {lo:.1%}-{hi:.1%}]"
    return s


async def classify_all(items: list[dict], detector: EmotionDetector, *,
                       concurrency: int, sleep: float) -> list[dict]:
    """逐条 classify(无上下文)。温和限速;失败(category 缺失)重试一次。"""
    sem = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def one(item: dict) -> dict:
        nonlocal done
        async with sem:
            await asyncio.sleep(sleep)  # 温和限速:每个并发槽位取件前小睡
            res = await detector.classify(item["text"])
            if res.category is None:  # LLM 调用失败 / JSON 解析失败 / 非法类别 → 重试一次
                await asyncio.sleep(2.0)
                res = await detector.classify(item["text"])
        async with lock:
            done += 1
            if done % 25 == 0 or done == len(items):
                print(f"  progress {done}/{len(items)}")
        return {
            "id": item["id"],
            "pred_valence": res.label,
            "pred_category": res.category or NONE_LABEL,
            "pred_intensity": res.intensity,
            "pred_strength": res.strength,
            "reason": res.reason,
        }

    return list(await asyncio.gather(*(one(it) for it in items)))


def compute_metrics(items: list[dict], preds: list[dict]) -> dict:
    by_id = {p["id"]: p for p in preds}
    rows = [(it, by_id[it["id"]]) for it in items]
    n = len(rows)

    val_ok = sum(1 for it, p in rows if p["pred_valence"] == it["gold_valence"])
    cat_ok = sum(1 for it, p in rows if p["pred_category"] == it["gold_category"])
    band_ok = sum(1 for it, p in rows if band_of(p["pred_intensity"]) == it["gold_intensity_band"])
    failed = sum(1 for _, p in rows if p["pred_category"] == NONE_LABEL)

    per_cat: dict[str, dict] = {}
    for cat in CATEGORIES:
        sub = [(it, p) for it, p in rows if it["gold_category"] == cat]
        per_cat[cat] = {
            "n": len(sub),
            "cat_ok": sum(1 for it, p in sub if p["pred_category"] == it["gold_category"]),
            "val_ok": sum(1 for it, p in sub if p["pred_valence"] == it["gold_valence"]),
            "band_ok": sum(1 for it, p in sub
                           if band_of(p["pred_intensity"]) == it["gold_intensity_band"]),
        }

    by_diff: dict[str, dict] = {}
    for diff in ("easy", "hard"):
        sub = [(it, p) for it, p in rows if it["difficulty"] == diff]
        by_diff[diff] = {
            "n": len(sub),
            "cat_ok": sum(1 for it, p in sub if p["pred_category"] == it["gold_category"]),
            "val_ok": sum(1 for it, p in sub if p["pred_valence"] == it["gold_valence"]),
        }

    confusion: dict[str, Counter] = defaultdict(Counter)
    for it, p in rows:
        confusion[it["gold_category"]][p["pred_category"]] += 1
    top_pairs = sorted(
        ((g, pr, c) for g, cnt in confusion.items() for pr, c in cnt.items() if pr != g),
        key=lambda x: -x[2],
    )

    val_confusion: dict[str, Counter] = defaultdict(Counter)
    for it, p in rows:
        val_confusion[it["gold_valence"]][p["pred_valence"]] += 1

    errors = [
        {
            "id": it["id"], "text": it["text"], "difficulty": it["difficulty"],
            "gold": it["gold_category"], "pred": p["pred_category"],
            "gold_val": it["gold_valence"], "pred_val": p["pred_valence"],
            "reason": p["reason"],
        }
        for it, p in rows
        if p["pred_category"] != it["gold_category"] or p["pred_valence"] != it["gold_valence"]
    ]

    return {
        "n": n, "failed": failed,
        "val_ok": val_ok, "cat_ok": cat_ok, "band_ok": band_ok,
        "per_cat": per_cat, "by_diff": by_diff,
        "confusion": {g: dict(c) for g, c in confusion.items()},
        "val_confusion": {g: dict(c) for g, c in val_confusion.items()},
        "top_pairs": top_pairs, "errors": errors,
    }


def print_summary(m: dict) -> None:
    n = m["n"]
    print(f"\n=== 总体(n={n},classify 兜底失败 {m['failed']} 条) ===")
    print(f"valence 三分类准确率 : {fmt_pct(m['val_ok'], n, ci=True)}")
    print(f"category top-1 准确率: {fmt_pct(m['cat_ok'], n, ci=True)}")
    print(f"intensity 落带率     : {fmt_pct(m['band_ok'], n, ci=True)}")

    print("\n=== 难例 vs 易例 ===")
    for diff in ("easy", "hard"):
        d = m["by_diff"][diff]
        print(f"{diff:<5} category {fmt_pct(d['cat_ok'], d['n'], ci=True)}   "
              f"valence {fmt_pct(d['val_ok'], d['n'])}")

    print("\n=== 逐类 category top-1 ===")
    print(f"{'category':<16}{'n':>4}{'cat_acc':>10}{'val_acc':>10}{'band':>8}")
    for cat in CATEGORIES:
        c = m["per_cat"][cat]
        if c["n"] == 0:
            continue
        print(f"{cat:<16}{c['n']:>4}"
              f"{c['cat_ok'] / c['n']:>10.1%}{c['val_ok'] / c['n']:>10.1%}"
              f"{c['band_ok'] / c['n']:>8.1%}")

    print("\n=== top 混淆对(gold → pred) ===")
    for g, p, c in m["top_pairs"][:10]:
        print(f"  {g:>15} → {p:<15} x{c}")


def render_report(m: dict, *, model: str, data_version: str, note: str = "") -> str:
    n = m["n"]
    val_lo, val_hi = wilson(m["val_ok"], n)
    cat_lo, cat_hi = wilson(m["cat_ok"], n)
    lines: list[str] = []
    add = lines.append

    add("# 情绪 15 类识别评测结果(emotion_zh)")
    add("")
    add(f"- 日期:{date.today().isoformat()}")
    add(f"- 数据:`eval/data/emotion_zh.json`({data_version}),n={n},15 类 x 20 条,"
        "每类 ≥6 条难例(克制表达/反话/混合情绪/省略主语短句)")
    add(f"- 模型:`{model}`(temperature=0,无上下文单条分类,脚本 `eval/emotion_eval.py`)")
    add(f"- classify 兜底失败(重试一次后仍无有效 category,按错计):{m['failed']} 条")
    if note:
        add(f"- 备注:{note}")
    add("")
    add("> **口径:gold 待人工审校。** 本数据集的 gold 标签"
        "(valence/category/intensity_band)为 AI 起草的草案,尚未完成人工全量审校与"
        "分歧样本三人标注(方法见 `docs/DATASET_METHODOLOGY.md` §三.4)。"
        "下表数字应按\"模型 vs 草案标注\"的一致率解读,不等同于最终准确率;"
        "审校后需在同一集合上重跑并更新本文件。"
        "另:15 类人类标注亦难全一致,category 指标应对照人工 kappa 天花板解读,"
        "不按 100% 解读。")
    add("")
    add("## 总体指标")
    add("")
    add("| 指标 | 值 | Wilson 95% CI |")
    add("|---|---|---|")
    add(f"| valence 三分类准确率 | {m['val_ok'] / n:.1%} ({m['val_ok']}/{n}) "
        f"| {val_lo:.1%} – {val_hi:.1%} |")
    add(f"| category top-1 准确率(15 类) | {m['cat_ok'] / n:.1%} ({m['cat_ok']}/{n}) "
        f"| {cat_lo:.1%} – {cat_hi:.1%} |")
    band_lo, band_hi = wilson(m["band_ok"], n)
    add(f"| intensity 落带率(1-2 / 3 / 4-5) | {m['band_ok'] / n:.1%} ({m['band_ok']}/{n}) "
        f"| {band_lo:.1%} – {band_hi:.1%} |")
    add("")
    add("## 难例 vs 易例")
    add("")
    add("| 分层 | n | category top-1 | Wilson 95% CI | valence 准确率 |")
    add("|---|---|---|---|---|")
    for diff in ("easy", "hard"):
        d = m["by_diff"][diff]
        lo, hi = wilson(d["cat_ok"], d["n"])
        add(f"| {diff} | {d['n']} | {d['cat_ok'] / d['n']:.1%} ({d['cat_ok']}/{d['n']}) "
            f"| {lo:.1%} – {hi:.1%} | {d['val_ok'] / d['n']:.1%} |")
    add("")
    add("## 逐类 category top-1")
    add("")
    add("| category | n | category top-1 | valence 准确率 | intensity 落带率 |")
    add("|---|---|---|---|---|")
    for cat in CATEGORIES:
        c = m["per_cat"][cat]
        add(f"| {cat} | {c['n']} | {c['cat_ok'] / c['n']:.1%} ({c['cat_ok']}/{c['n']}) "
            f"| {c['val_ok'] / c['n']:.1%} | {c['band_ok'] / c['n']:.1%} |")
    add("")
    add("## valence 混淆(gold 行 → pred 列)")
    add("")
    vals = ["negative", "neutral", "positive"]
    add("| gold \\ pred | " + " | ".join(vals) + " |")
    add("|---|" + "---|" * len(vals))
    for g in vals:
        row = m["val_confusion"].get(g, {})
        add(f"| {g} | " + " | ".join(str(row.get(p, 0)) for p in vals) + " |")
    add("")
    add("## 15x15 混淆矩阵(gold 行 → pred 列)")
    add("")
    cols = CATEGORIES + [NONE_LABEL]
    add("| gold \\ pred | " + " | ".join(c[:4] for c in CATEGORIES) + " | none |")
    add("|---|" + "---|" * len(cols))
    for g in CATEGORIES:
        row = m["confusion"].get(g, {})
        add(f"| {g} | " + " | ".join(str(row.get(p, 0)) for p in cols) + " |")
    add("")
    add(f"(列名为类别前 4 字母,顺序:{', '.join(CATEGORIES)};none=兜底失败)")
    add("")
    add("## top 混淆对")
    add("")
    add("| gold | pred | 次数 |")
    add("|---|---|---|")
    for g, p, c in m["top_pairs"][:10]:
        add(f"| {g} | {p} | {c} |")
    add("")
    add("## 误判样本(category 或 valence 不一致,供人工审校参考)")
    add("")
    add("| id | difficulty | text | gold | pred | gold_val | pred_val |")
    add("|---|---|---|---|---|---|---|")
    for e in m["errors"]:
        text = e["text"].replace("|", "\\|")
        add(f"| {e['id']} | {e['difficulty']} | {text} | {e['gold']} | {e['pred']} "
            f"| {e['gold_val']} | {e['pred_val']} |")
    add("")
    return "\n".join(lines)


async def amain(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    # 分类输出是 ~60 token 的小 JSON;把 max_tokens 压到小值(仅本进程内存中覆盖,
    # 不改 config.yaml),既省成本也避免 OpenRouter 按 max_tokens 预扣余额报 402。
    update: dict = {"max_tokens": args.max_tokens}
    if args.model:  # 备用通道:余额不足等情况下用替代模型跑,报告里必须注明
        update["small_model"] = args.model
    llm_cfg = cfg.llm.model_copy(update=update)
    provider = make_provider(llm_cfg)
    detector = EmotionDetector(provider, small_model=llm_cfg.small_model)
    model = llm_cfg.small_model or llm_cfg.model

    data = json.loads(DATA.read_text(encoding="utf-8"))
    items = data["items"]
    if args.limit:
        items = items[: args.limit]
    assert all(it["gold_category"] in VALID_CATEGORIES for it in items)
    print(f"评测 {len(items)} 条,model={model},concurrency={args.concurrency}")

    preds = await classify_all(items, detector,
                               concurrency=args.concurrency, sleep=args.sleep)
    m = compute_metrics(items, preds)
    print_summary(m)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"preds": preds, "metrics": {k: v for k, v in m.items()
                                                    if k not in ("top_pairs", "errors")}},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print(f"\n原始预测已写入 {args.json_out}")
    if args.report:
        version = data.get("_meta", {}).get("version", "?")
        Path(args.report).write_text(
            render_report(m, model=model, data_version=version, note=args.note),
            encoding="utf-8")
        print(f"报告已写入 {args.report}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条(冒烟用)")
    ap.add_argument("--concurrency", type=int, default=3, help="并发槽位(温和限速)")
    ap.add_argument("--sleep", type=float, default=0.4, help="每请求前的限速小睡秒数")
    ap.add_argument("--max-tokens", type=int, default=96,
                    help="覆盖 llm.max_tokens(仅内存中,不改 config.yaml)")
    ap.add_argument("--model", default="",
                    help="覆盖 small_model(仅内存中);默认用 config.yaml 的 llm.small_model")
    ap.add_argument("--note", default="", help="写进报告头部的备注(如模型替换原因)")
    ap.add_argument("--report", default="", help="把 markdown 报告写到该路径")
    ap.add_argument("--json-out", default="", help="把原始预测 JSON 写到该路径")
    args = ap.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
