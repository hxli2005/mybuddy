# 桥协议 v2(壳 ↔ 引擎合同)

> `bridge: "vpet-bridge/2"`。壳只认 2。双服务路径(FastAPI + web.py)同步实现,token/`X-MyBuddy-Client-Flags` 机制沿用 phase-2 不变。
> 方向反转说明:phase-2 的 body_state **上行**在 v2 废弃(生理真相源在引擎);状态改为**下行**。
> 状态:`FINAL`(2026-07-11);字段名、事件枚举与计时归属自此冻结。

## 1. 新增 GET /api/vpet/state(下行状态通道)

壳默认 20s 轮询 + chat/event 响应后立即刷一次。**不写遥测**(太噪),仅 server_flags 变化时记一条 `event="flags_changed"`。

```jsonc
{
  "ok": true, "bridge": "vpet-bridge/2",
  "server_time": "2026-07-11T14:00:00+08:00",
  "time_offset_minutes": 0, // 仅 acceptance_mode=true 时返回非零;壳不得自行叠加偏移
  "physio": {"hunger": 62, "energy": 41, "mood": 58,
              "sleeping": false, "woken": false,
              "levels": {"hungry": false, "tired": false, "low": false, "bright": false}},
  "idle_hint": "read",        // read|write|nap|gaze|stretch|work|sleep;由 living_state 活动 + 共处 + 作息派生,sleep 最高优先
  "warmth": 0.6,              // 0–1,关系行为参数;v1 = 近 7 天日均互动次数的 EMA 映射,壳映射到 idle 距离/表情基线
  "server_flags": {...},      // 同 phase-2 三开关快照(键名见 EXPERIMENT §2)
  "day_index": 3
}
```

`server_time` 是壳的唯一业务时钟。时钟模拟通过启动引擎前设置
`MYBUDDY_TIME_OFFSET_MINUTES` 完成;改变偏移必须重启引擎。壳设置页只显示当前偏移和重启提示,
不保存、不修改第二套模拟时钟。生产配置 `vpet.acceptance_mode=false` 时偏移必须为 0。

## 2. POST /api/vpet/chat(改)

- 请求:`{"message", "event"}`;**`body_state` 字段 deprecated**——仍接受但忽略,首次收到记一条 warning 日志,不再进 prompt(处置表 §Python-3)。
- 响应新增 `speech.truncated`:`text` 保留全文(壳的聊天面板显示);`speech.text` 服务端裁到 **≤2 句**(复用 `_short_vpet_reaction` 的句读逻辑,limit 放宽为两句),超裁时 `truncated: true`,壳气泡显示 speech.text 并提示"完整的在聊天窗"。
- 其余(action/expression/pending/turn_id/遥测 meta)不变。

## 3. POST /api/vpet/event(改)

- `event` 最终枚举:`touch_head | touch_body | feed | user_back | work_start | work_stop | presence_heartbeat | notice_shown`。
- 引擎在落库前统一向所有 `context_json` 追加只读字段
  `local_date/server_time/client_event_id`;若客户端传同名字段,以服务端值覆盖。所有按日 SQL 只读 `local_date`,不自行猜 UTC 偏移。
- `feed`:`context.item` 必须为食物目录 id(§5);引擎调 `PhysioEngine.apply_feed` 并把 `physio_delta` 记入遥测 context;投喂本身进 shared_moment 候选(§6)。
- `work_start/work_stop`(共处模式):
  - `work_start.context.session_id` 由壳生成 UUID;引擎落事件并用现有 APScheduler 创建持久 job `vpet_cowork:{session_id}`,触发时间 = start+50min。
  - job 触发时重查 `vpet_events`:仅会话仍未 stop 才 enqueue `source="cowork_break"`;重启时从未闭合的 start 恢复缺失 job。
  - `work_stop.context.session_id` 必填;引擎取消 job,按服务端时间计算 `duration_minutes` 写入 stop 的 context;未知/重复 stop 幂等返回 200。
  - start 后 `idle_hint=work`;stop 响应带一句收尾语(轻语义、`enable_tools=False`、不占触摸预算,日上限 4 次)。壳不得本地生成提醒或收尾台词。
- `presence_heartbeat`:壳仅在用户在场时每 20 分钟上报一次,`count` 为距上次成功心跳覆盖的在场分钟数(1–20),context 固定为
  `{"local_date":"YYYY-MM-DD","idle_seconds":0..,"fullscreen":bool,"work_session_id":string|null}`。引擎只落遥测,不调用 LLM,返回 `replied=false`;离场、睡眠或全屏不等同于在场,不得补造心跳。
- `notice_shown`:壳在气泡或持久卡**实际进入可见状态后**上报,context 固定为
  `{"pending_id":int|null,"source":string,"interrupt":bool,"persistent":bool,"shown_at":ISO8601}`。被全屏/今天安静/睡眠门控拦下的内容不得上报。该事件是打扰率和夜间零打扰的展示真相源。
  引擎保留来值为 `client_shown_at`,并用收到请求时的服务端时钟覆盖规范 `shown_at`,避免 20s state 轮询陈旧值跨越睡眠窗边界。
- `touch_*` 升格批准链、预算、`want_reply` 语义:**沿用 phase-2 不变**。
- `body_state` 字段同 §2 deprecated。

## 4. drain / digest / overdue

phase-2 语义原封不动(`digest=true`、overdue 持久卡、greeting 超窗丢弃、遥测三事件)。新增:`source="body_murmur"`(PHYSIO §7)与 `source="cowork_break"` 按普通 pending 走,digest 时 murmur 可丢弃、cowork_break 不合并不打断(它有时效)。

所有 pending/drain 响应附带当前 `server_flags`;壳在展示前以它刷新物理主动镜像，避免跨 00:00 的轮询间隙沿用前一天实验条件。

drain 表示“内容已交给壳”,不等于“用户看见”。壳必须在真正显示后另发 `notice_shown`;
所有展示率、打扰率和夜间零打扰只以 `notice_shown` 计数。

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
- event:八种 kind;feed 目录 Δ 与未知 id 兜底;work_start→持久 job→50min 提醒;stop 取消与幂等;重启恢复未闭合会话。
- audit:heartbeat 只在场上报且分钟数 1–20;门控内容无 `notice_shown`,真正显示恰有一条 ack。
- clock:`server_time` 含时区;offset 仅随引擎启动环境生效;壳不叠加偏移;生产模式拒绝非零偏移。
- 记忆:feed 日聚合(同日两次=一条);touch 日聚合阈值。
- 兼容:O1 形状请求(带 body_state)全部 200。

## 9. 错误与幂等合同

- 所有 event 请求支持 `client_event_id`;重复 id 返回首次结果且不重复改生理、不重复排 job、不重复写共同记忆。
- `presence_heartbeat`、`notice_shown` 同样要求唯一 `client_event_id`;网络重试不得放大实验计数。
- 业务校验失败返回 HTTP 400 + `{"ok":false,"error":{"code","message"}}`;鉴权失败 401;引擎忙只用于需要 LLM 的升格请求并仍返回 200 + `gate_reason="agent_busy"`。
- 壳对 chat/event 使用 15s 超时;heartbeat/state 5s;只重试幂等 event,指数退避最多 24h。chat 不自动重放,避免重复对话。
