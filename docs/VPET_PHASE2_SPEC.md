# VPet 深度集成二期 · 实现规格 v2

> **历史文档**:记录 O1/VPet 插件阶段的工程决定。自 2026-07-11 起,v1 施工以
> `VPET_PRODUCT_V1.md` 与 `VPET_V1_*` 规格包为准;冲突处不得引用本文覆盖最终规格。

> Claude(方案作者)与 Codex/GPT-5.5(实现者)四轮评审后收敛:两轮实现层 + 两轮功能层(Codex thread `019f40cd-0311-7762-b1e3-d29e048ca355`)。
> 实现以本文件为准;所有开关关闭时,一期(commit `ce7d1e1`)行为必须原样保留。

## 0. 赌注与失败模式

**核心赌注(功能轮修正后)**:VPet 的独占资产不是"身体",而是三层——①零打开成本的**常驻在场**(不用召唤、不用切应用);②**时机感**(第一次知道用户在不在、刚回来、坐了多久);③**可感身体**(触摸/数值/动作)。二期赌"常驻在场 + 时机感 + 少量身体反馈",纯身体功能容易沦为 gimmick。

**最硬的失败模式**:主动行为错时打扰,把"人味"做成"打扰人的拟人弹窗"→ 用户两周后不再打开。全部 UX 约束(§6)围绕这一条。

**非目标(明确不做)**:经济/商店语义、多皮肤、`/v1` streaming、负面情绪期后端自动升格、feed→长期记忆(T2)、stats_delta 反向写(T2,研究信息量低)、TTS(T2 靠后)。

## 1. T1 功能定义(最终版,五条)

1. **body_state 一致性护栏**:VPet chat/event 携带可见身体数值,后端用它约束 `living_state`,避免回复与屏幕上的饱食/口渴/健康/心情状态矛盾(VPet 数值用户可见——不注入不是少一个功能,而是允许 MyBuddy 对屏幕上的身体撒谎),并记录矛盾事件供标注。
2. **触摸升格**:VPet 原生触摸反馈完整保留(反射层,插件不拦截);插件仅在本地启发式命中时申请一次短句 LLM 反应,后端按预算和忙闲批准;回复必须短(≤28 中文字/一句)、不反问、不给建议、不展开话题。
3. **在场门控与回场摘要**:插件按用户在场状态控制 pending 派送;回来时先发 `user_back` 再 drain;后端对过期 greeting 丢弃、nudge 合并、reminder 标 overdue,生成 digest 一句话摘要。
4. **physical_proactive 最小版**:主动消息受在场、冷却、静默时段、全屏状态约束,开时 interrupt 级消息走到屏幕前展示;T1 只测派送时机与打扰率,不做复杂策略生成。
5. **VPet 遥测基础设施**(常开):所有 chat/event/drain 记录当时开关、body_state、presence、升格/降级/丢弃/合并结果——两周后分析留存、响应率、打扰率、身体矛盾率的唯一数据源。

## 2. 实验设计(两周)

| 开关 | 角色 | 排期 |
|---|---|---|
| `body_state_injection` | 一致性护栏,**不进 ABAB** | 全程 on;指标 = 矛盾事件率(回复含身体陈述且与当时 body_state 阈值冲突;规则粗筛 + 人工标注) |
| `physical_proactive` | 主变量(最能证明常驻+时机感) | **按周切**(week1 off / week2 on,顺序抛硬币定,对冲作者预设);按天切会把体验切碎 |
| `touch_escalation` | 次变量 | **按天交替**,嵌套在两个周条件内(保证两周都覆盖 on/off) |

- Day 1–2 标为 **acclimation(适应期)**,不进主分析或单独分析新鲜感。
- 每天记录 `day_index`,分析时把时间趋势当协变量(至少分前/后半看)。
- 指标口径:nudge 响应率 = `messages` 中 `meta.source='pending_message'` 且 `pending_source in ('nudge','dynamic')` 的 assistant 行,同 session 10 分钟内出现 `role='user'` 的比例;触摸曲线/升格率/打扰率/在场时长从 `vpet_events` 按 `server_flags_json` + `day_index` 聚合。

## 3. 交付切分

| 块 | 内容 | 预估 |
|---|---|---|
| 后端 PR1 | token + `/api/vpet/event` + 遥测表 + agent_lock + drain 语义升级(overdue/discard/digest) | 1.5 天 |
| 后端 PR2 | body_state 归一化/透传/注入 + living_state 让位 + Agent.run 扩签名 + 升格模板 | 1 天 |
| C# scaffold | `vpet-plugin/`,代码完备;Windows 侧构建联调 | 1 天 + 联调 1–3 天 |

