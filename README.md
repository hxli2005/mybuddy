# MyBuddy

生活陪伴型 AI 小伙伴。借鉴 [NousResearch Hermes Agent](https://github.com/nousresearch/hermes-agent) 的自学习机制 + 自研分层记忆系统。

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
```

## 项目状态

CLI 形态 MVP 已完成,并提供本地演示前端。
