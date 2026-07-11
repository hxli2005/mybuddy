# 六拍验收规格(PM 轮批准条件 #7:验收客观化)

> 完工定义:每拍证据包齐 → Codex 审计签"证据完备"(不是作者签"我觉得发生了")→ 周检三项一轮。
> 证据包目录:`eval/acceptance/v1/beat-N/`,内含:操作录屏或截图序列 + SQL 输出粘贴 + 壳日志片段。
> 状态:`FINAL`(2026-07-11);触发、判定、证据格式和降级标记均已冻结。
> 钟点拍只通过引擎启动变量 `MYBUDDY_TIME_OFFSET_MINUTES` 模拟;壳读取 `server_time`,不另设偏移。拍 5、6 各留一次**真机真时**复验。

每拍目录固定包含 `steps.md`、`screen.mp4`(或有时间戳的截图序列)、`events.sql.txt`、
`shell.log.txt`、`result.json`。`result.json` 字段固定为
`beat/status(PASS|FAIL|DEFERRED)/commit/config_hash/tested_at/codex_evidence/user_experience/deviation`;
缺任一强制证据不得标 PASS。共同记忆等非 SQLite 证据由统一只读命令
`uv run python scripts/vpet_acceptance_evidence.py ...` 导出到同目录,禁止手工改数据库截图充证据。

## 拍 1|回场问候(带记忆)

- 触发:引擎在跑,壳 idle 暂停态(模拟 idle>30min),恢复输入。
- 预期:壳先 POST user_back → drain;若有积压,气泡一句 digest;问候语引用近期真实话题(living_state recent_self_event)。
- SQL:`SELECT * FROM vpet_events WHERE event='user_back' ORDER BY id DESC LIMIT 1;` + drain 遥测(`pending_drained/pending_digested`) + 实际气泡对应的 `notice_shown`。
- 证据:录屏(离开→回来→气泡)+ SQL 行。

## 拍 2|角落做自己的事(idle 表达)

- 触发:无交互 ≥3 分钟,引擎 living_state 活动非空。
- 预期:`GET /api/vpet/state` 返回 idle_hint ∈ {read,write,gaze,...};壳播对应闲时动画;**全程无气泡**(宪法 3)。
- SQL:确认该 3 分钟窗口不存在 `notice_shown`;另存 state 响应 JSON + 动画录屏 10s。

## 拍 3|摸头反射 + 首次升格

- 触发:当天第一次摸头。
- 预期:反射动画 <100ms(录屏帧判);随后气泡一句 ≤28 字,不反问不建议;第二次摸头只有动画(预算/启发式生效)。
- SQL:按 `context_json.local_date` 查询当日 `touch_head`;首行 `escalated=1,replied=1`,第二次 `replied=0`,并确认两个不同 client_event_id、没有重复行。
- 证据:录屏两次触摸对比 + SQL。

## 拍 4|投喂 → 晚间自己提起

- 触发:白天(偏移 14:00)拖咖喱投喂;偏移至 21:00 后发起一次闲聊(话题不提食物)。
- 预期:投喂即时吃动画 + physio.hunger 上升;晚间回复中**自发**提及当日投喂(shared_moment 回流)。允许两次对话内出现;两次都不出现 = FAIL。
- 证据查询:`vpet_events.event='feed'` 的 before/after/physio_delta +
  `scripts/vpet_acceptance_evidence.py shared-moments --date YYYY-MM-DD --source vpet_feed` 恰有一条日聚合。
- 证据:投喂录屏 + 晚间对话截图 + SQLite 输出 + shared_moment 导出 JSON。

## 拍 5|回来收 digest(真时复验一次)

- 触发:偏移制造 3h 空档,期间引擎侧放入 reminder(过期 >30min)+ nudge + 过期 greeting。
- 预期:回来后一句 digest(reminder+nudge 合并表述)+ overdue 持久卡(不打断);greeting 被丢弃。
- SQL:`pending_discarded/pending_digested/pending_overdue` 三类齐,且真正可见的 digest/overdue 各有一条 `notice_shown`;过期 greeting 无 `notice_shown`。
- 证据:录屏 + SQL;**另跑一次不带偏移的真实 3h 版本**(可与日常使用重合)。

## 拍 6|她也困了 / 晚安去睡(真时复验一次)

- 触发:偏移至 00:40(睡眠窗内),发一句消息;然后说"晚安"。
- 预期:回复带困意(physio woken 措辞);"晚安"后壳进入睡眠动画;睡眠窗内无任何主动播报(murmur/nudge 被门控)。
- 证据:保存 state 响应(`physio.sleeping=true`,不是查询不存在的数据库列);对话截图 + 睡眠动画录屏 + 次日查询睡眠窗内 `notice_shown` 为 0。聊天回复本身不是主动展示,单独按 chat turn_id 排除。

## 周检三项(7/25–31 一轮)

| 项 | 口径 | SQL/来源 |
|---|---|---|
| 记忆回流被接住 ≥1 次 | 她提起往事且你回应了 | 对话记录人工标 + messages meta |
| 共处 session ≥3 次 | work_start/stop 对 | `SELECT count(*) FROM vpet_events WHERE event='work_stop';` |
| 夜间打扰 = 0 | 睡眠窗内零主动展示 | 睡眠窗内 `notice_shown` 排除用户主动 chat 后计数为 0 |

周检必须覆盖连续 7 个服务端本地日期,计划窗固定 2026-07-25‑31。有效日按
`presence_heartbeat.count` 合计 ≥120 分钟;少于 7 个有效日时周检不算完成,不得用时钟偏移压缩。

## 检查点与签署

- **7/24**:六拍状态表(PASS/FAIL/证据链接)提交;未全绿则停止新增,只修六拍路径。
- **7/27**:仍失败的拍改标 `DEFERRED`,进入 v1.1;冻结前不得反复重开范围。
- 每拍签署人:Codex(审证据完备性)+ 用户(体验确认);两签俱全才算 PASS。

## 发布判定

- 六拍 PASS + 周检完成:`release_level=FULL`。
- 任一拍 DEFERRED 或周检未完成:`release_level=REDUCED`;仍按硬日期冻结交付,但不得写“六拍完成”。
- 任一 crash、数据丢失、token 绕过或 UI 主线程冻结:`release_blocked=true`;这四类不能用 DEFERRED 豁免。
- 总表固定写 `eval/acceptance/v1/RESULT.json`,冻结 manifest 只引用它,不复制人工结论。