## 4. 后端 PR1 规格

### 4.1 config(mybuddy/config.py)

```python
class VPetConfig(BaseModel):
    body_state_injection: bool = False   # 实验期常开;默认 false 保持一期行为
    touch_escalation: bool = False
    physical_proactive: bool = False
    touch_escalation_daily_limit: int = 20
    greeting_discard_after_minutes: int = 120   # greeting 超窗即丢
    reminder_overdue_after_minutes: int = 30    # reminder 过期转 overdue
    bridge_token: str = ""
```

`Config` 加 `vpet: VPetConfig = Field(default_factory=VPetConfig)`;`config.example.yaml` 加注释齐全的 `vpet:` 段。

### 4.2 token 鉴权(双服务路径)

- `bridge_token` 非空时保护 `/api/*` 与 `/v1/*`;`/`、`/static/*` 不罩。Header `X-MyBuddy-Token`,失败 401;空 token = 现行为。
- `mybuddy/web.py`:每个 `do_*` 最外层第一行 `if not self._authorize_request(): return`。
- FastAPI:`@app.middleware("http")`(不用 dependency,防漏挂)。
- 红线:不允许一条路径罩了另一条裸奔。

### 4.3 POST /api/vpet/event(新端点,双路径)

```jsonc
{
  "event": "touch_head",       // touch_head | touch_body | feed | user_back
  "count": 7,                  // 客户端 30s 聚合;后端 clamp 1..50
  "body_state": {...},         // 可选
  "context": {"item": "咖啡"},
  "want_reply": false,
  "client_event_id": "..."     // 可选;unique 去重,重放返回原结果
}
```

`want_reply=true` 的批准链(按序):
1. 服务端 `touch_escalation` 关 → `gate_reason="escalation_disabled"`
2. `agent_lock.locked()` → `gate_reason="agent_busy"`(不排队,插件走原生反射)
3. 当日 `escalated=1` 计数 ≥ limit(vpet_events 表为权威,内存计数只作安全阀)→ `gate_reason="budget_exceeded"`
4. 通过 → 全量 `Agent.run()` 事件模式(§5.3),响应复用 `chat_to_vpet_payload` 形状
- 同一事件窗口(同 client_event_id)只允许一次升格;`user_back` 一律不升格。
- 拒绝/false → HTTP 200 `{"ok": true, "replied": false, "gate_reason": ...}`。

### 4.4 遥测表 vpet_events

```sql
id integer pk
client_event_id text unique null
event text not null              -- 含派送侧事件:pending_discarded / pending_digested
count integer not null
body_state_json text null
context_json text null
want_reply integer not null
escalated integer not null
replied integer not null
gate_reason text null
turn_id text null
message_id integer null
client_flags_json text null      -- 插件 X-MyBuddy-Client-Flags 声明
server_flags_json text not null  -- 后端三开关快照
last_emotion_label text null     -- 仅分析用,不参与升格决策
day_index integer null           -- 实验日序号
created_at datetime (indexed)
```

新增 `record_vpet_event()` / `mark_vpet_event_result()`(建议 `mybuddy/storage/vpet_events.py`);init_db 走 create_all 自动补表。

### 4.5 AppState.agent_lock

`asyncio.Lock`:`chat_payload()` 整体等锁;event 升格路径不等锁,`locked()` 即降级。参照 `services/chat.py` 的 `runtime.lock` 先例。

### 4.6 drain 语义升级(取代一期"stale 一刀切")

`POST /api/vpet/pending/drain` 接受 `{"digest": true}`(插件回场时传)。pending 三分语义:

| 类型 | 处理 | 遥测 |
|---|---|---|
| 用户显式 reminder 过期 | **overdue**:不丢、`persistent=true`、`interrupt=false`,插件持久可见展示 | 正常记录 |
| greeting 超窗 | **后端 drain 时丢弃**,不返回插件 | `event=pending_discarded, reason=stale_greeting` |
| nudge/dynamic | **digestable**:合并进 digest,不逐条涌出 | `event=pending_digested` |

digest 响应形态(模板拼接,**不走 LLM**):

```json
{
  "events": [ /* overdue reminder 等必须逐条展示项 */ ],
  "digest": {
    "text": "你不在的时候我攒了两件事:一个提醒,还有一次想叫你歇会儿。",
    "sources": ["reminder", "nudge"],
    "discarded_count": 1
  }
}
```

