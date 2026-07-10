# 桥协议 v2(壳 ↔ 引擎合同)

> `bridge: "vpet-bridge/2"`。壳只认 2。双服务路径(FastAPI + web.py)同步实现,token/`X-MyBuddy-Client-Flags` 机制沿用 phase-2 不变。
> 方向反转说明:phase-2 的 body_state **上行**在 v2 废弃(生理真相源在引擎);状态改为**下行**。

## 1. 新增 GET /api/vpet/state(下行状态通道)

壳默认 20s 轮询 + chat/event 响应后立即刷一次。**不写遥测**(太噪),仅 server_flags 变化时记一条 `event="flags_changed"`。

```jsonc
{
  "ok": true, "bridge": "vpet-bridge/2",
  "physio": {"hunger": 62, "energy": 41, "mood": 58,
              "sleeping": false, "woken": false,
              "levels": {"hungry": false, "tired": false, "low": false, "bright": false}},
  "idle_hint": "read",        // read|write|nap|gaze|stretch|work|sleep;由 living_state 活动 + 共处 + 作息派生,sleep 最高优先
  "warmth": 0.6,              // 0–1,关系行为参数;v1 = 近 7 天日均互动次数的 EMA 映射,壳映射到 idle 距离/表情基线
  "server_flags": {...},      // 同 phase-2 三开关快照(键名见 EXPERIMENT §2)
  "day_index": 3
}
```

## 2. POST /api/vpet/chat(改)

- 请求:`{"message", "event"}`;**`body_state` 字段 deprecated**——仍接受但忽略,首次收到记一条 warning 日志,不再进 prompt(处置表 §Python-3)。
- 响应新增 `speech.truncated`:`text` 保留全文(壳的聊天面板显示);`speech.text` 服务端裁到 **≤2 句**(复用 `_short_vpet_reaction` 的句读逻辑,limit 放宽为两句),超裁时 `truncated: true`,壳气泡显示 speech.text 并提示"完整的在聊天窗"。
- 其余(action/expression/pending/turn_id/遥测 meta)不变。

## 3. POST /api/vpet/event(改)

- `event` 枚举扩为:`touch_head | touch_body | feed | user_back | work_start | work_stop`。
- `feed`:`context.item` 必须为食物目录 id(§5);引擎调 `PhysioEngine.apply_feed` 并把 `physio_delta` 记入遥测 context;投喂本身进 shared_moment 候选(§6)。
- `work_start/work_stop`(共处模式):引擎记会话(新表不建,vpet_events 足够:`event=work_start/stop` + duration 在 stop 的 context 里);`work_start` 后 `idle_hint=work`;引擎在会话 50 分钟时向 pending 队列 enqueue 一条 `source="cowork_break"` 久坐提醒;`work_stop` 响应直接带一句收尾语(走升格同款轻语义:`enable_tools=False`,不占触摸预算,日上限 4 次)。
- `touch_*` 升格批准链、预算、`want_reply` 语义:**沿用 phase-2 不变**。
- `body_state` 字段同 §2 deprecated。

## 4. drain / digest / overdue

phase-2 语义原封不动(`digest=true`、overdue 持久卡、greeting 超窗丢弃、遥测三事件)。新增:`source="body_murmur"`(PHYSIO §7)与 `source="cowork_break"` 按普通 pending 走,digest 时 murmur 可丢弃、cowork_break 不合并不打断(它有时效)。

## 5. 食物目录(v1 固定五样,壳硬编码同一份)

| id | 名称 | hunger | mood |
|---|---|---|---|
| congee | 一碗粥 | +20 | +2 |
| curry | 咖喱饭 | +35 | +4 |
| milk_tea | 奶茶 | +10 | +6 |
| coffee | 咖啡 | +5 | +8 |
| water | 水 | +3 | +1 |

未知 id 按 water 落地并记 warning。**无价格、无库存、无解锁**(宪法 5 + 反形态)。

## 6. 身体事件 → 共同记忆(拍 4 的后半)

- feed 时引擎直写一条 shared_moment:`ltm.add(内容="你请我吃了{名称}", mem_type="shared_moment", extra_meta={source:"vpet_feed", item, date})`,**日聚合**:同日多次投喂合并更新为一条("今天你请我吃了粥和奶茶")。
- touch 不逐次入记忆;日终(当日首次跨天交互时惰性触发)若 touch 累计 ≥5,写一条日聚合 shared_moment("今天被你 rua 了好多下")。
- 回流不需要新机制:走现有检索(`ltm.search` 已在 Agent 记忆管线里)。验收见 ACCEPTANCE 拍 4。

## 7. 兼容与版本

- O1 插件(vpet-plugin/,冻结探针)只认 `/1` 字段集——v2 全部为**新增或放宽**,`/api/vpet/chat`、`/api/vpet/event`、drain 对旧请求保持可用(body_state 忽略即兼容),O1 探针在 v1 期内仍可用于后端 debug。
- `vpet_status_payload` 的 `bridge` 字段升 `"vpet-bridge/2"`,并新增 `"protocol": {"state": true, "cowork": true}` 能力声明。

## 8. 测试清单

- state:snapshot 字段齐;sleeping 窗内派生;idle_hint 优先级(sleep>work>living_state)。
- chat:body_state 忽略且仅一次 warning;speech 两句裁剪 + truncated 标记。
- event:五种新旧 kind;feed 目录 Δ 与未知 id 兜底;work_start→idle_hint=work;50min 提醒入队;work_stop 收尾语不占触摸预算。
- 记忆:feed 日聚合(同日两次=一条);touch 日聚合阈值。
- 兼容:O1 形状请求(带 body_state)全部 200。
