# VPet 集成说明

> **O1 历史接入说明**:本文描述冻结的 VPet 插件探针与 bridge v1。小布桌宠 v1 已转向
> 独立 WPF `buddyshell/`,施工与协议真相源见 `VPET_V1_KICKOFF.md`、`VPET_V1_PROTOCOL_V2.md`。

MyBuddy 可以作为 VPet 的本地 AI 引擎运行。推荐架构是:VPet 负责桌宠窗口、
动画、气泡和语音;MyBuddy 负责人格、记忆、情绪、工具调用和主动关怀。

## 启动 MyBuddy

```bash
uv run mybuddy web --host 127.0.0.1 --port 8000
```

VPet 插件从本机调用 `http://127.0.0.1:8000`。需要先在 `config.yaml`
配置 `llm.api_key`。

## 推荐接入方式

仓库现在包含一份 VPet 代码插件 scaffold:

```text
vpet-plugin/
  MyBuddyBridgePlugin.cs   # VPet MainPlugin 入口
  MyBuddyTalkAPI.cs        # TalkBox 接入
  MyBuddyPlugin.cs         # bridge 生命周期、timer、事件转发
  BridgeClient.cs          # HTTP + X-MyBuddy-Token + X-MyBuddy-Client-Flags
  EventAggregator.cs       # 30s 聚合、client_event_id、触摸启发式
  PresenceGate.cs          # GetLastInputInfo 在场门控
  DrainWorker.cs           # pending/digest/overdue 展示
  VPetHostAdapter.cs       # VPet API 适配层
```

在 Windows 或支持 Windows targeting 的 .NET SDK 环境里构建:

```bash
dotnet build vpet-plugin/MyBuddy.VPetPlugin.csproj -p:EnableWindowsTargeting=true
```

生成 VPet MOD 目录:

```bash
bash scripts/package_vpet_plugin.sh
```

Windows 没有 Bash 时可用:

```powershell
.\scripts\package_vpet_plugin.ps1
```

输出目录:

```text
dist/vpet/1114_MyBuddyBridge/
  info.lps
  plugin/
    MyBuddy.VPetPlugin.dll
    MyBuddy.VPetPlugin.deps.json
    ...
```

把整个 `1114_MyBuddyBridge` 目录放进 VPet 本地 MOD 目录后,按 VPet 的 MOD 设置启用。
代码入口继承官方 `VPet_Simulator.Windows.Interface.MainPlugin`。

### 桌宠专用接口

发送用户文本:

```http
POST /api/vpet/chat
Content-Type: application/json

{
  "message": "今天有点累",
  "event": "user_chat",
  "body_state": {
    "food": 42,
    "drink": 70,
    "feeling": 61,
    "health": 90,
    "strength": 55,
    "likability": 120,
    "money": 80,
    "mode": "Nomal"
  }
}
```

响应:

```json
{
  "ok": true,
  "bridge": "vpet-bridge/1",
  "text": "先坐会儿,别硬顶。",
  "speech": {
    "text": "先坐会儿,别硬顶。",
    "interrupt": true
  },
  "action": {
    "name": "concern",
    "priority": 85,
    "loop": false,
    "reason": "strong_negative_emotion"
  },
  "expression": {
    "name": "worried"
  }
}
```

VPet 插件只需要读:

- `speech.text`:气泡文本或 TTS 文本。
- `speech.interrupt`:是否打断当前语音/动作。
- `action.name`:动画意图。
- `expression.name`:表情意图。
- `pending`:本轮聊天前后顺手播出的主动消息。

插件每个请求都会带 `X-MyBuddy-Client-Flags`,后端会把客户端开关和服务端开关同时写入
`vpet_events`,便于两周实验分析。

### VPet 事件

插件把触摸、投喂和回场事件发给后端:

```http
POST /api/vpet/event
Content-Type: application/json

{
  "event": "touch_head",
  "count": 7,
  "body_state": {"food": 20, "mode": "Nomal"},
  "context": {},
  "want_reply": true,
  "client_event_id": "vpet-20260708153000-..."
}
```

支持的 `event`: `touch_head`、`touch_body`、`feed`、`user_back`。

`want_reply=true` 时,后端按顺序检查:

