# MyBuddy 项目汇报

> 面向技术负责人的口头汇报提纲。正文是纸面结构,`>` 引用块是口头 talking points,可以对着直接讲。预计 15–20 分钟讲完。

---

## 0. 一句话定位

生活陪伴型 AI 小伙伴。不是 ChatGPT 包装,是一个**记得你、懂你、会主动关心你**的本地 Agent,用工程化方式复现了 NousResearch Hermes Agent 的四要素自学习机制,核心代码自研。

> 我做了一个能长期陪伴的 AI 助手,跑在本地,一轮聊完会记得你说过什么;失败过的做法会下次避免;夜里还会自己整理一遍记忆。这份汇报讲一下做到哪了、为什么这么做、下一步建议。

---

## 1. 项目是什么(30 秒电梯陈述)

- Python 3.12 + CLI + 本地 Web 前端 + SQLite / 文本 archive 本地存储
- 技术路线:**Hermes tool-call 协议 + ReAct 循环 + 自研分层记忆 + 关系编排 + 动态命题治理**
- 当前是单用户本地可演示版本,日常可自用

> 这东西跑在我自己机器上,每天能用。底座是 Claude API,LLM 层做了 provider 抽象,未来本地 Hermes 模型(vLLM / llama.cpp)零改动就能切。

---

## 2. 为什么做这个

**产品判断**
- 市面 AI 助手绝大部分是单轮工具型,**缺人格连贯和长期记忆**,聊到第二天就像失忆
- "情感陪伴 + 日程提醒"是真实、高频、个人化的场景
- 给自己用 → 闭环反馈 → 能持续演化

**技术判断**
- Hermes / OpenManus / Letta / mem0 这些开源项目**思路值得借鉴,但直接 fork 的成本大于收益**:基础设施可以直接用(APScheduler / SQLite / 文本档案),核心循环自研保证可控
- 选 Python + CLI 不是因为简单,是因为**交互链路最短,测试最容易**

> 不想再做一个 ChatGPT 套壳。差异点不是 prompt,是**记忆结构 + 主动性 + 反馈回流** —— 这三件事决定了它是不是"一个小伙伴",而不是"又一个聊天框"。

---

## 3. 做到哪了

**里程碑**(都有 `docs/DEVLOG.md` 开发日志,含决策 / 踩坑 / 验证):

| 里程碑 | 内容 |
|---|---|
| M1 基础骨架 | Provider 抽象 + SQLite + CLI |
| M2 Agent 闭环 | ReAct 循环 + 工具注册表 + trajectory 采集 |
| M3 分层记忆 | 短期 + raw/conversations/archive 文本长期记忆 + 混合画像 + 事实自抽取 |
| M4 调度 + Dream Job | APScheduler + 每日早安 + 夜间五件事 |
| M5 情绪 + 反馈总线 | 情绪分类 + consecutive-negative nudge + FeedbackBus |
| M6 Skills 子系统 | 自生长 skill + 自动归档 + curator 复盘 |
| M7 MVP 收尾 | 8 个工具 + 3 组 admin CLI + 本地 Web 管理界面 |
| 关系/记忆治理 | 乙女式关系编排 + 关系记忆 + 动态命题生命周期与晋升 |

**关键数字**
- 当前测试基线:**162 个测试用例全绿**
- 8 个工具:`weather / set_reminder / recall_memory / write_note / search_notes / translate / web_search / list_skills`
- 7 个顶级命令:`version / init / chat / dream / profile / reminders / skills`

> 七个里程碑全闭环,测试全绿。代码和测试行数接近 2:1,不是放羊写出来的。

---

## 4. 技术亮点(挑 4 个讲)

### (a) 分层记忆

- **短期** `deque(maxlen=N)` — 滚动窗口
- **长期** `raw/` + `conversations/` + `archive/` 文本档案 — 可追溯、可人工审查
- **用户画像** = 核心字段(hard facts,SQLite KV) + 动态命题(soft claims,带置信度、证据时间、冲突关系和晋升状态,**同步写入 archive**)

