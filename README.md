# MyBuddy

生活陪伴型 AI 小伙伴「小布」。借鉴 [NousResearch Hermes Agent](https://github.com/nousresearch/hermes-agent) 的自学习机制,自研 ReAct 主循环、三层文本长期记忆、角色关系编排与动态角色生活状态,配本地 Web 前端。

- 开发日志:[`docs/DEVLOG.md`](docs/DEVLOG.md) · 项目报告:[`docs/项目报告.md`](docs/项目报告.md) · 评测:[`eval/README.md`](eval/README.md)

## 部署方式一:Docker(推荐,无需 Python/Node 环境)

```bash
# 1) 配置 LLM key(OpenRouter / Anthropic / DeepSeek 任一)
cp config.example.yaml config.yaml
# 编辑 config.yaml,填入 llm.api_key

# 2) 构建并启动
docker compose up -d --build

# 3) 打开 http://127.0.0.1:8000
```

数据(记忆/画像/技能/轨迹)持久化在宿主机 `./data/`;时区默认 Asia/Shanghai(角色的时段问候依赖它,可在 compose 里改)。

```bash
# 可选:灌入一套演示数据(画像/记忆/对话/提醒/笔记/技能,覆盖全部面板)
docker compose exec mybuddy uv run --no-sync python scripts/seed_demo.py
docker compose restart mybuddy
```

## 部署方式二:本机运行(开发)

```bash
uv sync                                # 需 Python 3.12+ 与 uv
cp config.example.yaml config.yaml     # 填入 api_key

uv run mybuddy web                     # Web:http://127.0.0.1:8000
uv run mybuddy chat                    # 或命令行对话
```

前端产物不入库:要本机由 `mybuddy web` 托管页面,先 `cd frontend && npm ci && npm run build`(仅改前端时需要;Docker 构建自动完成这步)。

## 项目状态

单用户本地版本,毕业设计研究载体(研究方向:陪伴 AI 的"人味"——构成、实现与测量):

- 分层长期记忆:`raw/ conversations/ archive/` 三层文本档案 + 可选语义召回(词法+向量 RRF 融合);
- 角色生活状态按真实信号动态合成(距上次对话间隔 / 时段 / 最近话题),非写死文案;
- 情绪识别与主动关怀(定时问候 / 沉默回访 / 夜间记忆整理 Dream Job);
- 自生长技能(Markdown 存储,置信度随反馈升降,连败自动归档);
- 评测:自建中文召回集 + LoCoMo 公开基准,结果与方法见 [`eval/RESULTS.md`](eval/RESULTS.md)。

## QQ 机器人(冻结)

QQ 渠道代码保留但当前不维护、不推荐部署(项目聚焦毕设研究)。历史文档见 [`docs/QQBOT.md`](docs/QQBOT.md)。
