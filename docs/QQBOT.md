# QQ 机器人接入手册

本文档描述 MyBuddy 的 QQ 官方机器人接入方式。QQ 是独立渠道挂件,核心聊天能力仍由 `ChatService` 统一承载,未来 Web/App 也复用这层服务。

## 架构

```text
QQ 官方机器人 WebSocket
  -> mybuddy.channels.qq.QQBotAdapter
  -> ChatService.chat(user_id, text, source="qq")
  -> Agent / Memory / Tools / LLM
```

QQ adapter 只负责:

- 接收 QQ 消息事件。
- 用 QQ external id 映射内部 `user_id`。
- 使用 `inbound_events` 表做消息去重。
- 执行跨渠道命令,如 `/help`、`/quota`、`/good`、`/bad`。
- 调用 `ChatService` 并把回复发回 QQ。

它不直接写长期记忆、不直接改画像、不直接实例化 Agent。

## 技术选型

- QQ 接入:QQ 官方机器人 API,优先 WebSocket 长连接。
- Python SDK:可选依赖 `qq-botpy`。
- 服务层:主库保存用户、外部账号、入站事件和额度;每个用户使用独立运行目录。

每用户数据目录:

```text
data/users/{user_id}/mybuddy.db
data/users/{user_id}/memory/
data/users/{user_id}/skills/
data/users/{user_id}/trajectories/
```

这种方式能在不大改现有单用户记忆/画像表的前提下完成小规模多用户隔离。

## 配置

复制 `config.example.yaml` 后填写:

```yaml
channels:
  qq:
    enabled: true
    app_id: ${QQ_BOT_APP_ID}
    app_secret: ${QQ_BOT_APP_SECRET}
    sandbox: true
    allow_auto_create_user: false
    daily_message_limit: 30
    reply_on_duplicate: false
```

建议测试期保持 `allow_auto_create_user: false`,由管理员显式创建并绑定用户。

## 安装与启动

```bash
uv sync --extra qq
export QQ_BOT_APP_ID="..."
export QQ_BOT_APP_SECRET="..."
export ANTHROPIC_API_KEY="..."
uv run mybuddy qqbot --config config.yaml
```

生产/试点部署建议拆成两个进程:

```bash
uv run mybuddy web --host 127.0.0.1 --port 8000
uv run mybuddy qqbot --config config.yaml
```

Web 管理面板和 QQ bot 可以共享同一个主库。QQ bot 出故障不会影响 Web/CLI。

## QQ 开放平台操作

1. 登录 QQ 开放平台并创建 QQ 机器人。
2. 保存 `AppID` 和 `AppSecret`。
3. 沙箱阶段添加测试成员。
4. 配置服务器 IP 白名单或按平台要求完成通道配置。
5. 测试成员在 QQ 中添加机器人并发送 `/help`。

## 管理员命令

创建用户:

```bash
uv run mybuddy users create "测试用户A" --daily 30
```

绑定 QQ external id:

```bash
uv run mybuddy users bind-qq 1 "qq-openid-or-user-id" --name "测试用户A"
```

查看用户:

```bash
uv run mybuddy users list
```

禁用/启用用户:

```bash
uv run mybuddy users disable 1
uv run mybuddy users enable 1
```

调整额度:

```bash
uv run mybuddy users quota 1 --daily 50
```

## 用户命令

- `/help`:查看帮助。
- `/quota`:查看今日额度。
- `/persona`:查看个人 AI 人格摘要。
- `/persona name 小鹿`:修改 AI 名字。
- `/persona style 更直接一点`:修改整体风格。
- `/persona tone 自然、短句`:修改语气。
- `/persona relationship 像学习搭子`:修改关系定位。
- `/persona address 阿航`:修改称呼。
- `/persona habit 先给结论`:追加回应习惯。
- `/persona habits clear`:清空个人回应习惯。
- `/persona reset`:重置为全局默认人格。
- `/privacy`:查看数据说明。
- `/reset`:重置当前运行上下文。
- `/good`:标记上一轮有帮助。
- `/bad`:标记上一轮不合适。

## 验收清单

- 未绑定 QQ 用户会收到测试名单提示,不会进入 LLM。
- 已绑定用户可以收到基础聊天回复。
- 同一 QQ event 重投只处理一次。
- 两个 QQ 用户写入不同的 `data/users/{user_id}` 目录。
- 用户达到每日额度后,不再调用 LLM。
- `uv run pytest tests/test_chat_service.py tests/test_qq_channel.py` 通过。
