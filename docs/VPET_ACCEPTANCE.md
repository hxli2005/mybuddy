# 六拍验收规格(PM 轮批准条件 #7:验收客观化)

> 完工定义:每拍证据包齐 → Codex 审计签"证据完备"(不是作者签"我觉得发生了")→ 周检三项一轮。
> 证据包目录:`eval/acceptance/v1/beat-N/`,内含:操作录屏或截图序列 + SQL 输出粘贴 + 壳日志片段。
> 钟点拍用时钟偏移(壳设置 + 引擎 `MYBUDDY_TIME_OFFSET_MINUTES`,两侧一致);拍 5、6 各留一次**真机真时**复验。

## 拍 1|回场问候(带记忆)

- 触发:引擎在跑,壳 idle 暂停态(模拟 idle>30min),恢复输入。
- 预期:壳先 POST user_back → drain;若有积压,气泡一句 digest;问候语引用近期真实话题(living_state recent_self_event)。
- SQL:`SELECT * FROM vpet_events WHERE event='user_back' ORDER BY id DESC LIMIT 1;` + drain 遥测(`pending_drained/digested`)。
- 证据:录屏(离开→回来→气泡)+ SQL 行。

## 拍 2|角落做自己的事(idle 表达)

- 触发:无交互 ≥3 分钟,引擎 living_state 活动非空。
- 预期:`GET /api/vpet/state` 返回 idle_hint ∈ {read,write,gaze,...};壳播对应闲时动画;**全程无气泡**(宪法 3)。
- SQL:无(state 不落遥测);证据:state 响应 JSON 抓包/日志 + 动画录屏 10s。

## 拍 3|摸头反射 + 首次升格

- 触发:当天第一次摸头。
- 预期:反射动画 <100ms(录屏帧判);随后气泡一句 ≤28 字,不反问不建议;第二次摸头只有动画(预算/启发式生效)。
- SQL:`SELECT event,count,escalated,replied,gate_reason FROM vpet_events WHERE event='touch_head' AND date(created_at)=date('now') ORDER BY id;`(首行 escalated=1,后续 replied=0)。
- 证据:录屏两次触摸对比 + SQL。

## 拍 4|投喂 → 晚间自己提起

- 触发:白天(偏移 14:00)拖咖喱投喂;偏移至 21:00 后发起一次闲聊(话题不提食物)。
- 预期:投喂即时吃动画 + physio.hunger 上升;晚间回复中**自发**提及当日投喂(shared_moment 回流)。允许两次对话内出现;两次都不出现 = FAIL。
- SQL:`SELECT context_json FROM vpet_events WHERE event='feed' AND date(created_at)=date('now');` + `ltm` 中 shared_moment 存在性(mem_type='shared_moment' AND source='vpet_feed')。
- 证据:投喂录屏 + 晚间对话截图 + 两条 SQL。

## 拍 5|回来收 digest(真时复验一次)

- 触发:偏移制造 3h 空档,期间引擎侧放入 reminder(过期 >30min)+ nudge + 过期 greeting。
- 预期:回来后一句 digest(reminder+nudge 合并表述)+ overdue 持久卡(不打断);greeting 被丢弃。
- SQL:`SELECT event,context_json FROM vpet_events WHERE event IN ('pending_discarded','pending_digested','pending_overdue') ORDER BY id DESC LIMIT 5;`
- 证据:录屏 + SQL;**另跑一次不带偏移的真实 3h 版本**(可与日常使用重合)。

## 拍 6|她也困了 / 晚安去睡(真时复验一次)

- 触发:偏移至 00:40(睡眠窗内),发一句消息;然后说"晚安"。
- 预期:回复带困意(physio woken 措辞);"晚安"后壳进入睡眠动画;睡眠窗内无任何主动播报(murmur/nudge 被门控)。
- SQL:physio_state.sleeping 派生验证(state 响应)+ 当晚 `vpet_events` 无 escalated/主动派送行。
- 证据:对话截图 + 睡眠动画录屏 + 次日 SQL(夜间零打扰)。

## 周检三项(7/25–31 一轮)

| 项 | 口径 | SQL/来源 |
|---|---|---|
| 记忆回流被接住 ≥1 次 | 她提起往事且你回应了 | 对话记录人工标 + messages meta |
| 共处 session ≥3 次 | work_start/stop 对 | `SELECT count(*) FROM vpet_events WHERE event='work_stop';` |
| 夜间打扰 = 0 | 睡眠窗内零主动展示 | 睡眠窗时段 vpet_events 无派送类事件 |

## 检查点与签署

- **7/24**:六拍状态表(PASS/FAIL/证据链接)提交;<4 拍 PASS → 触发砍拍(KICKOFF)。
- 每拍签署人:Codex(审证据完备性)+ 用户(体验确认);两签俱全才算 PASS。
