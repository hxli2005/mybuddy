# MyBuddy

生活陪伴型 AI 小伙伴「小布」—— 一个具有长期记忆、情绪感知和主动关怀能力的 AI 陪伴智能体。

借鉴 [NousResearch Hermes Agent](https://github.com/nousresearch/hermes-agent) 的自学习机制，自研 ReAct 主循环、三层文本长期记忆、角色关系编排与动态角色生活状态，配本地 Web 前端。

---

## 功能列表

### 核心对话

- **ReAct Agent 主循环**：多步推理-行动循环，支持工具调用与结果反馈
- **多 LLM 提供商**：支持 Anthropic Claude、OpenAI、OpenRouter、DeepSeek，热切换
- **双模型策略**：主模型负责对话生成，小模型负责记忆抽取、情绪分类等轻量任务
- **Web 搜索自动触发**：检测搜索意图（显式请求 / 时效话题 / 高风险事实），自动联网检索

### 记忆系统

- **短期记忆**：可配置容量的滚动窗口（默认 20 轮）
- **三层长期记忆**：
  - `raw/` — 追加式 JSONL 原始事件
  - `conversations/` — 按日组织的对话 JSONL
  - `archive/` — 带 YAML 前言元数据的 Markdown 记忆卡片
- **混合检索**：中文分词词法搜索 + 可选语义向量召回（OpenAI 兼容 Embeddings API），RRF 融合排序
- **时间感知重排序**：识别用户意图中的"最近/以前"并调整检索排序
- **事实抽取**：每 N 轮（默认 3）由 LLM 自动抽取事实、偏好、人物实体、开放线索
- **记忆治理**：去重、合并、开放线索生命周期管理、用户纠错支持
- **用户画像**：键值对形式的硬事实存储（姓名、生日、偏好、过敏等）

### 情绪系统

- **15 类情绪识别**：焦虑、悲伤、愤怒、疲惫、孤独、压力、内疚、羞耻、恐惧、失望、无聊、平静、喜悦、感激、兴奋
- **情绪滚动窗口**：追踪最近 N 轮连续负面/同类情绪，触发主动关怀
- **情绪支持策略**：为每种情绪预设镜像/需求/引导/小行动四类支持模板

### 安全系统

- **五级危机检测**：NONE → LOW → MEDIUM → HIGH → CRITICAL
  - 双层检测：正则关键词匹配 + 可选 LLM 复核
  - HIGH/CRITICAL 直接返回安全建议 + 危机热线，不经过 LLM 生成
- **内容审核**：
  - 输入审核：拦截有害请求（自杀方法、自伤指令等）
  - 输出审核：扫描并改写诊断声明、药物推荐、替代专业咨询等内容
- **能力边界注入**：系统提示中明确 AI 能做什么（心理教育、情绪支持、应对策略），不能做什么（诊断、处方、替代治疗）
- **内置危机热线**：北京心理危机研究与干预中心、希望 24 热线、生命热线

### 心理健康评估

- **对话式 PHQ-9 / GAD-7 评估**：不显式问卷，自然地融入对话流程
- **按维度独立追踪**：每维度记录 unasked → asked → scored 状态
- **LLM 自动评分**：从对话上下文自动评分（Likert 0-3）
- **评估周期存档**：支持历史趋势查询
- **登录/游客双模式**：登录用户持久化到数据库，游客使用内存存储

### CBT 认知行为疗法

- **五种 CBT 技术**，融入自然对话中，不暴露技术名称：
  - "一起来拆弹" — 认知重构（负面自我评价）
  - "5 分钟小挑战" — 行为激活（低能量/无聊）
  - "烦恼收纳盒" — 焦虑时间（反刍/过度思考）
  - "今日小确幸" — 感恩练习（积极时刻）
  - "感官旅行" — 接地技术（焦虑/恐慌）
- **频率控制**：最少间隔 5 轮，每技术 24 小时冷却
- **危机适配**：高风险场景自动跳过高挑战技术

### 心情追踪

- **自动记录**：每次对话自动从情绪检测结果记录心情
- **手动打卡**：用户自评 0-10 + 可选备注
- **统计分析**：总记录数、连续打卡天数、情绪类别分布、平均分、最佳/最差日
- **趋势图表**：可配置时间范围（默认 30 天）的日平均趋势

### 自学习系统

- **技能注册表**：Markdown + YAML 前言存储，触发器关键词匹配，Laplace 平滑置信度
- **技能策展**：LLM 对复杂交互轨迹（≥3 个工具调用）进行事后反思，自动抽象为可复用技能
- **信心衰减**：低置信度 (<0.5) 不注入，极低置信度 (<0.2) 自动归档
- **反馈总线**：发布/订阅模式，支持 good / bad / fix:xxx / implicit:negative 标签
- **轨迹日志**：完整记录每轮对话的用户输入、AI 回复、工具调用与结果

### 调度与主动关怀

- **每日问候**：固定时间的早安问候（默认 09:17）
- **Dream Job（夜间记忆整理）**：夜间定时执行（默认 02:23）：
  1. 记忆卡片去重（文本相似度）
  2. 从开放线索生成关怀推送
  3. 从共有记忆生成轻量温馨时刻
- **定时提醒**：支持自然语言时间解析（"明天下午三点"），APScheduler 持久化到 SQLite
- **沉默回访**：用户中断对话后定时发送关怀消息（可配置冷却、日上限、静默时段）

### 工具集

| 工具 | 说明 |
|------|------|
| `weather` | 实时天气查询（open-meteo 免费 API，内置主要城市坐标） |
| `web_search` | DuckDuckGo 搜索（ddgs 浏览器指纹，60s 内存缓存） |
| `set_reminder` | 定时提醒（支持 ISO 时间、中文相对时间） |
| `translate` | LLM 翻译（走小模型，无外部 API 依赖） |
| `write_note` | 笔记写入（SQLite + 长期记忆双重存储） |
| `search_notes` | 笔记检索 |
| `recall_memory` | 长期记忆召回 |
| `list_skills` | 已学技能列表 |

### 认证与用户管理

- **Cookie 会话**：HMAC-SHA256 签名，30 天有效期
- **密码哈希**：bcrypt
- **游客模式**：无需注册，localStorage 存储消息
- **游客转登录**：游客消息可导入登录账号
- **用户数据导出**：JSON 格式（心情、评估、CBT、消息）

### Web 前端

- **技术栈**：React 19 + TypeScript + Vite + Tailwind CSS 4 + TanStack React Query
- **页面**：主聊天界面、心情日记（含图表）、评估状态、登录注册
- **特性**：语音输入（MediaRecorder API + 后端 Whisper 转写）、快捷提示、CBT 提示、危机横幅、搜索来源展示、反馈（赞/踩）、设置面板、移动端响应式

### 语音转文字

- **本地离线 Whisper**：基于 openai-whisper，无需外部 API
- **可配置模型**：tiny / base / small / medium / large-v3
- **需要 ffmpeg**：音频处理依赖

---

## 系统架构

```
用户输入 (CLI / Web / QQ)
    │
    ▼
Agent (ReAct 主循环)
    ├── 安全审核 (输入门禁)
    ├── 危机检测 (关键词 + LLM 复核)
    ├── 情绪检测 (LLM 分类)
    ├── 记忆检索 (短期 + 长期混合检索)
    ├── 技能匹配 (自学习技能注册表)
    ├── CBT 机会检测
    ├── 搜索意图检测
    ├── 系统提示组装 (人设 + 记忆 + 情绪 + 技能 + 安全边界)
    ├── ReAct 循环 (LLM 生成 → 工具调用 → 结果反馈 → 继续)
    ├── 安全审核 (输出改写)
    ├── 事实抽取 (后台异步)
    └── 技能策展 (后台异步)
    │
    ▼
响应输出 (控制台 / Web JSON / QQ 消息)
```

---

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | ≥ 3.12 | 需安装 `uv` 包管理器 |
| Node.js | ≥ 20 | 仅开发前端时需要 |
| npm | ≥ 9 | 仅开发前端时需要 |
| ffmpeg | 任意 | 语音转文字功能需要，不启用可忽略 |

---

## 快速开始

### 方式一：Docker 部署（推荐）

无需安装 Python 或 Node 环境。

```bash
# 1) 创建配置文件并填入 LLM API Key
cp config.example.yaml config.yaml
# 编辑 config.yaml:
#   - 填入 llm.api_key
#   - 选择 llm.provider (anthropic/openai/openrouter/deepseek)
#   - 如需改模型，调整 llm.model 和 llm.small_model

# 2) 构建并启动
docker compose up -d --build

# 3) 浏览器打开 http://127.0.0.1:8000
```

数据（记忆/画像/技能/轨迹）持久化在宿主机 `./data/` 目录。

时区默认 `Asia/Shanghai`（角色的时段问候和调度任务依赖它，可在 `docker-compose.yml` 中修改 `TZ` 环境变量）。

**可选：灌入演示数据**

```bash
docker compose exec mybuddy uv run --no-sync python scripts/seed_demo.py
docker compose restart mybuddy
```

这会创建一个演示用户，包含画像、记忆卡片（8 条）、对话历史、提醒（4 条）、笔记（3 条）、技能（4 个含 1 个已归档）和主动消息。

### 方式二：本机开发运行

```bash
# 1) 安装 Python 依赖
uv sync                    # 如需要 API 服务: uv sync --extra api

# 2) 创建配置
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入 llm.api_key

# 3) 启动 Web 服务
uv run mybuddy web         # Web: http://127.0.0.1:8000

# 或命令行对话
uv run mybuddy chat
```

**前端开发**（仅修改前端代码时需要）：

```bash
cd frontend
npm ci
npm run dev               # 启动 Vite 开发服务器 (热更新)
# 开发服务器自动将 /api 代理到 http://127.0.0.1:8000
```

前端构建产物不入库。Docker 构建会自动完成前端编译；本机由 `mybuddy web` 托管静态文件时，需要先 `cd frontend && npm ci && npm run build`。

---

## 配置详解

所有配置集中在 `config.yaml`（从 `config.example.yaml` 复制修改）。支持 `${ENV_VAR}` 语法引用环境变量。

### LLM 提供商 (`llm`)

| 字段 | 类型 | 说明 |
|------|------|------|
| `provider` | `str` | 提供商：`anthropic` / `openai` / `openrouter` / `deepseek` |
| `model` | `str` | 主对话模型名称 |
| `small_model` | `str` | 轻量任务的廉价模型。不填则复用 `model` |
| `api_key` | `str` | API 密钥，支持 `${VAR}` 环境变量 |
| `base_url` | `str\|null` | 自定义 API 地址（OpenAI 兼容中转），留空用默认 |
| `max_tokens` | `int` | 单次生成最大 token 数，默认 2048 |
| `temperature` | `float` | 生成温度，默认 0.7 |

常用提供商默认 Base URL：

| Provider | 默认 base_url |
|----------|--------------|
| `anthropic` | `https://api.anthropic.com` |
| `openai` | `https://api.openai.com/v1` |
| `openrouter` | `https://openrouter.ai/api/v1` |
| `deepseek` | `https://api.deepseek.com` |

### 人设 (`persona`)

定义角色的名字、性格、说话风格、关系模式。字段较多，详见 `config.example.yaml` 中的中文注释。

关键字段：
- `name` — 角色名
- `style` — 整体风格描述
- `tone` — 语气要求
- `boundaries` — 能力边界声明
- `response_habits` — 回复习惯列表
- `roleplay_style` — 角色扮演细节（身份、性格特征、语言风格、微反应、示例对话）
- `character_life` — 角色的动态生活状态
- `relationship_model` — 关系模型（阶段、各维度评分、共有习惯、边界声明）
- `address_user` — 如何称呼用户

### 记忆系统 (`memory`)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `short_term_size` | `int` | 20 | 短期记忆保留消息数 |
| `long_term_top_k` | `int` | 3 | 每次检索返回的记忆卡片数 |
| `extract_after_turns` | `int` | 3 | 每 N 轮对话触发一次事实抽取 |
| `embedding.enabled` | `bool` | false | 是否开启语义向量召回 |
| `embedding.model` | `str` | text-embedding-3-small | embedding 模型名 |
| `embedding.base_url` | `str` | — | embedding API 地址 |
| `embedding.api_key` | `str` | — | embedding API 密钥（留空复用主 LLM） |
| `embedding.batch_size` | `int` | 64 | 向量化批大小 |
| `embedding.rrf_k` | `int` | 60 | RRF 融合参数 |
| `embedding.candidate_multiplier` | `int` | 4 | 各路候选放大倍数 |

语义召回关闭时，检索为纯词法、纯离线、零额外开销。开启后需要 OpenAI 兼容的 embeddings 端点。

### 数据路径 (`paths`)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `data_dir` | `./data` | 数据根目录 |
| `db_file` | `./data/mybuddy.db` | SQLite 数据库路径 |
| `chroma_dir` | `./data/memory` | 长期记忆三层文本存储目录 |
| `skills_dir` | `./data/skills` | 自学习技能存储目录 |
| `trajectories_dir` | `./data/trajectories` | 对话轨迹存储目录 |

### 调度 (`scheduler`)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | true | 是否启用调度器 |
| `daily_greeting` | `str` | "09:17" | 每日问候时间 (HH:MM) |
| `dream_job` | `str` | "02:23" | 夜间 Dream Job 时间 (HH:MM) |
| `quiet_hours.start` | `str` | "23:00" | 静默开始（不推送主动消息） |
| `quiet_hours.end` | `str` | "08:00" | 静默结束 |

### 语音转文字 (`transcription`)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | true | 是否启用本地 Whisper |
| `model` | `str` | base | 模型：`tiny` / `base` / `small` / `medium` / `large-v3` |

模型越大识别越准，但加载越慢、占用内存越多。`base` 适合大多数场景。

### 日志 (`logging`)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `level` | `str` | INFO | 日志级别：DEBUG / INFO / WARNING / ERROR |
| `file` | `str` | `./data/mybuddy.log` | 日志文件路径 |

### 外部工具 (`tools`)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `weather_mock` | `bool` | false | true 时跳过 open-meteo，返回占位数据 |
| `web_search_max_results` | `int` | 5 | 搜索返回结果数上限 |
| `http_timeout` | `float` | 5.0 | HTTP 请求超时（秒） |

### QQ 渠道 (`channels.qq`)

QQ 渠道代码已冻结，不推荐部署。配置项保留供参考：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 QQ Bot |
| `app_id` | QQ Bot App ID |
| `app_secret` | QQ Bot App Secret |
| `sandbox` | 是否使用沙箱环境 |
| `allow_auto_create_user` | 是否自动创建用户 |
| `daily_message_limit` | 每日消息限制 |
| `reply_on_duplicate` | 重复事件是否回复 |

---

## CLI 命令参考

所有命令通过 `uv run mybuddy <command>` 执行。

### 主命令

| 命令 | 说明 |
|------|------|
| `mybuddy version` | 打印版本号 |
| `mybuddy init` | 初始化配置和数据库（首次使用） |
| `mybuddy chat` | 启动命令行交互对话 |
| `mybuddy web` | 启动 Web 服务器 |

`mybuddy web` 选项：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host` | 127.0.0.1 | 绑定地址 |
| `--port` | 8000 | 监听端口 |

### 管理命令

**画像管理** (`mybuddy profile`):

| 子命令 | 说明 |
|------|------|
| `profile show` | 查看所有画像字段 |
| `profile set <key> <value>` | 设置画像字段 |
| `profile unset <key>` | 删除画像字段 |

**提醒管理** (`mybuddy reminders`):

| 子命令 | 说明 |
|------|------|
| `reminders list` | 查看所有提醒 |
| `reminders cancel <id>` | 取消指定提醒 |

**技能管理** (`mybuddy skills`):

| 子命令 | 说明 |
|------|------|
| `skills list` | 查看所有技能 |
| `skills show <name>` | 查看技能详情 |
| `skills archive <name>` | 归档技能 |
| `skills unarchive <name>` | 恢复技能 |

**用户管理** (`mybuddy users`):

| 子命令 | 说明 |
|------|------|
| `users list` | 查看所有用户 |
| `users create <name>` | 创建测试用户 |
| `users bind-qq <qq_id> <user_id>` | 绑定 QQ 账号 |
| `users enable <id>` | 启用用户 |
| `users disable <id>` | 禁用用户 |
| `users quota <id> <limit>` | 设置每日消息限额 |

**Dream Job** (`mybuddy dream`):

| 子命令 | 说明 |
|------|------|
| `dream run` | 手动触发夜间记忆整理 |

### 聊天内快捷键

在 `mybuddy chat` 中：

| 输入 | 作用 |
|------|------|
| `/good` | 对上一轮回复给出正向反馈 |
| `/bad` | 对上一轮回复给出负向反馈 |
| `/fix:说明` | 对上一轮回复给出修正反馈 |

---

## Web API 参考

所有路径前缀为 `/api`。

### 状态与认证

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 系统状态、人设、模型、工具、调度任务 |
| GET | `/api/auth/me` | 当前用户信息 |
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/logout` | 登出 |
| DELETE | `/api/auth/account` | 删除账号 |

### 聊天

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 发送消息，获取 AI 回复 |
| POST | `/api/chat/reset` | 重置会话上下文 |
| POST | `/api/feedback` | 提交反馈 (good/bad/fix) |
| GET | `/api/messages` | 获取聊天历史 |
| POST | `/api/messages/import` | 游客消息导入已登录账号 |

### 记忆

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/memory` | 列出所有记忆卡片 |
| PATCH | `/api/memory/archive/{id}` | 修改记忆卡片 |
| DELETE | `/api/memory/archive/{id}` | 删除记忆卡片 |

### 画像

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/profile` | 获取所有画像字段 |
| PATCH | `/api/profile/fields/{key}` | 更新画像字段 |
| DELETE | `/api/profile/fields/{key}` | 删除画像字段 |

### 笔记

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/notes` | 列出笔记 |
| POST | `/api/notes` | 创建笔记 |
| PATCH | `/api/notes/{id}` | 更新笔记 |
| DELETE | `/api/notes/{id}` | 删除笔记 |

### 提醒

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/reminders` | 列出提醒 + 待推送消息 |
| PATCH | `/api/reminders/{id}` | 取消提醒 |

### 技能

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/skills` | 列出所有技能 |
| PATCH | `/api/skills/{name}` | 归档/恢复技能 |

### 心情

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/mood` | 获取心情记录 |
| GET | `/api/mood/trends` | 心情趋势（日平均） |
| GET | `/api/mood/stats` | 心情统计 |
| POST | `/api/mood/checkin` | 手动心情打卡 |

### 评估

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/assessment/status` | 评估维度状态 |
| GET | `/api/assessment/history` | 评估周期历史 |
| DELETE | `/api/assessment/status` | 重置评估周期 |

### CBT

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cbt/status` | CBT 事件历史 |

### 安全

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/safety/resources` | 危机热线信息 |

### 语音

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/transcribe` | 上传音频，返回转写文本 |

### 用户数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/user/export` | 导出用户数据 (JSON) |
| DELETE | `/api/user/data` | 清除用户数据 |

### 管理员

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/users` | 用户列表 |
| POST | `/api/users` | 创建测试用户 |
| PATCH | `/api/users/{id}` | 更新用户状态/限额 |

---

## 前端开发

前端为 React SPA，源码在 `frontend/` 目录。

### 目录结构

```
frontend/
  src/
    main.tsx              # 入口
    App.tsx               # 根组件 (HashRouter)
    components/           # 通用组件
      Shell.tsx           # 布局框架 + 导航
      Sheet.tsx           # 可复用抽屉组件
      ui.tsx              # UI 原语 (Chip, EmptyState, TypingDots)
      ChatCbtPrompt.tsx   # CBT 技巧提示
      ChatCrisisBanner.tsx# 危机干预横幅
      CrisisPanel.tsx     # 危机资源面板
      MoodChart.tsx       # 心情图表
      MoodTrends.tsx      # 趋势可视化
      CheckInDialog.tsx   # 心情打卡对话框
      SettingsSheet.tsx   # 设置面板
      SafetyDisclaimer.tsx# 安全声明
      GuestBanner.tsx     # 游客提示横幅
      UserMenu.tsx        # 用户菜单
    views/
      ChatView.tsx        # 主聊天界面
      MoodDiary.tsx       # 心情日记
      AssessmentStatus.tsx# 评估状态
      LoginView.tsx       # 登录注册
      settings/           # 设置子模块
    lib/
      api.ts              # API 请求层
      auth.tsx            # 认证 Context + Provider
      router.tsx          # Hash 路由
      queryKeys.ts        # React Query 键名
      guestStorage.ts     # 游客 localStorage 操作
      useMediaRecorder.ts # 语音录制 Hook
      cn.ts               # className 合并工具
    types/
      api.ts              # API 类型定义
```

### 开发命令

```bash
cd frontend

npm ci              # 安装依赖
npm run dev         # 启动 Vite 开发服务器 (127.0.0.1:5173)
npm run build       # 生产构建 (输出到 ../dist/)
npm run preview     # 预览生产构建
```

Vite 开发服务器自动将 `/api/*` 代理到 `http://127.0.0.1:8000`。

### 增加新页面

1. 在 `frontend/src/views/` 创建页面组件
2. 在 `frontend/src/lib/router.tsx` 中添加路由
3. 在 `frontend/src/components/Shell.tsx` 中添加导航入口（如需要）

---

## 数据说明

### 数据目录结构

运行后在 `./data/` 下生成：

```
data/
  mybuddy.db              # SQLite 主数据库
  mybuddy.log             # 运行日志
  memory/                 # 长期记忆三层存储
    raw/                  # 原始事件 JSONL
    conversations/        # 按日对话 JSONL
    archive/              # 记忆卡片 Markdown
    vectors.db            # 语义向量索引 (仅开启 embedding 时)
  skills/                 # 自学习技能 Markdown
  trajectories/           # 对话轨迹 JSONL
```

### 数据库表

| 表名 | 用途 |
|------|------|
| `users` | 用户账号，bcrypt 密码哈希，游客标志 |
| `external_accounts` | 外部渠道账号绑定 |
| `inbound_events` | 外部事件去重 |
| `user_usage` | 每日消息配额追踪 |
| `messages` | 对话消息（user/assistant/tool/system） |
| `reminders` | 定时提醒 |
| `pending_messages` | 主动消息队列（问候/关怀/提醒） |
| `profile_fields` | 用户画像键值对 |
| `notes` | 用户笔记 |
| `mood_records` | 心情记录 |
| `safety_events` | 安全事件日志 |
| `assessment_dimensions` | PHQ-9 / GAD-7 各维度状态 |
| `assessment_cycles` | 已完成评估周期存档 |
| `cbt_events` | CBT 技术使用记录 |
| `chat_sessions` | 会话管理（chat/cbt/diary） |
| `apscheduler_jobs` | APScheduler 任务持久化（自动创建） |

---

## 测试与评测

### 运行测试

```bash
uv sync --extra dev       # 安装开发依赖 (pytest, ruff)
uv run pytest             # 运行全部 29 个测试文件
```

测试覆盖：Agent 主循环、安全门禁、情绪检测、记忆系统、技能学习、工具调用、调度器、API、认证、CLI 管理命令、LLM Provider。

### 评测框架

评测代码在 `eval/` 目录，详见 [`eval/README.md`](eval/README.md)。

- **长期记忆召回**：自建中文数据集（37 张记忆卡片、52 个查询、4 种查询类型），Hit@k / MRR / Recall@k 指标
- **LoCoMo 公开基准**：通用记忆评测（`locomo_eval.py`、`locomo_extract.py`、`locomo_granularity.py`）
- 评测结果与方法见 [`eval/RESULTS.md`](eval/RESULTS.md)

---

## 项目结构

```
mybuddy-main/
  mybuddy/                  # Python 后端
    agent/                  # ReAct Agent 主循环
    llm/                    # LLM Provider 抽象 (Claude/OpenAI/Whisper)
    memory/                 # 三层长期记忆 + 画像
    emotion/                # 情绪检测与支持策略
    learning/               # 自学习 (技能/轨迹/反馈/Dream Job)
    scheduler/              # APScheduler 定时任务
    tools/                  # 工具注册表 + 内置工具
    safety/                 # 安全系统 (危机检测/审核/边界)
    therapy/cbt/            # CBT 认知行为疗法
    assessment/             # 心理健康评估 (PHQ-9/GAD-7)
    mood/                   # 心情追踪
    storage/                # SQLAlchemy ORM + 消息/队列/用户存储
    auth/                   # 认证 (Cookie/bcrypt/游客)
    session/                # 会话管理
    services/               # 聊天服务
    channels/               # 外部渠道 (QQ，已冻结)
    cli.py                  # CLI 入口 (Typer)
    cli_admin.py            # 管理命令
    api.py                  # FastAPI 应用
    web.py                  # stdlib HTTP Server 备选
    config.py               # 配置加载 (Pydantic + YAML)
  frontend/                 # React + TypeScript Web 前端
    src/
      components/           # 通用组件
      views/                # 页面视图
      lib/                  # 工具库 (API/认证/路由/查询)
      types/                # 类型定义
  tests/                    # pytest 测试 (29 个文件)
  eval/                     # 评测框架与数据集
  scripts/
    seed_demo.py            # 演示数据灌入脚本
  config.example.yaml       # 配置文件模板
  pyproject.toml            # Python 项目定义
  Dockerfile                # Docker 多阶段构建
  docker-compose.yml        # Docker Compose 编排
```
