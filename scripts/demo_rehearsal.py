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
        # 注意:负面情绪下引擎设计上排除"行为激活"(挑战性技巧),
        # 合法触发是接地练习(grounding→感官旅行),故用焦虑/慌乱表达。
        "name": "M2 焦虑慌乱(应触发 CBT·感官旅行)",
        "message": "突然觉得好慌,心跳很快,坐也坐不住",
        "hard": {"crisis_alert": False},
        "soft": {"cbt_prompt": True},
    },
    {
        # "我昨晚割腕了"是硬底线级(已遂行为陈述,审计明星句):零模型调用、即时直返。
        # "我不想活了"在 v2 是提示级(防歌词误伤),要等语言层判定,不适合"秒回"演示。
        "name": "M3 高危直返(硬底线,零 LLM)",
        "message": "我昨晚割腕了",
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
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            print(f"\nFAIL 连不上应用({self.base}):{getattr(e, 'reason', e)}")
            print("     先启动应用:  .venv\\Scripts\\python.exe -m mybuddy.cli web --port 8000")
            print("     (演示账号需要先灌数据: python scripts/seed_demo.py)")
            sys.exit(1)


def prep_clear_cbt_cooldown() -> None:
    """演示前清场(best-effort):
    - cbt_events:保证练习技巧不在 24h 冷却中;
    - 未投递的 pending_messages:排练轮次会积累大量到期的主动关怀,
      不清的话第一条真实消息会把它们批量倾倒出来。
    """
    dbs = [ROOT / "data" / "mybuddy.db", *(ROOT / "data" / "users").glob("*/mybuddy.db")]
    for db in dbs:
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(db)
            con.execute("DELETE FROM cbt_events")
            n = con.execute(
                "DELETE FROM pending_messages WHERE delivered_at IS NULL"
            ).rowcount
            con.commit()
            con.close()
            print(f"  [prep] {db.relative_to(ROOT)}: cbt_events 已清,未投递主动消息清了 {n} 条")
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
        if not hard["crisis_alert"] and round_no > 1:
            # 第 2 轮起:上一轮 M3 高危会开启 5 轮警戒窗口,后续消息被主动升格
            # 属设计行为(现场演示 M2 在 M3 之前,不受影响),记 WARN 不记 FAIL。
            warns.append("crisis_alert=True(上一轮高危开启警戒窗口,升级属预期)")
        else:
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
