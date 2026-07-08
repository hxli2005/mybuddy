# VPet 集成说明

MyBuddy 可以作为 VPet 的本地 AI 引擎运行。推荐架构是:VPet 负责桌宠窗口、
动画、气泡和语音;MyBuddy 负责人格、记忆、情绪、工具调用和主动关怀。

## 启动 MyBuddy

```bash
uv run mybuddy web --host 127.0.0.1 --port 8000
```

VPet 插件从本机调用 `http://127.0.0.1:8000`。需要先在 `config.yaml`
配置 `llm.api_key`。

## 推荐插件调用方式

### 桌宠专用接口

发送用户文本:

```http
POST /api/vpet/chat
Content-Type: application/json

{
  "message": "今天有点累",
  "event": "user_chat"
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

### 主动消息

查看未派送主动消息,不标记已读:

```http
GET /api/vpet/pending
```

取出并标记已派送:

```http
POST /api/vpet/pending/drain
Content-Type: application/json

{}
```

建议 VPet 插件每 10-30 秒调用一次 drain 接口。如果返回 `events` 非空,
按事件里的 `speech/action/expression` 播放。

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

## 集成边界

MyBuddy 当前不直接包含 VPet C# 插件代码。这里提供的是稳定本地协议,用于:

- 自写 VPet 代码插件调用。
- 现成 OpenAI-compatible VPet 插件快速接入。
- 后续把 VPet 点击、摸头、拖拽、投喂等事件回传为 `event`。