命题的 `confidence` 随证据增减,近期证据 `+0.05` / 无近期证据 `-0.05`,低于 0.3 被 Dream Job 归档;满足证据数、跨日证据、置信度和无冲突条件后,会晋升为长期记忆或关系规则。

> 不做"一个大 vector DB 装一切"—— 因为结构化字段(名字、过敏、生日)就该是 KV,推测性的"这人可能周日情绪低"才是带置信度的命题。两种东西混在一起的系统最后都会崩。

### (b) 自学习 Skills 子系统

- Skill = `data/skills/<name>.md`,frontmatter + 步骤,人类可读可手改
- Agent 一轮对话里 **工具调用 ≥3 次 且收敛成功** → 异步让 LLM 复盘是否抽象成 skill(起步 confidence=0.3)
- 匹配注入 system prompt,使用后根据 `/good /bad` 反馈更新计数;置信度用 Laplace 平滑 `success / (success + fail + 1)`
- 连续失败 → 自动 `archived: true`,不删文件,可人工 `mybuddy skills unarchive` 恢复

> 这块是整个项目最像"活的"的部分:用户不做任何事,系统自己在总结"什么情况下该怎么做";做对了加分,做错了扣分,烂到一定程度自己下线。

### (c) 反馈回流闭环

- 一条 `FeedbackEvent` → `FeedbackBus` 广播到三类订阅者:
  - trajectory 写 label 文件(为未来微调留数据)
  - 画像命题调置信度(好的 +0.05,差的 -0.1,负向大于正向 —— 错得离谱比说对更值得更新)
  - skill 计数更新
- 同步 pub/sub,订阅者失败隔离

> 大部分助手"用户说好 / 说不好",系统就只能点个头。这里 `/good /bad /fix` 是真的回流到**画像 + skill + 轨迹**三条线,每条线都能独立验证到变化。

### (d) LLM Provider 抽象 + 本地模式预留

- 唯一抽象点:`BaseLLMProvider.generate(messages, tools, *, system, ...)`
- **system prompt 走独立参数而非塞 messages** —— 对本地小模型更友好,Hermes 原生 XML tool-call 协议也是这样设计的
- 云端 Claude provider 内部做格式转换,外部接口统一
- 切本地 Hermes 时,只加一个 provider 实现,业务代码零改动

> Provider 层这块看起来平平无奇,但它是未来能把 LLM 成本降下去的关键锚点 —— 现在调的是 Anthropic API,数据够了之后微调本地 Hermes 顶上,上层不用动。

---

## 5. 工程质量

- **分层清晰**:interface(CLI) / orchestrator(Agent) / LLM / tools / memory / scheduler / learning / storage / emotion,每层独立目录,**每个核心文件 < 600 行**
- **测试覆盖**:
  - `ScriptedProvider` —— 脚本化 LLM 响应,不连真实 API
  - `MockEmbedding` —— 128 维确定性 hash,绕开 BGE-M3 下载(~2GB)
  - `httpx.MockTransport` —— 伪造 open-meteo / DuckDuckGo 响应
  - `typer.testing.CliRunner` —— 子命令端到端
- **容错**:外部 API 全部有降级;LLM 失败不阻塞主对话;画像以 SQLite 为 source of truth,archive 作为可检索档案;Dream Job 每一步 try/except 隔离
- **可观测**:trajectory 全量 JSONL 按天落盘;emotion / triggered_skills / related_claim_ids 都写到轨迹 meta
- **配置外置**:YAML + `${ENV_VAR}` 展开;`tools.weather_mock` / `api_key` 等开关分离

> 工程上的几个原则:外部依赖全部可 mock、失败不崩只降级、关键状态全写文件可复盘。这让我敢在凌晨跑 Dream Job,也敢第二天看轨迹找问题。

---

## 6. 演示路径(现场想给导师看什么)

