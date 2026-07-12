# MyBuddy

生活陪伴型 AI 小伙伴「小布」。借鉴 [NousResearch Hermes Agent](https://github.com/nousresearch/hermes-agent) 的自学习机制,自研 ReAct 主循环、三层文本长期记忆、角色关系编排与动态角色生活状态,配本地 Web 前端。

- 开发日志:[`docs/DEVLOG.md`](docs/DEVLOG.md) · 项目报告:[`docs/项目报告.md`](docs/项目报告.md) · 评测:[`eval/README.md`](eval/README.md) · 小布桌宠 v1:[`docs/VPET_V1_KICKOFF.md`](docs/VPET_V1_KICKOFF.md) · O1 历史接入:[`docs/VPET.md`](docs/VPET.md)

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
uv sync --extra api                    # 需 Python 3.12+ 与 uv
cp config.example.yaml config.yaml     # 填入 api_key

uv run --extra api mybuddy web         # Web:http://127.0.0.1:8000
uv run mybuddy chat                    # 或命令行对话
```

## 小布桌宠（Windows）

`buddyshell/` 是 v1 的 WPF 桌宠壳。先启动 MyBuddy Web 后端，再运行：

```powershell
.\scripts\start_mybuddy_web.ps1
# 另开一个 PowerShell；仓库本地 SDK 可避免系统未安装 dotnet SDK
.\.dotnet-sdk\dotnet.exe run --project .\buddyshell\BuddyShell.csproj
```

开发机若已安装 VPet，会自动查找默认宠物素材；也可设置
`BUDDYSHELL_PET_ROOT` 指向 `0000_core/pet/vup`。发布包由
`scripts/package_buddyshell.ps1` 生成。

动画状态机回归、安装素材校验和 V1–V10 截图证据可用以下命令重跑：

```powershell
$env:BUDDYSHELL_PET_ROOT="D:\steam\steamapps\common\VPet\mod\0000_core\pet\vup"
.\.dotnet-sdk\dotnet.exe run --project .\buddyshell.Tests\BuddyShell.Tests.csproj -c Release -- --assets
```

桌宠动画素材版权归[虚拟主播模拟器制作组](https://github.com/LorisYounger/VPet)，
本项目仅按非商用条件使用；商业化前须另行取得授权。

六拍验收时按拍采集只读证据（初始结果固定为 `FAIL`，不会自动冒充通过）：

```powershell
uv run python scripts/vpet_acceptance_capture.py --beat 3
uv run python scripts/vpet_weekly_check.py
uv run python scripts/vpet_acceptance_finalize.py
uv run python scripts/vpet_acceptance_verify.py --root eval/acceptance/v1
```

前端产物不入库:要本机由 `mybuddy web` 托管页面,先 `cd frontend && npm ci && npm run build`(仅改前端时需要;Docker 构建自动完成这步)。

## 项目状态

单用户本地版本,毕业设计研究载体(研究方向:陪伴 AI 的"人味"——构成、实现与测量):

- 分层长期记忆:`raw/ conversations/ archive/` 三层文本档案 + 可选语义召回(词法+向量 RRF 融合);
- 角色生活状态按真实信号动态合成(距上次对话间隔 / 时段 / 最近话题),非写死文案;
- 情绪识别与主动关怀(定时问候 / 沉默回访 / 夜间记忆整理 Dream Job);
- 自生长技能(Markdown 存储,置信度随反馈升降,连败自动归档);
- 评测:自建中文召回集 + LoCoMo 公开基准,结果与方法见 [`eval/RESULTS.md`](eval/RESULTS.md)。

桌宠 v1 当前按 2026-08-01 硬交付施工:Windows 独立 WPF 壳负责渲染与传感,
MyBuddy 引擎统一负责生理、人格、记忆、时机和遥测。产品形态、桥协议、验收与实验口径
已在 [`docs/VPET_V1_KICKOFF.md`](docs/VPET_V1_KICKOFF.md) 所列规格包中冻结。

## QQ 机器人(冻结)

QQ 渠道代码保留但当前不维护、不推荐部署(项目聚焦毕设研究)。历史文档见 [`docs/QQBOT.md`](docs/QQBOT.md)。