digest 放后端不放插件:后端拥有 source/meta/scheduled_at 和优先级,且遥测不被客户端策略切散。

### 4.7 条件标注(ABAB 分析的生命线)

- vpet_events 每行存 `server_flags_json` + `client_flags_json` 双快照。
- VPet 来源 chat 的 user message meta:
  `{"source": "vpet_chat", "vpet": {"event": "chat", "body_state_present": true, "body_state_used": true, "client_flags": {...}, "server_flags": {...}}}`
- 升格事件合成消息 meta:
  `{"source": "vpet_event", "vpet": {"event": "touch_head", "count": 7, "body_state_used": true, "client_event_id": "...", "event_log_id": 123}}`
- 验收:两周后只靠 SQL 能写出 §2 全部指标。

## 5. 后端 PR2 规格

### 5.1 normalize_body_state()(mybuddy/integrations/vpet.py)

```python
_NUM_0_100 = {"food", "drink", "feeling", "health", "strength"}
_UNBOUNDED_NONNEG = {"likability", "money"}   # 低把握:clamp >=0,软上限 100000
_MODES = {"Happy", "Nomal", "PoorCondition", "Ill"}  # VPet 源码拼写就是 Nomal
```

非 dict 返回 `{}`;白名单外丢弃;`_NUM_0_100` clamp 0..100;mode 非法丢弃。

### 5.2 透传链(显式 kwarg,禁止 AppState 字段/ContextVar)

`VPetChatRequest.body_state` → `vpet_chat_payload(..., body_state=...)` → `chat_payload(..., body_state=...)` → `Agent.run(..., body_state=...)`。理由:web.py 多 handler 线程投递同一 bg loop,请求可并发,实例字段会串。

### 5.3 Agent.run 扩签名(mybuddy/agent/core.py:146)

```python
async def run(self, user_input: str, *, source: str = "chat",
              body_state: dict[str, Any] | None = None,
              enable_tools: bool = True,
              meta: dict[str, Any] | None = None) -> AgentResult:
```

- `enable_tools=False`(事件模式):`provider.generate(..., tools=None)` + 跳过 web_search 预取。不用空 ToolRegistry(污染构造)、不靠 prompt(不可保证)、不用 max_steps=2(挡不住第一步)。
- `EmotionDetector` 保留(action/expression 映射依赖 emotion)。
- `source/meta` 落到 messages 表持久化,不是只写 trajectory。

### 5.4 living_state 让位(一致性护栏的核心)

`synthesize_living_state(persona, ..., body_state=None)`(调用点 core.py:191):
- 开关开且有 body_state:虚构身体条款让位——不再生成"刚吃完晚饭/杯水还温着"类句子;身体相关 `today_status/current_mood` 由真实数值合成(`food<=30` → "肚子有点空,语气会更黏一点")。保留 `recent_self_event` 真实话题衔接。
- 注入走 living_state 单一入口,**不走** `build_system_prompt` extras——两套身体现实同框是人味事故。
- 开关关/无 body_state:与现状完全一致。

### 5.5 升格合成输入(最终模板)

```text
touch_head: 用户刚刚摸了摸你的头,30 秒内共 {count} 次。这不是普通聊天输入;请只给一句很短的自然反应,像桌宠被轻轻碰到后的即时回应。不要展开新话题,不要反问,不要给建议。
touch_body: 用户刚刚轻轻碰了碰你/戳了戳你,30 秒内共 {count} 次。(约束同上)
feed:       用户刚刚给你喂了{item 或 "一点东西"}。(约束同上)
```

回复目标 ≤28 中文字、最多一句。

## 6. C# 插件 scaffold(vpet-plugin/,Windows 侧构建)

```text
MyBuddyPlugin.cs        // 入口、生命周期、事件订阅
BridgeClient.cs         // HTTP + X-MyBuddy-Token + X-MyBuddy-Client-Flags
EventAggregator.cs      // 30s 聚合 + client_event_id;过滤窗口拖拽/长按移动/快速重复误触
PresenceGate.cs         // GetLastInputInfo;idle>30min 停 drain;回来先 user_back 再 drain(digest=true)
DrainWorker.cs          // 10–30s timer;digest/overdue/普通事件的差异化展示
ActionMapper.cs         // action/expression → VPet 动作;API 名待 Windows 侧确认,做成可配置映射表兜底
BridgeModels.cs         // DTO
SettingsView(.xaml/.cs) // bridge URL / token / 三开关镜像 / "今天安静"
```

