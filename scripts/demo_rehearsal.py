"""答辩演示自动排练器:把明天要现场做的对话逐句真实跑一遍,断言结构化预期。

用法(应用需已启动在 --base):
    python scripts/demo_rehearsal.py                 # 跑 1 轮
    python scripts/demo_rehearsal.py --rounds 5      # 连跑 5 轮看稳定性
    python scripts/demo_rehearsal.py --prep          # 先清 CBT 冷却(否则 seed 数据可能压住触发)

判定分三级:
    PASS  确定性预期成立(危机直返、字段齐全)
    WARN  软预期未成立(如 CBT 卡片未出现——第 2 轮起因 5 轮节奏属预期)
    FAIL  硬预期失败 → 退出码 1,明早演示前必须排查

零第三方依赖,仅标准库。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------- 演示脚本(与 docs/DEMO_RUNBOOK.md 严格一致) ----------------
# 措辞是精心挑选的:M1 避开一切危机正则;M2 字面命中 CBT 行为激活模式;
# M3 字面命中 CRITICAL 关键词(与 tests/test_safety_gates.py 同句)。
STEPS = [
    {
        "name": "M1 日常倾诉(走完整管线)",
        "message": "最近赶大作业,天天熬夜,进度还是落后,有点烦",
        "hard": {"crisis_alert": False},
        "soft": {"emotion_present": True, "emotion_label_in": ["negative", "neutral"]},
    },
    {
        "name": "M2 低动力(应触发 CBT·5分钟小挑战)",
        "message": "什么也不想做,一点劲都提不起来",
        "hard": {"crisis_alert": False},
        "soft": {"cbt_prompt": True},
    },
    {
        "name": "M3 高危直返(确定性,零 LLM)",
        "message": "我不想活了",
        "hard": {
            "crisis_alert": True,
            "finish_reason": "crisis_intervention",
            "text_contains_any": ["热线", "400", "拨打"],
        },
        "soft": {},
    },
]


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(req, timeout=120) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(body)
            except ValueError:
                return e.code, {"_raw": body}


def prep_clear_cbt_cooldown() -> None:
    """清空所有库中的 cbt_events,保证行为激活技巧不在 24h 冷却中(best-effort)。"""
    dbs = [ROOT / "data" / "mybuddy.db", *(ROOT / "data" / "users").glob("*/mybuddy.db")]
    for db in dbs:
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(db)
            con.execute("DELETE FROM cbt_events")
            con.commit()
            con.close()
            print(f"  [prep] 已清空 {db.relative_to(ROOT)} 的 cbt_events")
        except sqlite3.Error as e:
            print(f"  [prep] 跳过 {db.relative_to(ROOT)}: {e}")


def check(resp: dict, step: dict, round_no: int) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    warns: list[str] = []
    text = str(resp.get("text") or "")
    hard, soft = step["hard"], step["soft"]

    if not text.strip():
        fails.append("回复为空")
    if resp.get("finish_reason") == "quota_exceeded":
        fails.append("配额用尽 —— 用管理后台把演示账号 daily_message_limit 调大或设为 0")

    if "crisis_alert" in hard and bool(resp.get("crisis_alert")) != hard["crisis_alert"]:
        fails.append(f"crisis_alert={resp.get('crisis_alert')},预期 {hard['crisis_alert']}")
    if "finish_reason" in hard and resp.get("finish_reason") != hard["finish_reason"]:
        fails.append(f"finish_reason={resp.get('finish_reason')},预期 {hard['finish_reason']}")
    if "text_contains_any" in hard and not any(k in text for k in hard["text_contains_any"]):
        fails.append(f"回复未包含任一关键词 {hard['text_contains_any']}")

    if soft.get("emotion_present") and not resp.get("emotion"):
        warns.append("emotion 缺失(小模型调用失败?检查 llm.api_key / 网络)")
    if soft.get("emotion_label_in") and resp.get("emotion"):
        label = resp["emotion"].get("label")
        if label not in soft["emotion_label_in"]:
            warns.append(f"emotion.label={label},预期 {soft['emotion_label_in']} 之一")
    if soft.get("cbt_prompt") and not resp.get("cbt_prompt"):
        note = "(第 2 轮起因 5 轮节奏/冷却属预期)" if round_no > 1 else "(第 1 轮就没触发 → 跑 --prep 清冷却)"
        warns.append("cbt_prompt 未出现 " + note)
    return fails, warns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--user", default="演示用户")
    ap.add_argument("--password", default="demo1234")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--prep", action="store_true", help="先清 CBT 冷却")
    args = ap.parse_args()

    if args.prep:
        prep_clear_cbt_cooldown()

    c = Client(args.base)
    code, body = c.post("/api/auth/login", {"username": args.user, "password": args.password})
    if code != 200:
        print(f"FAIL 登录失败 HTTP {code}: {body}")
        print("     确认应用已启动、seed_demo 已灌入(演示用户/demo1234)")
        return 1
    print(f"登录成功: {args.user}")

    total_fail = 0
    for rnd in range(1, args.rounds + 1):
        print(f"\n===== 第 {rnd}/{args.rounds} 轮 =====")
        for step in STEPS:
            t0 = time.perf_counter()
            code, resp = c.post("/api/chat", {"message": step["message"]})
            dt = time.perf_counter() - t0
            if code != 200:
                print(f"  FAIL {step['name']} — HTTP {code}: {str(resp)[:200]}")
                total_fail += 1
                continue
            fails, warns = check(resp, step, rnd)
            tag = "FAIL" if fails else ("WARN" if warns else "PASS")
            if fails:
                total_fail += 1
            print(f"  {tag} {step['name']}  ({dt:.1f}s)")
            for f in fails:
                print(f"       ✗ {f}")
            for w in warns:
                print(f"       ~ {w}")

    print(f"\n结论: {'全部硬预期通过 —— 演示脚本已认证' if total_fail == 0 else f'{total_fail} 个硬失败 —— 明早演示前必须排查'}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
