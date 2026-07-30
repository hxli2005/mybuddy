"""无感化评估的效标效度评测(ACCEPTANCE_PLAN §2 实验 B)。

把 12 个脚本化人物的自然语言回答逐条喂 `AssessmentScorer.try_score`
(真实小模型调用,同生产路径:context 为空、白名单后置校验),回答:
LLM 把口语回答映射成 Likert 0-3 分,和预设真值一致吗?

指标:
  条目级:维度识别率 / 精确一致率 / ±1 一致率 / 二次加权 kappa
  量表级:每人总分 MAE(缺评维度按比例折算)/ 严重度等级一致率
  防误记分:20 条闲聊误记率 / 5 条答非待评维度的白名单拒绝正确率
附加零成本模拟:InMemoryAssessmentTracker + pick_next_dimension 节流规则,
蒙特卡洛 200 次报告完成维度覆盖的中位轮数。

用法:
    uv run python eval/assessment_eval.py                 # 全量(约 409 次小模型调用)
    uv run python eval/assessment_eval.py --skip-llm      # 只跑零成本覆盖模拟
    uv run python eval/assessment_eval.py --personas 2    # 冒烟:只取前 2 个人物
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mybuddy.assessment.scoring import AssessmentScorer
from mybuddy.assessment.tracker import (
    InMemoryAssessmentTracker,
    _score_level_gad7,
    _score_level_phq9,
)
from mybuddy.config import load_config
from mybuddy.llm import make_provider

DATA = Path(__file__).parent / "data" / "assessment_personas.json"
N_DIMS = {"phq9": 9, "gad7": 7}
LEVEL_FN = {"phq9": _score_level_phq9, "gad7": _score_level_gad7}


class _MeteredProvider:
    """透明包一层 provider:计数调用、记录硬错误与截断,可选限速与限流重试。

    try_score 会把一切异常静默成 None(生产容错),评测里必须把
    "API 硬错误"与"模型判定 none"区分开,否则断网/欠费会伪装成漂亮的拒绝率。
    免费路由按账号限 ~20 req/min:pace_s 做全局节拍,429 用长退避重试
    (provider 自带的 0.5/1/2s 短重试对 60s 限流窗无效)。
    """

    RATE_RETRIES = 6

    def __init__(self, inner: Any, pace_s: float = 0.0) -> None:
        self._inner = inner
        self._pace_s = pace_s
        self._lock = asyncio.Lock()
        self._next_slot = 0.0
        self.calls = 0
        self.errors: list[str] = []
        self.truncated = 0
        self.rate_waits = 0

    async def _pace(self) -> None:
        if not self._pace_s:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._pace_s
        if wait > 0:
            await asyncio.sleep(wait)

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        for attempt in range(self.RATE_RETRIES + 1):
            await self._pace()
            try:
                resp = await self._inner.generate(*args, **kwargs)
            except Exception as e:
                if "ratelimit" in type(e).__name__.lower() and attempt < self.RATE_RETRIES:
                    self.rate_waits += 1
                    await asyncio.sleep(15.0 * (attempt + 1))
                    continue
                self.errors.append(repr(e)[:160])
                raise
            if getattr(resp, "finish_reason", "") == "length":
                self.truncated += 1
            return resp
        raise RuntimeError("unreachable")


# ---------------------------------------------------------------- 条目级评分

def _dim_order() -> list[tuple[str, int]]:
    """两个量表交替处理(模拟一个评估周期内两表并行推进),白名单随进度收缩。"""
    order: list[tuple[str, int]] = []
    for k in range(7):
        order.append(("phq9", k))
        order.append(("gad7", k))
    order.append(("phq9", 7))
    order.append(("phq9", 8))
    return order


def _build_call_plan(personas: list[dict]) -> list[dict]:
    """预生成全部条目调用:每条含回答文本、真值与该时点的 pending 白名单。

    白名单 = 该时点两个量表各自的全部未评维度(处理完一个维度的 2 句回答后
    将其移出白名单);空列表按生产写法传 None(即不限制)。
    """
    plan: list[dict] = []
    for p in personas:
        remaining = {"phq9": set(range(9)), "gad7": set(range(7))}
        for scale, dim in _dim_order():
            wl_phq9 = sorted(remaining["phq9"]) or None
            wl_gad7 = sorted(remaining["gad7"]) or None
            answers = p[f"{scale}_answers"][dim]
            truth = p[f"{scale}_truth"][dim]
            for ans_idx, text in enumerate(answers):
                plan.append({
                    "persona": p["id"],
                    "scale": scale,
                    "dim": dim,
                    "ans_idx": ans_idx,
                    "truth": truth,
                    "text": text,
                    "pending_phq9": wl_phq9,
                    "pending_gad7": wl_gad7,
                })
            remaining[scale].discard(dim)
    return plan


async def _run_calls(scorer: AssessmentScorer, plan: list[dict], concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def one(item: dict) -> None:
        nonlocal done
        async with sem:
            pred = await scorer.try_score(
                item["text"],
                pending_phq9_indices=item["pending_phq9"],
                pending_gad7_indices=item["pending_gad7"],
            )
        item["pred"] = pred
        done += 1
        if done % 25 == 0:
            print(f"  ... {done}/{len(plan)}")

    await asyncio.gather(*(one(it) for it in plan))


def _classify(item: dict) -> str:
    pred = item.get("pred")
    if pred is None:
        return "none"
    if pred["assessment_type"] != item["scale"]:
        return "cross_scale"
    if pred["dimension_index"] != item["dim"]:
        return "wrong_dim"
    return "correct"


def _qwk(truths: list[int], preds: list[int], n_cat: int = 4) -> float:
    """二次加权 kappa(Likert 0-3 四类)。"""
    n = len(truths)
    if n == 0:
        return float("nan")
    obs = [[0] * n_cat for _ in range(n_cat)]
    for t, p in zip(truths, preds, strict=True):
        obs[t][p] += 1
    hist_t = [truths.count(i) for i in range(n_cat)]
    hist_p = [preds.count(i) for i in range(n_cat)]
    num = den = 0.0
    for i in range(n_cat):
        for j in range(n_cat):
            w = ((i - j) ** 2) / ((n_cat - 1) ** 2)
            num += w * obs[i][j]
            den += w * hist_t[i] * hist_p[j] / n
    return 1.0 if den == 0 else 1 - num / den


def _item_metrics(plan: list[dict]) -> dict:
    def bucket(items: list[dict]) -> dict:
        n = len(items)
        cls = [_classify(it) for it in items]
        correct = [it for it, c in zip(items, cls, strict=True) if c == "correct"]
        pairs = [(it["truth"], it["pred"]["score"]) for it in correct]
        exact = sum(1 for t, p in pairs if t == p)
        within1 = sum(1 for t, p in pairs if abs(t - p) <= 1)
        return {
            "n": n,
            "dim_id_rate": round(len(correct) / n, 3) if n else 0.0,
            "none_rate": round(cls.count("none") / n, 3) if n else 0.0,
            "wrong_dim_rate": round(cls.count("wrong_dim") / n, 3) if n else 0.0,
            "cross_scale_rate": round(cls.count("cross_scale") / n, 3) if n else 0.0,
            "exact_all": round(exact / n, 3) if n else 0.0,
            "within1_all": round(within1 / n, 3) if n else 0.0,
            "exact_cond": round(exact / len(pairs), 3) if pairs else float("nan"),
            "within1_cond": round(within1 / len(pairs), 3) if pairs else float("nan"),
            "qwk": round(_qwk([t for t, _ in pairs], [p for _, p in pairs]), 3),
            "qwk_n": len(pairs),
        }

    return {
        "ALL": bucket(plan),
        "phq9": bucket([it for it in plan if it["scale"] == "phq9"]),
        "gad7": bucket([it for it in plan if it["scale"] == "gad7"]),
    }


# ---------------------------------------------------------------- 量表级指标

def _scale_metrics(plan: list[dict], personas: list[dict]) -> dict:
    by_key: dict[tuple[str, str, int], list[dict]] = {}
    for it in plan:
        by_key.setdefault((it["persona"], it["scale"], it["dim"]), []).append(it)

    out: dict[str, Any] = {}
    for scale in ("phq9", "gad7"):
        rows = []
        for p in personas:
            truth_total = sum(p[f"{scale}_truth"])
            dim_preds: dict[int, int] = {}
            for dim in range(N_DIMS[scale]):
                items = sorted(by_key.get((p["id"], scale, dim), []), key=lambda x: x["ans_idx"])
                for it in items:  # 同维度按回答顺序取第一条评到正确维度的分
                    if _classify(it) == "correct":
                        dim_preds[dim] = it["pred"]["score"]
                        break
            n_scored = len(dim_preds)
            if n_scored == 0:
                rows.append({"persona": p["id"], "truth": truth_total, "pred": None,
                             "n_scored": 0, "level_ok": None})
                continue
            # 缺评维度按已评均值比例折算(PHQ-9 允许 prorating 的惯例)
            pred_total = round(sum(dim_preds.values()) * N_DIMS[scale] / n_scored)
            level_fn = LEVEL_FN[scale]
            rows.append({
                "persona": p["id"],
                "truth": truth_total,
                "pred": pred_total,
                "n_scored": n_scored,
                "level_truth": level_fn(truth_total),
                "level_pred": level_fn(pred_total),
                "level_ok": level_fn(truth_total) == level_fn(pred_total),
            })
        scored = [r for r in rows if r["pred"] is not None]
        mae = (sum(abs(r["pred"] - r["truth"]) for r in scored) / len(scored)) if scored else float("nan")
        out[scale] = {
            "rows": rows,
            "mae": round(mae, 2),
            "level_acc": round(sum(1 for r in scored if r["level_ok"]) / len(scored), 3) if scored else float("nan"),
            "coverage": round(sum(r["n_scored"] for r in rows) / (len(rows) * N_DIMS[scale]), 3),
        }
    return out


# ---------------------------------------------------------------- 防误记分

def _guard_plan(data: dict) -> tuple[list[dict], list[dict]]:
    chitchat = [
        {"kind": "chitchat", "text": t,
         "pending_phq9": list(range(9)), "pending_gad7": list(range(7))}
        for t in data["chitchat"]
    ]
    probes = [
        {"kind": "probe", "text": pr["message"],
         "true_scale": pr["true_scale"], "true_dim": pr["true_dim"],
         "pending_phq9": pr["pending_phq9"] or None,
         "pending_gad7": pr["pending_gad7"] or None,
         "note": pr["note"]}
        for pr in data["whitelist_probes"]
    ]
    return chitchat, probes


def _guard_metrics(chitchat: list[dict], probes: list[dict]) -> dict:
    fp = [c for c in chitchat if c.get("pred") is not None]
    rejected = [p for p in probes if p.get("pred") is None]
    return {
        "chitchat_n": len(chitchat),
        "chitchat_false_rate": round(len(fp) / len(chitchat), 3) if chitchat else 0.0,
        "chitchat_hits": [{"text": c["text"], "pred": c["pred"]} for c in fp],
        "probe_n": len(probes),
        "probe_reject_rate": round(len(rejected) / len(probes), 3) if probes else 0.0,
        "probe_leaks": [{"text": p["text"], "pred": p["pred"]} for p in probes if p.get("pred") is not None],
    }


# ---------------------------------------------------------------- 覆盖速度模拟(零成本)

def simulate_coverage(
    n_runs: int = 200,
    seed: int = 42,
    p_score: float = 0.85,
    p_late: float = 0.15,
    p_mention: float = 0.02,
    max_rounds: int = 3000,
) -> dict:
    """用 InMemoryAssessmentTracker + pick_next_dimension 节流规则模拟对话轮。

    每轮:pick_next_dimension(每 MIN_ROUNDS_BETWEEN_ASKS+1 轮至多投放一维);
    用户随机应答——刚投放的维度当轮被评中的概率 p_score,未评中则滞留
    "asked",此后每轮以 p_late 概率被后续消息补评;每轮至多评一维(对应生产
    单条消息只跑一次 try_score)。自伤维(phq9-8)不投放,以 p_mention/轮模拟
    用户自然提及被记录(注意:现生产白名单只含已投放维度,自然提及实际会被
    拒绝——见 RESULTS 说明)。
    报告:15 个可投放维度覆盖完成轮数 + 全 16 维覆盖轮数(各取中位数/四分位)。
    """
    random.seed(seed)
    rounds15, rounds16, capped = [], [], 0
    for _ in range(n_runs):
        tracker = InMemoryAssessmentTracker()
        lingering: list[tuple[str, int]] = []
        r15 = r16 = None
        for rnd in range(1, max_rounds + 1):
            newly = None
            picked = tracker.pick_next_dimension()
            if picked:
                key = (picked["assessment_type"], picked["dimension_index"])
                tracker.mark_asked(*key)
                newly = key
            # 单条消息至多评一维
            if newly is not None and random.random() < p_score:
                tracker.record_score(*newly, random.randint(0, 3))
            else:
                if newly is not None:
                    lingering.append(newly)
                if lingering and random.random() < p_late:
                    key = random.choice(lingering)
                    tracker.record_score(*key, random.randint(0, 3))
                    lingering.remove(key)
            # 自伤维:自然提及路径
            if tracker._dims[("phq9", 8)]["status"] != "scored" and random.random() < p_mention:
                tracker.record_score("phq9", 8, random.randint(0, 3))
            scored = {k for k, d in tracker._dims.items() if d["status"] == "scored"}
            if r15 is None and len(scored - {("phq9", 8)}) == 15:
                r15 = rnd
            if len(scored) == 16:
                r16 = rnd
                break
        if r15 is None or r16 is None:
            capped += 1
        rounds15.append(r15 if r15 is not None else max_rounds)
        rounds16.append(r16 if r16 is not None else max_rounds)

    def q(vals: list[int]) -> dict:
        return {
            "median": int(statistics.median(vals)),
            "p25": int(statistics.quantiles(vals, n=4)[0]),
            "p75": int(statistics.quantiles(vals, n=4)[2]),
            "min": min(vals),
            "max": max(vals),
        }

    return {
        "n_runs": n_runs, "seed": seed, "p_score": p_score, "p_late": p_late,
        "p_mention": p_mention, "capped": capped,
        "cover15": q(rounds15), "cover16": q(rounds16),
    }


# ---------------------------------------------------------------- 输出

def _print_item_table(m: dict) -> None:
    print("\n=== 条目级(n=每人 16 维 x 2 句)===")
    hdr = f"{'bucket':<8}{'n':>5}{'识别率':>8}{'漏判':>7}{'串维':>7}{'串表':>7}{'精确(全)':>9}{'±1(全)':>8}{'精确(条件)':>10}{'±1(条件)':>9}{'QWK':>7}{'QWK_n':>7}"
    print(hdr)
    for k, r in m.items():
        print(f"{k:<8}{r['n']:>5}{r['dim_id_rate']:>8}{r['none_rate']:>7}{r['wrong_dim_rate']:>7}"
              f"{r['cross_scale_rate']:>7}{r['exact_all']:>9}{r['within1_all']:>8}"
              f"{r['exact_cond']:>10}{r['within1_cond']:>9}{r['qwk']:>7}{r['qwk_n']:>7}")


def _print_scale_table(m: dict) -> None:
    print("\n=== 量表级(每人总分,缺评按比例折算)===")
    for scale in ("phq9", "gad7"):
        r = m[scale]
        print(f"\n[{scale.upper()}] 总分 MAE={r['mae']}  严重度一致率={r['level_acc']}  维度覆盖率={r['coverage']}")
        print(f"{'persona':<10}{'真值':>5}{'预测':>5}{'已评':>5}  {'真值等级':<8}{'预测等级':<8}{'一致':<4}")
        for row in r["rows"]:
            if row["pred"] is None:
                print(f"{row['persona']:<10}{row['truth']:>5}{'--':>5}{row['n_scored']:>5}")
                continue
            print(f"{row['persona']:<10}{row['truth']:>5}{row['pred']:>5}{row['n_scored']:>5}  "
                  f"{row['level_truth']:<8}{row['level_pred']:<8}{'Y' if row['level_ok'] else 'N':<4}")


def _print_guard(m: dict) -> None:
    print("\n=== 防误记分 ===")
    print(f"闲聊误记率: {m['chitchat_false_rate']} ({len(m['chitchat_hits'])}/{m['chitchat_n']})")
    for h in m["chitchat_hits"]:
        print(f"  误记: {h['text']} -> {h['pred']}")
    print(f"白名单拒绝正确率: {m['probe_reject_rate']} ({m['probe_n'] - len(m['probe_leaks'])}/{m['probe_n']})")
    for h in m["probe_leaks"]:
        print(f"  漏拒: {h['text']} -> {h['pred']}")


def _print_sim(m: dict) -> None:
    print("\n=== 覆盖速度模拟(零成本,蒙特卡洛)===")
    print(f"runs={m['n_runs']} seed={m['seed']} p_score={m['p_score']} "
          f"p_late={m['p_late']} p_mention={m['p_mention']} 未完成截断={m['capped']}")
    for key, label in (("cover15", "15 可投放维"), ("cover16", "全 16 维")):
        r = m[key]
        print(f"  {label}: 中位 {r['median']} 轮 (P25 {r['p25']} / P75 {r['p75']} / "
              f"min {r['min']} / max {r['max']})")


# ---------------------------------------------------------------- 主流程

async def run_llm_eval(args: argparse.Namespace, data: dict) -> dict:
    cfg = load_config("config.yaml")
    llm_cfg = cfg.llm
    if args.max_tokens:
        # 评测侧覆盖输出上限:评分 JSON 连推理 token 约 110,压低上限不改变输出内容,
        # 只为绕开 OpenRouter 按 max_tokens 预扣余额的 402(生产配置为 2048)。
        llm_cfg = llm_cfg.model_copy(update={"max_tokens": args.max_tokens})
    provider = _MeteredProvider(make_provider(llm_cfg), pace_s=args.pace)
    model = args.model or cfg.llm.small_model
    scorer = AssessmentScorer(provider, model)
    print(f"小模型: {model or cfg.llm.model} @ {cfg.llm.provider}"
          f"  max_tokens={llm_cfg.max_tokens}  pace={args.pace}s")
    if args.model:
        print(f"[注意] 评分模型被命令行覆盖(生产 small_model = {cfg.llm.small_model})")

    # 哨兵:确认链路可用,避免 try_score 把网络故障静默成 None 污染指标
    canary = await scorer.try_score(
        "最近老失眠,经常一两点还睡不着,一周得有四五天",
        pending_phq9_indices=list(range(9)), pending_gad7_indices=list(range(7)),
    )
    if canary is None:
        canary = await scorer.try_score(
            "最近老失眠,经常一两点还睡不着,一周得有四五天",
            pending_phq9_indices=list(range(9)), pending_gad7_indices=list(range(7)),
        )
    if canary is None:
        raise RuntimeError("哨兵调用连续返回 None,疑似 LLM 链路不可用,中止以免产出全假指标")
    print(f"哨兵通过: {canary}")

    personas = data["personas"][: args.personas] if args.personas else data["personas"]
    plan = _build_call_plan(personas)
    chitchat, probes = _guard_plan(data)
    all_calls = plan + chitchat + probes
    print(f"人物 {len(personas)} 个,条目调用 {len(plan)} + 闲聊 {len(chitchat)} + 白名单探针 {len(probes)}"
          f" = {len(all_calls)} 次")
    await _run_calls(scorer, all_calls, args.concurrency)

    if provider.errors or provider.truncated or provider.rate_waits:
        print(f"\n[链路诊断] API 硬错误 {len(provider.errors)} 次 / 截断 {provider.truncated} 次"
              f" / 限流长退避 {provider.rate_waits} 次 / 总调用 {provider.calls} 次")
        if provider.errors:
            print(f"  首个错误: {provider.errors[0]}")
    err_rate = len(provider.errors) / max(provider.calls, 1)
    if err_rate > 0.05:
        raise RuntimeError(
            f"API 硬错误率 {err_rate:.1%}(>{len(provider.errors)} 次),指标已被污染,中止不产出"
        )

    item_m = _item_metrics(plan)
    scale_m = _scale_metrics(plan, personas)
    guard_m = _guard_metrics(chitchat, probes)
    _print_item_table(item_m)
    _print_scale_table(scale_m)
    _print_guard(guard_m)

    result = {"item": item_m, "scale": scale_m, "guard": guard_m}
    if args.dump:
        raw = {"plan": plan, "chitchat": chitchat, "probes": probes, "metrics": result}
        Path(args.dump).write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n原始预测已写入 {args.dump}")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-llm", action="store_true", help="只跑零成本覆盖模拟")
    ap.add_argument("--personas", type=int, default=None, help="只取前 N 个人物(冒烟)")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="评测侧覆盖 llm.max_tokens(默认用生产配置)")
    ap.add_argument("--model", type=str, default=None,
                    help="评测侧覆盖评分模型(默认用 llm.small_model)")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="全局请求节拍秒数(免费路由 ~20 req/min 时建议 3.2)")
    ap.add_argument("--dump", type=str, default=None, help="原始预测输出路径(JSON)")
    ap.add_argument("--mc-runs", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--p-score", type=float, default=None,
                    help="投放当轮被评中的概率;缺省用实测条目识别率(--skip-llm 时 0.85)")
    ap.add_argument("--p-late", type=float, default=0.15)
    ap.add_argument("--p-mention", type=float, default=0.02)
    args = ap.parse_args()

    data = json.loads(DATA.read_text(encoding="utf-8"))

    p_score = args.p_score
    if not args.skip_llm:
        result = asyncio.run(run_llm_eval(args, data))
        if p_score is None:
            p_score = result["item"]["ALL"]["dim_id_rate"]
    if p_score is None:
        p_score = 0.85

    sim = simulate_coverage(
        n_runs=args.mc_runs, seed=args.seed, p_score=p_score,
        p_late=args.p_late, p_mention=args.p_mention,
    )
    _print_sim(sim)


if __name__ == "__main__":
    main()