1. **`uv run mybuddy init`** — 初始化配置和数据库
2. **`uv run mybuddy chat`** 交互一轮:
   - 说"我叫 XX,爱喝手冲咖啡" → 事实抽取到 `profile_fields` 和长期记忆
   - 说"明天 10 点提醒我开会" → `reminders` 表写入 + scheduler 注册 job
   - 连说两句"有点累""心情不好" → 情绪链路连续两轮 negative → `pending_messages` 里出现 30 分钟后的关怀 nudge
   - `/good` 对上一轮打标 → 看 `trajectory.labels.jsonl` + `skill success_count+1`
3. **`uv run mybuddy dream run`** — 手工触发夜间五件事,打印 `DreamReport`(合并/更新/冲突/洞察/nudge 计数)
4. **`uv run mybuddy profile show` / `skills list` / `reminders list`** — 所有状态都有 CLI 可观测,不用 sqlite3

> 演示顺序按"先看能对话,再看能记住,再看能主动关心,最后看能观察管理"—— 一条 demo 串起来整个系统。

---

## 7. 风险 & 未覆盖

- **LLM 调用成本**:每轮主对话 + 情绪分类(small model),再加偶发的 extractor / curator / dream job。量级可控但会持续计费 —— 所以留了本地化的 Provider 抽象
- **BGE-M3 首次加载 ~2GB**:真机第一次启动慢,后续缓存 OK;已有测试用 MockEmbedding 绕开
- **单进程 CLI 假设**:多用户 / 并发场景没设计;skill 的 `.md` 文件并发写有风险
- **抄思路不 fork 的代价**:上游 bugfix 享受不到,换来的是可控小内核
- **多用户没做**:当前仍是单用户本地流程;多用户、多角色、权限隔离和并发写入还没有设计

> 不是所有东西都做了。该做但没做的事情我都写在这里,不藏。

---

## 8. 下一步选项 & 推荐

| 选项 | 内容 | 预估 | 前置条件 |
|---|---|---|---|
| **A. HTTP API** | FastAPI 骨架 `POST /chat` + `GET /memory/search`,接入上层业务 / 前端 | 1–2 周 | 无 |
| **B. 本地 Hermes 适配** | vLLM 或 llama.cpp 加一个 provider,LoRA 微调 | 2–3 周 | 数据量够(见 D) |
| **C. 工具生态扩展** | 日历 / 邮件 / 健康数据 / Webhook 入站 | 按业务排 | 业务方向明确 |
| **D. 轨迹导出 + 微调** | JSONL → DPO/SFT 数据集 | 1 周 + 数据积累 | 每日真实使用,轨迹积累到万条 |

**推荐路径**:**A → D → B**

- 先 A,让更多入口(移动端 / 浏览器)能连上,**快速放大真实轨迹产出**
- 积累够数据再 D,把 `/good /bad /fix` 这条反馈链的价值变现成模型层面的改进
- 最后 B,用自家数据微调的 Hermes 替换云端 LLM,把单轮成本降到近零

> 如果让我挑一件事先做,是 HTTP API。理由是:真正决定这个系统进化速度的不是多一个工具、多一层微调,而是**多几个真实用户每天用它**。API 是把"一个人自用"变成"少数人试用"的必要一步。

---

## 附:目录速查(方便导师事后看代码)

```
mybuddy/
├── agent/       ReAct 主循环 + context 构建
├── llm/         Provider 抽象 + Claude 实现
├── tools/       8 个工具 + registry + runtime context
├── memory/      分层记忆(short_term / long_term / profile / manager / extractor)
├── learning/    trajectory + feedback + dream + skills + skill_curator
├── emotion/     detector + tracker
├── scheduler/   APScheduler 封装 + job 定义
├── storage/     SQLAlchemy 模型 + DB + 主动消息队列
├── cli.py       主 CLI(chat / init / version / dream)
└── cli_admin.py profile / reminders / skills 管理命令

docs/
├── DEVLOG.md    七个里程碑的完整开发日志(重点看这个)
└── REPORT.md    本文件
```