**行为约束(全部来自 UX 反模式审查,违反即验收不过)**:
- 反射层不拦截:VPet 原生触摸动画/台词完整保留,插件只在启发式命中时额外异步 POST。
- 升格启发式(本地,只申请不批准):当天第一次触摸 / 30s 内 count≥5;同窗口只申请一次。
- physical_proactive 约束集:全屏/演示检测静默、两次物理接近 ≥45min 冷却、每日上限、菜单"今天安静"一键关;开时仅非 overdue 的 interrupt 消息走到屏幕前,关时只弹气泡。
- 回场顺序:`user_back`(want_reply=false)→ `drain(digest=true)`;digest 一句 + overdue 持久卡片,不逐条轰炸。
- chat 发送瞬间本地播 thinking 动画(延迟掩蔽);断网/超时不得冻结 UI;token 错误有可见状态。

## 7. 测试与验收

**pytest**(仿 tests/test_api.py;必要时新增 tests/test_vpet.py):
- normalize_body_state:白名单/clamp/Nomal/非 dict
- event false 只落表不触 agent;client_event_id 重放去重;同窗口二次升格拒绝
- 批准链三种 gate_reason;通过时写 messages + 返回 VPet payload 形状
- token:空 token 旧测试全过;非空时 /api/* /v1/* 401、/ 不拦(双路径)
- drain 三分语义:overdue 不丢不打断、greeting 超窗丢弃且遥测有 pending_discarded、nudge 进 digest;digest 文本模板正确
- body_state 关不进 prompt;开时 living_state 虚构身体条款让位(断言 prompt 文本)
- §2 指标 SQL 可算

**Windows 手动联调**:status 连通(token 对/错)→ chat 动作表情 → 摸头原生动画零感知延迟 → 拖拽不触发事件 → 30s 聚合一次 → agent 忙不排队 → 空闲 30min drain 停 → 回来 user_back+digest(攒 3 小时只出一句摘要+overdue 卡片)→ 全屏时不走过来 → 断网不冻结。

## 8. 已知不确定性

- VPet 插件 API 把握度**低到中**:TalkBox 挂法、`Event_TouchHead/TouchBody` 真实事件名、动作接口、DLL 部署目录——以 VPet.Plugin.Demo 与 VPet 源码为准,ActionMapper 用可配置映射表兜底。**这是全项目最大工期风险。**
- VPet 数值范围按低把握 clamp(§5.1)。

## 9. 决策记录(四轮对喷的裁决,防实现期翻案)

| 争点 | 原案 | 裁决 |
|---|---|---|
| VPet 独占资产 | 身体 | **常驻在场 + 时机感 + 少量身体反馈**(功能轮 F1,Codex 推翻,采纳) |
| body_state 注入 | Codex 主张砍(用户难感知) | **保留但降级为一致性护栏**:VPet 数值用户可见,不注入=允许对屏幕上的身体撒谎;测矛盾事件率不测偏好分(D1,Claude 反驳,Codex 接受) |
| 升格回应路径 | 轻路径(无工具单步) | **全量 Agent.run + enable_tools=False**:轻路径补账成本高、失忆风险大 |
| 反馈菜单 | 消融条件 | **常开基础设施**:样本稀疏,ABAB 不可解释 |
| token | 可选 T2 | **T1 必做**:跨 LAN 时 /api/messages、/api/persona 裸奔 |
| 负面情绪期升格 | T1 启发式 | **砍**:后端主动升格复杂度不值;仅记 last_emotion_label |
| body_state 注入点 | prompt extras | **living_state 单一入口**:两套身体现实同框是事故 |
| stale 提醒 | 一刀切降级 | **三分**:reminder→overdue 持久可见 / greeting→丢弃 / nudge→digest(F3c+D3) |
| 回场派送 | 逐条补派 | **digest 后端模板拼接**:一句摘要 + overdue 卡片,防消息轰炸(F3d+D3) |
| 三开关 ABAB | 三变量都切 | **body_state 常开、physical 按周、touch 按天嵌套**;Day1-2 适应期(D2) |

**T2 排序(功能轮重排)**:①桌面边缘状态表达(吸收 living_state↔idle:不开口,用姿态表达当前状态,"可被瞥见"是 VPet 最强下行)②共处/陪伴工作模式(给两周使用一个稳定场景)③注意力感知触达完整版 ④feed→shared_moment ⑤TTS ⑥stats_delta。

**实现者赌注**:72% 提升两周留存;最大风险 = 主动行为错时打扰(门控/digest/overdue/护栏已收窄失败面)。
