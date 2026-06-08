# MyBuddy

生活陪伴型 AI 小伙伴。借鉴 [NousResearch Hermes Agent](https://github.com/nousresearch/hermes-agent) 的自学习机制,并加入角色关系编排、分层文本记忆、动态命题治理和本地前端。

## 设计文档

- 架构与方案:`/Users/lhx/.claude/plans/zazzy-moseying-curry.md`
- 开发日志:[`docs/DEVLOG.md`](docs/DEVLOG.md)

## 快速开始

```bash
# 安装依赖
uv sync

# 配置 LLM key(复制模板)
cp config.example.yaml config.yaml
# 编辑 config.yaml 填入 API key

# 运行
uv run mybuddy chat

# 演示前端 + 真实后端
uv run mybuddy web

# QQ 官方机器人渠道(可选)
uv sync --extra qq
uv run mybuddy qqbot
```

## 项目状态

当前是单用户本地可演示版本:

- CLI + Web 前端均可运行。
- 长期记忆使用 `raw/`、`conversations/`、`archive/` 三层文本档案。
- 用户画像包含稳定字段和动态命题,动态命题支持证据时间、置信度、冲突检查和晋升为长期记忆。
- 角色陪伴链路已接入关系记忆、未完成话题和事件式主动消息。

## QQ 机器人接入

QQ 渠道是独立挂件:QQ 只负责收发消息、账号映射、事件去重和调用共享 `ChatService`,
不直接操作 Agent / Memory。详细部署与管理员操作见 [`docs/QQBOT.md`](docs/QQBOT.md)。