- `touch_escalation` 是否开启。
- agent 是否忙;忙则立即降级,不排队。
- 当日升格次数是否超过 `touch_escalation_daily_limit`。

通过时返回和聊天相同的 VPet payload,并保证回复被压成一句短反应。拒绝时仍返回 200:

```json
{"ok": true, "replied": false, "gate_reason": "agent_busy", "event_log_id": 12}
```

### 主动消息

查看未派送主动消息,不标记已读:

```http
GET /api/vpet/pending
```

取出并标记已派送:

```http
POST /api/vpet/pending/drain
Content-Type: application/json

{"digest": false}
```

建议 VPet 插件每 10-30 秒调用一次 drain 接口。如果返回 `events` 非空,
按事件里的 `speech/action/expression` 播放。

用户离开超过 `IdlePauseMinutes` 后,插件停止普通 drain。用户回来时先发:

```json
{"event": "user_back", "count": 1, "want_reply": false}
```

然后调用:

```json
{"digest": true}
```

digest 语义:

- 过期 reminder:返回为 `persistent=true`、`interrupt=false`,插件持久展示。
- 超窗 greeting:后端丢弃,只写 `pending_discarded` 遥测。
- nudge/dynamic:后端合并成一句 digest,只写 `pending_digested` 遥测。

### 状态探测

```http
GET /api/vpet/status
```

返回 bridge 版本、是否配置 LLM、人格和当前支持的动作名。

## OpenAI-compatible 快速 POC

如果使用 VPet 现成 ChatGPT/VPetLLM 类插件,可把 endpoint 配成:

```text
http://127.0.0.1:8000/v1
```

非流式接口:

```http
POST /v1/chat/completions
Content-Type: application/json

{
  "model": "mybuddy",
  "stream": false,
  "messages": [
    {"role": "user", "content": "你好"}
  ]
}
```

返回标准 `choices[0].message.content`。MyBuddy 的动作和情绪信息放在扩展
字段 `mybuddy.action`、`mybuddy.expression`、`mybuddy.emotion` 里。现成插件
如果只读取 OpenAI 标准字段,也能先完成对话 POC。

## 动作映射

| MyBuddy 状态 | action | expression |
| --- | --- | --- |
| 普通回复 | `talk` | `neutral` |
| 正向情绪 | `happy` | `happy` |
| 轻度负面 | `comfort` | `worried` |
| 强负面 / 主动关怀 | `concern` | `worried` |
| 安全风险 | `safety` | `serious` |
| 工具辅助回复 | `thinking` | `thinking` |
| 早安问候 | `greet` | `smile` |
| 提醒到期 | `remind` | `alert` |

插件侧可以把这些通用动作名映射到 VPet 当前模型实际可用的动画名。MyBuddy 不直接
假设具体 VPet 皮肤或动画资源。

当前 scaffold 的保守策略是:先尝试同名自定义动画;没有则退回 VPet 官方公开动画入口,
例如待机、默认动画和气泡,避免依赖某个皮肤私有动作。

## Token

`config.yaml` 可配置:

```yaml
vpet:
  bridge_token: "change-me"
```

token 非空时保护 `/api/*` 和 `/v1/*`,插件必须带:

```http
X-MyBuddy-Token: change-me
```

`/` 和 `/static/*` 不拦截,方便本机打开 UI。

## 集成边界

MyBuddy 现在同时提供稳定本地协议和 VPet C# scaffold。仍需在 Windows + VPet
实机环境完成手动联调:

- token 正确/错误都有可见状态。
- chat 能触发 thinking 和动作/表情映射。
- 摸头/摸身体保留 VPet 原生动画,插件只异步上报。
- 拖拽/长按移动不触发触摸升格。
- 回场顺序为 `user_back` 后 `drain(digest=true)`。
- 全屏/演示时 interrupt 不走到前台。
- 断网/超时不冻结 UI。

人工验收步骤见 [VPET_PHASE2_MANUAL_QA.md](VPET_PHASE2_MANUAL_QA.md)。
如果由 Windows 环境里的 Codex 执行,使用
[VPET_PHASE2_WINDOWS_CODEX_RUNBOOK.md](VPET_PHASE2_WINDOWS_CODEX_RUNBOOK.md)。
