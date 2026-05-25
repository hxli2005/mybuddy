# MyBuddy 开发日志

本文档按时间顺序记录 MyBuddy 项目的开发过程:每个里程碑完成后追加一节,包含**目标、关键决策、实现要点、遇到的问题、验证结果、下一步**。

项目设计方案见 `/Users/lhx/.claude/plans/zazzy-moseying-curry.md`,本日志只记录"发生了什么"不重复"为什么这么设计"。

---

## 项目初始化(2026-05-08)

- 工作目录: `/Users/lhx/code/mybuddy`(非 git 仓库)
- 创建了目录骨架:
  ```
  mybuddy/
  ├── docs/              # 开发文档
  ├── data/              # 运行时数据(后续 gitignore)
  ├── mybuddy/           # 主代码包
  │   ├── agent/         # Agent 核心循环
  │   ├── llm/           # LLM Provider 适配
  │   ├── tools/         # 工具注册表与实现
  │   ├── memory/        # 分层记忆
  │   ├── scheduler/     # 定时任务
  │   ├── emotion/       # 情绪感知
  │   ├── learning/      # 自学习子系统(借鉴 Hermes Agent)
  │   └── storage/       # 数据模型与 DB
  └── tests/             # 单元/集成测试
  ```
- 任务清单见 TaskList,共 7 个里程碑:
  - M1 基础骨架 → M2 Agent 闭环 → M3 记忆系统 → M4 调度/Dream → M5 情绪/反馈 → M6 Skills → M7 工具扩展

---

## M1 基础骨架(2026-05-08 完成)

**目标**:项目初始化、可 import、可读配置、LLM Provider 抽象 + 至少一个实现、DB 初始化、CLI 骨架可运行。

**实现要点**
- **LLM Provider 抽象**:`mybuddy/llm/base.py` 用 pydantic 定义 `Message / Role / ToolSpec / ToolCall / LLMResponse`,`BaseLLMProvider.generate(messages, tools, *, system, ...)` 为唯一抽象方法。system prompt 走独立参数,messages 不塞 system——这是为未来本地 Hermes 保留最干净的接口。
- **Claude Provider**:`mybuddy/llm/claude.py` 用 `AsyncAnthropic`,`_to_anthropic_message` 处理 user/assistant 直出、tool 转为 `tool_result` block;`_from_anthropic_response` 遍历 `content` blocks 合成文本 + 抽 `tool_use`。`make_provider(cfg.llm)` 按 `cfg.provider` 分派,M1 只认 anthropic,其它抛 `NotImplementedError`。
- **存储层**:`mybuddy/storage/models.py` 用 SQLAlchemy 2.x `DeclarativeBase` + `Mapped` 定义 5 张表(messages / reminders / pending_messages / profile_fields / profile_claims),字段保持最小,对应表留到后续里程碑填;`db.py` 提供 `make_engine / init_db / session_scope`(上下文管理器带 commit/rollback)。
- **CLI**:`mybuddy/cli.py` 用 typer,`version` / `init` / `chat` 三个子命令。`init` 幂等:拷贝 `config.example.yaml → config.yaml`(已存在则跳过,除非 `--force`)→ `ensure_dirs` → `init_db`。`chat` 为 M1 占位。

**文件清单(本轮新增/修改)**
- `mybuddy/llm/base.py`(新)
- `mybuddy/llm/claude.py`(新)
- `mybuddy/llm/__init__.py`(导出统一命名)
- `mybuddy/storage/models.py`(新)
- `mybuddy/storage/db.py`(新)
- `mybuddy/storage/__init__.py`(扩展导出)
- `mybuddy/cli.py`(新)

**验证结果**
- `uv sync` 成功装齐依赖(anthropic 0.80+、sqlalchemy 2.0、typer 0.25、chromadb、apscheduler 等)。
- `uv run mybuddy version` → `mybuddy 0.1.0` ✅
- `uv run mybuddy init` → 生成 `config.yaml`、创建 `data/`、建 5 张表 ✅
- `sqlite3 data/mybuddy.db '.tables'` 显示:`messages / pending_messages / profile_claims / profile_fields / reminders` ✅
- `make_provider(cfg.llm)` 返回 `ClaudeProvider` ✅(不调用真实 API,仅构造)
- `uv run mybuddy chat` 按预期打印 M1 占位提示 ✅

**踩坑**
- `mybuddy/llm/__init__.py` 存在一版旧命名(`LLMProvider / ToolDef / build_provider`),本轮统一为 `BaseLLMProvider / ToolSpec / make_provider`,避免两套命名混用。

**下一步 — M2 Agent 最小闭环**
- `agent/core.py` ReAct 循环 + `agent/context.py` prompt 构建
- `tools/registry.py` decorator 注册 + JSON Schema 自动生成
- 首批 2 个工具:`weather`、`set_reminder`(后者写入 reminders 表)
- CLI `chat` 子命令实现:多轮交互 + 短期记忆窗口
- **同步上线** `learning/trajectory.py`,从第一轮对话就采集轨迹(设计要求第一天就采)

---

## M2 Agent 最小闭环(2026-05-08 完成)

**目标**:跑通 `user_input → LLM → 工具调用 → 工具结果回灌 → LLM 收敛 → 文本回复` 的完整 ReAct 循环,CLI 可交互多轮,同时从第一轮开始采集轨迹。

**实现要点**
- **工具注册表 `tools/registry.py`**:`@tool(name=, description=)` 装饰器注册到 `ToolRegistry` 实例,默认写入全局单例;`_build_tool_spec` 用 `inspect.signature + get_type_hints` 反射生成 JSON Schema(支持 `str/int/float/bool/list[...]/Optional`),无默认值即为 `required`。`execute(name, args)` 总是返回字符串(出错走 `{"error": ...}` JSON),LLM 侧统一吃文本 tool_result。同时支持同步和 async 工具。
- **工具运行时上下文 `tools/context.py`**:工具函数想用 DB/config 时通过 `get_engine()/get_config()` 取。CLI 启动时 `set_context(engine=..., config=...)` 注入一次。这样工具函数签名保持纯净(LLM 友好),进程级依赖通过上下文解决。
- **内置工具**
  - `weather(city)` — M2 用 mock 数据,M7 接真实 API
  - `set_reminder(content, time)` — dateutil 解析 ISO / `YYYY-MM-DD HH:MM`,写 `reminders` 表。中文自然语言(如"明天下午3点")由 LLM 负责先换算成 ISO
- **Agent `agent/core.py`**:`Agent.run(user_input)` 维护 `deque(maxlen=cfg.memory.short_term_size)` 短期记忆;主循环 `for step in range(max_steps)` —— LLM generate → 有 tool_call 就 `execute` 并把结果回写为 `Role.TOOL` 消息 → 否则收敛。Anthropic 协议要求 `tool_use` 后必须紧跟 `tool_result`,记忆顺序就是天然对齐的。`max_steps` 未收敛返回 `finish_reason="max_steps"`。
- **`agent/context.py`**:目前只拼人设 system prompt 和透传短期窗口,保留为独立层是为了 M3 在同一个函数里接入长期记忆检索 + 画像注入。
- **TrajectoryLogger `learning/trajectory.py`**:每个 turn 一条 JSON line,按天写 `data/trajectories/YYYY-MM-DD.jsonl`,包含 `system / user_input / steps[...tool_calls, tool_results] / final_response / finish_reason / outcome_label`。`attach_label(turn_id, label)` 不改原始行,另写 `YYYY-MM-DD.labels.jsonl`,保留完整证据链,合并交给未来的离线导出管线。
- **CLI `chat`**:rich prompt 循环,`/exit` 退出,`/good | /bad | /fix <修正>` 给上一轮标 label。缺 `api_key` 直接报错退出,避免后续真实调用时踩坑。

**文件清单(本轮新增)**
- `mybuddy/tools/registry.py`、`mybuddy/tools/context.py`、`mybuddy/tools/weather.py`、`mybuddy/tools/reminder.py`、`mybuddy/tools/__init__.py`(导入副作用注册内置工具)
- `mybuddy/agent/core.py`、`mybuddy/agent/context.py`、`mybuddy/agent/__init__.py`
- `mybuddy/learning/trajectory.py`、`mybuddy/learning/__init__.py`
- `mybuddy/cli.py`(`chat` 实现)
- `tests/test_tools.py`、`tests/test_agent.py`、`tests/test_trajectory.py`

**验证结果**
- `uv run pytest tests/` → 10 passed(tools 6 / agent 2 / trajectory 2) ✅
- `uv run mybuddy chat --help` 正确打印 `/exit /good /bad /fix` 说明 ✅
- Agent 循环测试用 `ScriptedProvider` 验证了两条关键路径:
  - tool_use → tool_result 回灌 → stop(第二次 LLM 调用确实能看到 `Role.TOOL` 消息)
  - 无限 tool_call 触发 `max_steps` 熔断

**踩坑**
- `Agent` 循环里即使 `resp.text` 为空也要把 assistant 消息追加到短期记忆,否则后续的 `tool_result` 会"悬空"(没有前序 assistant 承接),违反 Anthropic 协议。现在统一用 `content=""` 占位,Anthropic SDK 接受。
- `tools/__init__.py` 通过 `from . import weather, reminder` 触发装饰器副作用注册,这种 import-side-effect 模式对 ruff B 规则比较不友好,用 `# noqa: F401` 抑制。M7 工具扩张后考虑改为显式注册函数。
- `datetime.utcnow()` 在 py3.12 已 Deprecated,目前还在多处沿用(models/trajectory/reminder),暂记录不修,等 M3 做 DB 层改造时一次性迁移到 `datetime.now(UTC)`。

**下一步 — M3 记忆系统**
- `memory/long_term.py` Chroma + BGE-M3 嵌入(首次加载慢,启动时异步预热)
- `memory/profile.py` 核心字段 + 动态命题集
- `memory/extractor.py` 每 N 轮 LLM 自动抽取事实
- `recall_memory` 工具注册进 registry,让 LLM 能主动查记忆
- `agent/context.py` 扩展:注入 top-k 长期记忆 + 画像相关命题

---

## M3 记忆系统(2026-05-08 完成)

**目标**:把 Agent 从"只有短期记忆的金鱼"升级为分层记忆系统 —— 短期滚动窗口 + 长期向量检索 + 混合型用户画像(核心字段 + 动态命题),并让 LLM 能主动查记忆。

**实现要点**
- **短期记忆 `memory/short_term.py`**:从 `agent/core.py` 抽出 `deque(maxlen=cap)` 封装为 `ShortTermMemory`,容量读 `config.memory.short_term_size`。独立模块是为了测试和后续替换(如改持久化短期存)。
- **长期记忆 `memory/long_term.py`**:Chroma `PersistentClient` 落盘到 `data/chroma/`,collection `mybuddy_long_term`。嵌入默认 `BAAI/bge-m3`(中文友好),通过 `chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction` 封装。
  - 记忆和命题同一 collection,用 `metadata.type = "memory" | "claim"` 区分,`search(mem_type=...)` 可按类型过滤。
  - 关键设计:构造器暴露 `embedding_fn` 注入口,测试用 `MockEmbedding`(固定 hash 哈希到 64 维向量)绕过 BGE-M3 的首次下载。
  - `_distance_to_score(dist) = 1 - dist` 把 Chroma 的 cosine distance 转成 [0,1] 相关度分数,越大越相关;`MemoryManager` 用 0.3 作为下限裁剪噪音。
- **用户画像 `memory/profile.py`**:
  - 核心字段(hard facts):`ProfileField` 表,`set_field / get_field / get_all_fields` 幂等 upsert。
  - 动态命题(soft claims):`ProfileClaim` 表存置信度/证据链,**命题文本同步写 Chroma**(`mem_type="claim"`, `uid=f"claim_{sql_id}"`),让画像也能做语义检索。
  - `update_confidence(claim_id, delta)` 增量调整置信度,clamp 到 [0, 1];`search_claims(query, top_k, min_confidence)` 先向量检索再按 confidence 过滤。
  - 命题 id 在 SQLite 和 Chroma 两侧共享(`claim_{sql_id}`),避免双主键同步难题。
- **事实抽取 `memory/extractor.py`**:
  - 一次 LLM 调用,输入最近 N 轮文本对,输出严格 JSON `{facts, profile_fields, claims}`,三类分别入长期记忆/核心字段/命题候选。
  - Prompt 强制"只针对 USER 抽取,不编造",新命题 confidence 区间 0.3-0.7(低起点,靠后续证据补强)。
  - `_parse` 容错:先直 json.loads,失败则剥 ```json 围栏再 parse;仍失败返回空结果,不让抽取异常影响主对话流。
  - 抽取用 `small_model`(从 config 读),省 token;`temperature=0.3` 压缩幻觉。
- **记忆协调器 `memory/manager.py`**:`MemoryManager` 是 Agent 与三层记忆的唯一入口,暴露 5 个方法:
  - `add_message` / `get_recent_messages`:透传短期记忆。
  - `build_context_section(user_input)`:拼 system prompt 附加块,三段式结构 —— `## 相关历史记忆`(top-k long-term hits, score≥0.3)、`## 用户画像`(全部核心字段)、`## 关于用户的认知(仅供参考)`(top-5 相关命题, confidence≥0.5)。空段自动省略,避免塞无用 token。
  - `record_turn / maybe_extract`:按 `config.memory.extract_after_turns` 阈值异步触发抽取,成功后清空 buffer。抽取异常吞掉不抛,保证主对话不受影响。
- **recall_memory 工具 `tools/memory_tool.py`**:给 Agent 主动查记忆的能力。用"setup 时注入 LongTermMemory 实例"的模式(`setup_memory_tool(ltm)`),而不是 `get_context` —— 因为 LTM 是重对象且带 embedding 模型,不适合放进工具运行时上下文。工具返回 JSON 文本 `[{id, content, relevance}]` 供 LLM 引用。
- **Agent 接线 `agent/core.py`**:构造函数强制要求传 `MemoryManager`,`run()` 流程改为:检索记忆 → 构建含记忆的 system prompt → ReAct 循环 → record_turn → `maybe_extract()`。短期记忆从 `deque` 迁到 MemoryManager 内部的 `ShortTermMemory`。
- **CLI 接线 `cli.py`**:启动时依次构建 `LongTermMemory(persist_dir=chroma_dir, embedding_model=...)` → `MemoryManager(engine, config, ltm, provider)` → `setup_memory_tool(ltm)` → 注入 Agent。

**文件清单(本轮新增/修改)**
- 新增:`mybuddy/memory/short_term.py`、`long_term.py`、`profile.py`、`extractor.py`、`manager.py`、`__init__.py`(重新导出)
- 新增:`mybuddy/tools/memory_tool.py`
- 修改:`mybuddy/agent/core.py`(集成 MemoryManager)、`agent/context.py`(system prompt 支持 memory_context)、`cli.py`(装配分层记忆)、`tools/__init__.py`(导出 `setup_memory_tool`)
- 新增:`tests/test_memory.py`(long_term / profile / extractor / manager 四组,含 `MockEmbedding` fixture)

**验证结果**
- `uv run pytest tests/` → **31 passed**(比 M2 的 10 个多出 21 个 memory 用例) ✅
- CLI 启动不调用真实 API 时 `mybuddy chat --help` 正常;银行测试跑过数据流:`MockEmbedding` 下 `add/search` 返回语义最近项,`build_context_section` 正确按置信度/相关度过滤。
- 长期记忆落盘:`data/chroma/` 创建,`PersistentClient` 重启后命中历史 collection。

**踩坑**
- `extractor._parse` 最初只用 `json.loads`,Claude 返回带 ```json 代码块围栏时失败。补了 `_extract_json_object` 做围栏剥离,`faild → return FactExtractResult()` 兜底,确保主对话流不被抽取错误阻断。
- Chroma `embedding_functions.SentenceTransformerEmbeddingFunction` 是**懒加载**的 —— 构造时不下模型,首次 `add/search` 才触发下载,这对 M3 测试不友好。通过 `embedding_fn` 注入 `MockEmbedding`(确定性 hash)绕过,真实 CLI 启动时才触发模型下载。
- `ProfileClaim` 的 SQLite id 与 Chroma document id 如何绑定一度纠结,最终选 `claim_{sql_id}` 硬编码前缀,新建命题流程:`flush() → 拿 sql_id → Chroma.add(uid=f"claim_{sql_id}")`。两库分别是事务性和最终一致性,接受"Chroma 写失败时 SQLite 已提交"的轻微不一致(影响面:命题查不到,下次抽取会重建)。
- M2 DEVLOG 里挂的 `datetime.utcnow()` 迁移没在本轮清:M3 新代码跟着旧习惯继续用 utcnow。已在 M3 收尾后单独统一迁到 `mybuddy/_time.utcnow()`(naive UTC,与现有 SQLAlchemy `DateTime` 列兼容)。
- chromadb 0.5 抛 `DeprecationWarning: legacy embedding function config: 'MockEmbedding' object has no attribute 'is_legacy'`,是上游兼容层告警,MVP 阶段忽略,后续升级 chromadb 再跟进。

**下一步 — M4 调度 + 主动关怀 + Dream Job**
- `scheduler/core.py` 用 APScheduler `AsyncIOScheduler` + `SQLAlchemyJobStore`(复用 mybuddy.db),封装 `add_reminder / add_daily_job / shutdown`
- `scheduler/proactive.py` 实现两类主动任务:到期提醒播放、每日早安
- `learning/dream.py` 每晚固定 job,五件事:记忆去重合并 / 命题置信度重算 / 冲突消解 / 生成洞察 / nudge 入队
- 所有主动产物(到期提醒 / 早安 / nudge)走 `pending_messages` 表,CLI 每次对话开始时 drain 并播出
- CLI 增 `mybuddy dream run`(手动触发)便于开发期验证

---

## M4 调度 + 主动关怀 + Dream Job(2026-05-09 完成)

**目标**:让 MyBuddy 有"主动关怀"能力 —— 用户设置的提醒到期后能播出、每天早安问候、夜间 Dream Job 整理记忆并生成 nudge。所有主动消息走 `pending_messages` 表,CLI 交互时 drain 播出(离线期消息不丢)。

**实现要点**
- **pending_messages 队列 `storage/queue.py`**:所有"调度器触发、等 CLI 播出"的消息统一走这张表,三类 source:`reminder`(到期提醒)、`greeting`(每日早安)、`nudge`(Dream Job 问候)。`enqueue/drain_pending/list_undelivered` 三个函数,drain 时**立即标记 delivered**(单 CLI 进程够用,多进程需加锁)。
- **调度器 `scheduler/core.py`** + **顶层 jobs `scheduler/jobs.py`**:`AsyncIOScheduler + SQLAlchemyJobStore(url=sqlite:///mybuddy.db)`,APScheduler 自建 `apscheduler_jobs` 表和 `Base.metadata.create_all` 不冲突。Job 函数必须模块顶层才能 pickle:
  - `fire_reminder(reminder_id, db_file)`:触发时自己 `init_db()` 重建 engine,更新 `Reminder.status=fired`,`enqueue(source="reminder")`
  - `fire_daily_greeting(db_file, persona_name)`:写一条固定话术的 greeting
  - `run_dream_job(db_file, config_path)`:在 APScheduler 线程池里用 `asyncio.run()` 起新 event loop 跑 `DreamJob.run()`
  - 只传基本类型(str/int),不传 engine/provider 等不可序列化对象
- **MyBuddyScheduler 三类入口**:`schedule_reminder(id, trigger)` 用 `DateTrigger + misfire_grace_time=3600`(1h 宽限);`schedule_daily_greeting(hh:mm)` 和 `schedule_dream_job(hh:mm)` 用 `CronTrigger`,时区跟随 tzlocal(本地时间解释)。`replace_existing=True` 保证幂等重注册。
- **Dream Job 五件事 `learning/dream.py`**(最小版,全部在 try/except 里隔离,错一步不影响其他):
  1. **去重合并**:`ltm.list_all(with_embeddings=True)` 两两 cosine,≥0.9 删除较长的一条(假定较短更精炼)
  2. **置信度重算**:`get_all_claims` → 检查 `updated_at` 是否在近 `RECENT_EVIDENCE_DAYS=7` 天内 → 是则 +0.05,否则 -0.05;然后 `prune_low_confidence(0.3)` 归档低置信度命题
  3. **冲突消解**:把所有命题打包一次 LLM,解析返回的 `[{a, b, reason}]`,置信度较低一方 -0.2
  4. **生成洞察**:读 `messages` 表当天记录(目前 agent 不持久化 messages,暂为空,等未来 agent 开写 DB 就自动有数据),交给 LLM 返回新命题候选,clamp 到 [0.3, 0.6]
  5. **nudge 生成**:按 `created_at` 最早的 top-3 记忆,LLM 生成 1-2 条问候,`enqueue(source="nudge")`
- **LongTermMemory.list_all**:为 Dream Job 去重新增,`Chroma.get(where, include=["documents","metadatas","embeddings"])`,按 `mem_type` 可选过滤。
- **set_reminder 工具接入调度器**:`tools/context.py` 新增 `scheduler` 槽位;`set_reminder` 写表后,若 `scheduler.running` 就立即 `schedule_reminder`,返回字段加 `scheduled: true/false`。调度失败不影响持久化(reminders 表仍有记录,CLI 启动时 `_restore_reminders` 兜底重注册)。
- **CLI 接线 `cli.py`**:
  - `chat` 启动:`cfg.scheduler.enabled=True` 时建 `MyBuddyScheduler`,`.start()` → `_restore_reminders()`(扫 pending + 未来触发的 Reminder) → 注册每日早安 + dream job;`set_context(scheduler=...)` 让工具能访问;退出时 `shutdown()`
  - 每次对话输入前调 `_drain_pending_to_console(engine)`,按 source 打 `⏰/🌅/💭` 图标
  - 启动时先 drain 一次,离线期间触发的消息登场即播
  - 新增 `mybuddy dream run` 子命令(`dream_app = typer.Typer()` + `app.add_typer`),手动执行 Dream Job 并打印 `DreamReport`(合并/更新/冲突/洞察/nudge 计数)

**文件清单(本轮新增/修改)**
- 新增:`mybuddy/storage/queue.py`、`mybuddy/scheduler/core.py`、`scheduler/jobs.py`、`mybuddy/learning/dream.py`
- 修改:`storage/__init__.py`(导出 queue 函数)、`scheduler/__init__.py`(导出 MyBuddyScheduler)、`learning/__init__.py`(导出 DreamJob)、`memory/long_term.py`(新增 `list_all`)、`tools/context.py`(新增 scheduler 槽)、`tools/reminder.py`(调用 `get_scheduler`)、`cli.py`(scheduler/drain/dream 子命令)
- 新增测试:`tests/test_queue.py`(2)、`tests/test_scheduler.py`(5)、`tests/test_dream.py`(5)

**验证结果**
- `uv run pytest tests/` → **43 passed**(比 M3 多 12 个) ✅
- `uv run mybuddy dream --help` / `mybuddy --help` 命令树正确 ✅
- `test_fire_reminder_updates_status_and_enqueues` 端到端验证:造一条 pending Reminder → `fire_reminder(id, db)` → Reminder.status=fired + pending_messages 有记录 ✅
- `test_scheduler_cron_jobs` 验证 SQLAlchemyJobStore 真的持久化了 `daily_greeting` / `dream_job` 两个 cron job(`list_jobs` 看到 next_run_time) ✅
- `test_nudges_enqueued` 用 ScriptedProvider + MockEmbedding 验证 Dream Job 写 2 条 source=nudge 到 pending_messages ✅

**踩坑**
- **AsyncIOScheduler 需要 running event loop**:`scheduler.start()` 内部调 `asyncio.get_running_loop()`,同步测试里会 `RuntimeError: no running event loop`。解决:涉及 `start()` 的 2 个 scheduler 测试标为 `@pytest.mark.asyncio`。Job 执行函数(`fire_reminder` 等)本身是同步的,可独立测。
- **ScriptedProvider 按调用次序返回 text 的隐坑**:Dream Job 的 3 个 LLM 步骤(conflict/insights/nudges)会"按条件懒调用"—— claims<2 就不调 conflict、messages 空就不调 insights。第一版测试只塞 `["[]","[]","[]"]` 当作 3 次 LLM 响应,但实际只调了 1 次,测试行为跟预期偏离。修法:测试里**补齐前置数据**(claims≥2、messages 非空、memories 非空),让每个步骤都真的会调 LLM,脚本响应按次序对齐。
- **Job 必须顶层函数**:一开始冲动想把 job 写成 `MyBuddyScheduler.fire_reminder` 方法,SQLAlchemyJobStore pickle 时炸 `can't pickle local object`。拆到 `scheduler/jobs.py` 作为模块顶层函数,参数全部基本类型,job 自己重建 engine。副作用:`run_dream_job` 要起新的 event loop(`asyncio.run`),因为 APScheduler 默认 executor 在普通线程跑。
- **Dream Job 今日对话暂时为空**:`_collect_today_messages` 读 `messages` 表,但 agent 目前只写短期内存,不持久化到 DB。所以当前 insights 步骤在真实运行时 input 为空,LLM 返回 `[]`。这是预期行为,等未来 agent 开启消息持久化后自动生效 —— 不做缓解,避免给 dream job 塞"假数据"。

**下一步 — M5 情绪感知 + 反馈回流**
- `emotion/detector.py`:每轮对话后对 user 消息做情绪分类(同 LLM 小模型,二级标签 positive/neutral/negative + strength 0-1)
- 情绪结果写入 trajectory 的 metadata;连续 ≥2 轮 negative 时触发 Dream Job 的 `nudge`(主动共情回访)
- `learning/feedback.py` FeedbackEvent 总线:`/good /bad /fix` 指令从 CLI 发出后,除了写 trajectory labels,还要回写触发它的 skill/claim 的计数和置信度
- 隐式反馈:下一条用户消息是否含纠错信号(LLM 二分类),自动给上一轮标 `implicit:negative`

---

## M5 情绪感知 + 反馈回流(2026-05-09 完成)

**目标**:让小伙伴"察言观色"——识别用户情绪并即时调整语气,连续低落时主动关怀;把 `/good /bad /fix` 反馈打通到画像命题,为 M6 Skills 的自优化预埋总线。

**决策(与用户对齐)**
- 情绪检测在**每轮对话开头**触发,结果即时影响本轮 system prompt(不走异步后置)。
- 连续 2 轮 negative 时**同时**:当场调软语气 + 入队一条延迟 30min 的 nudge。
- 隐式反馈用**关键词启发式**,不走 LLM(省成本,误判可接受)。

**实现要点**
- **EmotionDetector `emotion/detector.py`**:单次 LLM 调用,严格 JSON `{label, strength, reason}`;容错同 FactExtractor(围栏剥离 + JSON fail → 回退 neutral);`EmotionResult.is_negative` 约定 `label=="negative" and strength>=0.3`(避免弱负面草木皆兵);LLM 异常吞掉不抛,主对话流不被情绪检测阻断。
- **EmotionTracker `emotion/state.py`**:`deque(maxlen=5)`,`is_consecutive_negative(n=2)` 判断最近 n 条是否全为有效负面。进程内状态,重启后重新积累(MVP 够用)。
- **FeedbackBus `learning/feedback.py`**:同步 pub/sub,订阅者失败隔离(一个炸不影响其他)。内置两个订阅者工厂:
  - `make_trajectory_subscriber(logger)`:把 label 写进 `YYYY-MM-DD.labels.jsonl`(原来 CLI 直接调 `logger.attach_label`,现在走 bus 以便 M6 skill 订阅者复用)。
  - `make_profile_claim_subscriber(profile, delta_good=0.05, delta_bad=-0.1)`:正反馈小幅加强、负反馈较大削弱本轮相关 claim。reward 信号很弱,所以 delta 都保守,且**负向大于正向**(错得离谱比说对更值得更新)。
  - `FeedbackEvent.is_negative` 把 `bad / fix / fix:<text> / implicit:negative` 统一识别为负反馈。
- **隐式反馈 `detect_implicit_negative(text)`**:正则 `不对|不是这样|不是这个意思|我的意思是|错了|再试|重来|理解错了|没听懂|搞错|不准确|别这样|不是`,命中即判定。保守起见不做 LLM 兜底,宁可漏过(用户真没意见)。
- **Agent 接线 `agent/core.py`**:构造参数新增 `emotion_detector / emotion_tracker / engine`,**均为可选**(None 时整个情绪链路跳过,测试友好)。`run()` 开头先 `_detect_emotion` → tracker 更新 → 若 `is_consecutive_negative(2)` 且有 engine,立即 `enqueue(source="nudge", scheduled_at=now+30min)`;然后 `_emotion_system_hint` 按当前轮单独 vs 连续两轮输出不同指导语,与 `memory_context` 一起拼进 system prompt 的 extras。`trajectory.meta["emotion"] = {label, strength, reason}` 方便未来离线分析。
- **CLI 装配 `cli.py`**:`chat` 启动时建 `EmotionDetector(provider, small_model)` + `EmotionTracker(window=5)` + `FeedbackBus`,bus 默认挂 trajectory + claim 两个订阅者。`/good /bad /fix` 走 `bus.publish(FeedbackEvent)` 取代原来直接写 label;每轮 user_input 先 `detect_implicit_negative` 命中就给上一轮补发 `implicit:negative`。`last_related_claim_ids` 钩子先占位(MemoryManager 尚未暴露本轮检索到的 claim ids,claim 回写暂为空 —— M6 补)。
- **`_render_response` 扩展**:回复尾行加情绪标签 `情绪 negative (0.7)`,neutral 且强度 <0.3 时不打扰地隐藏。

**文件清单(本轮新增/修改)**
- 新增:`mybuddy/emotion/detector.py`、`emotion/state.py`、`emotion/__init__.py`、`mybuddy/learning/feedback.py`
- 修改:`mybuddy/agent/core.py`(情绪检测 + nudge 触发 + system prompt 合并 + emotion 写 meta)、`cli.py`(EmotionDetector/Tracker/FeedbackBus 装配 + 隐式反馈)、`learning/__init__.py`(导出 feedback 符号)
- 新增测试:`tests/test_emotion.py`(13)、`tests/test_feedback.py`(8)、`tests/test_agent_emotion.py`(4)

**验证结果**
- `uv run pytest tests/` → **68 passed**(M4 的 43 + 新增 25) ✅
- `test_consecutive_negative_enqueues_nudge` 端到端验证:第一轮 negative → pending_messages 空;第二轮 negative → pending_messages 多一条 source=nudge ✅
- `test_single_negative_doesnt_trigger_nudge`:negative → positive 打断,不触发 ✅
- `test_agent_without_emotion_system_still_works`:不传 detector 时旧行为完整保留 ✅
- `test_claim_subscriber_*`:正反馈 +0.05、负反馈 -0.1、隐式负面同等处理、无 related 不改 ✅

**踩坑**
- **StubProvider 要同时吃情绪和对话两种请求**:情绪 prompt 和主对话 system 不同,单一脚本队列没法分流。靠情绪 prompt 里"情绪识别"关键字做路由,emotion_responses/chat_responses 两个独立队列。这种 hack 对单测够用,生产里双实例更稳。
- **弱强度负面不该触发 nudge**:最初 `is_negative` 只判 `label=="negative"`,导致 strength=0.2 的轻微抱怨也会累积成连续负面。改为 `strength>=0.3` 后稳定。
- **claim 回写在 CLI 没实连**:反馈总线已把 claim 订阅者挂上了,但 MemoryManager 目前不暴露"本轮 build_context_section 检索到了哪些 claim ids",所以 `last_related_claim_ids=[]`。函数级回写逻辑已单测验证正确;CLI 接通留到 M6(配合 skill 场景一次补)。
- **情绪检测每轮多一次 LLM 调用**:成本用 `small_model`(haiku)缓解,评估下来每轮 +~200 tok,可接受。未来如果想省,可以给"明显中性/短文本"加本地关键词前置跳过 LLM。

**下一步 — M6 Skills 子系统**
- `learning/skills.py`:YAML frontmatter + Markdown 模板加载;`SkillRegistry.match(context) -> list[Skill]` 按 triggers 匹配;`Skill.on_success/on_fail` 计数 + confidence 自动调节;低置信度归档
- 任务完成后(≥3 步工具调用成功)让 LLM 复盘是否值得抽象为 skill → 写 `data/skills/*.md`
- FeedbackBus 订阅者:`make_skill_subscriber(registry)` 把 /good /bad 计入触发它的 skill 的 success/fail
- 配套把 MemoryManager 改一下:`build_context_section` 返回 (text, related_claim_ids) 元组,这样 CLI 的 `last_related_claim_ids` 钩子能真的工作 —— M5 留的账

---

## M6 Skills 子系统(2026-05-09 完成)

**目标**:让 MyBuddy 有"程序性记忆"—— 积累"在 X 情境下怎么做"的 skill 模板,使用越多的越可靠,失败过多自动归档。配套把 M5 遗留的 `last_related_claim_ids` 欠账一起补上,让反馈总线对画像命题的回写真正生效。

**决策(与用户对齐)**
- **存储**:纯 Markdown + YAML frontmatter(`data/skills/*.md`),高频字段(success/fail/confidence)也进文件。单 CLI 进程够用,并发留给未来。
- **自动创建**:Agent 每轮末尾,`tool_calls ≥ 3 且 finish_reason=="stop"` 时,`asyncio.create_task` 异步让 LLM 复盘产出 skill 草案(confidence=0.3 起步),不阻塞用户。

**实现要点**
- **Skill 数据模型 `learning/skills.py`**:`Skill` dataclass 承载 frontmatter + 步骤;`to_markdown / from_markdown` 双向转换,frontmatter 用 `yaml.safe_dump(sort_keys=False)` 保持字段稳定顺序。置信度用 Laplace 平滑 `success / (success + fail + 1)`,避开 0/0 和"1 次成功就 100%"的偏差。`confidence < 0.2 且 total ≥ 3` 时 archived=True(不删文件,留痕,可人工翻案)。
- **SkillRegistry**:`load_all(skills_dir)` 扫目录全部加载,`match(user_input, emotion_label, consecutive_negative)` 返回 top-N(默认 3,最低置信 0.5)按 confidence 降序。触发匹配用子串 / 关键词("情绪=消极" / "持续>2轮" 这类标签),MVP 够用,未来可替换为 embedding。`create(name, ...)` 幂等(同名保留已有 counts,覆盖 triggers/steps)。
- **Agent 注入点 `agent/core.py`**:`run()` 开头顺序执行:情绪检测 → `build_context_section` 解包 `(text, claim_ids)` → `_match_skills` 返回 `(skill_hint, triggered_names)` → 三段与人设合并成 system prompt。`trajectory.meta` 记录 `emotion/triggered_skills/related_claim_ids`。`AgentResult` 新增 `related_claim_ids` 和 `triggered_skills`,供 CLI 下一轮发 FeedbackEvent 时带上。
- **SkillCurator `learning/skill_curator.py`**:轨迹序列化成紧凑 prompt(工具名 + 简短参数 + 结果前 120 字),small_model + temperature=0.3;输出结构化 JSON `{should_create, name, triggers, steps, reason}`。解析容错全套照抄 Dream Job 的围栏剥离 + 异常吞掉。
- **FeedbackBus 新订阅者 `make_skill_subscriber(registry)`**:从 `event.meta["triggered_skills"]` 读本轮 skill,`is_positive → record_success`、`is_negative → record_failure`。未知 skill 不抛异常。
- **MemoryManager 签名改动**:`build_context_section(user_input) -> tuple[str, list[int]]`,第二个返回值是命中命题的 sql_id 列表。调用方 `agent/core.py` 一处更新,测试里的 override 改为返回 `("", [])`。由此 CLI `_chat_loop` 的 `last_related_claim_ids` 真正接通,`make_profile_claim_subscriber` 不再空转。
- **CLI 装配 `cli.py`**:启动时 `SkillRegistry.load_all(cfg.paths.skills_dir)` + `SkillCurator(provider, registry, small_model)`,`feedback_bus.subscribe(make_skill_subscriber(registry))`。每轮把 `result.triggered_skills / related_claim_ids` 存到局部变量,下一轮发 `FeedbackEvent(meta={"triggered_skills": ...}, related_claim_ids=...)` 时带上。banner 增加 `skills N/M` 展示。响应尾行若有 skill 命中会打 `参考 skill …`。

**文件清单(本轮新增/修改)**
- 新增:`mybuddy/learning/skills.py`、`mybuddy/learning/skill_curator.py`、`tests/test_skills.py`(25)、`tests/test_agent_skills.py`(5)
- 修改:`mybuddy/learning/feedback.py`(加 `make_skill_subscriber`)、`mybuddy/learning/__init__.py`(导出)、`mybuddy/memory/manager.py`(`build_context_section` 返回元组)、`mybuddy/agent/core.py`(skill 匹配 + 注入 + 触发 curator + 扩 AgentResult)、`mybuddy/cli.py`(装配 + banner + 反馈带 skill name)、`tests/test_agent.py` / `tests/test_agent_emotion.py`(override 改返回元组)

**验证结果**
- `uv run pytest tests/` → **98 passed**(M5 的 68 + M6 新增 30) ✅
- `test_skill_match_injects_into_system_prompt`:skill 文件中的"温柔回应" / skill 名"早安问候流程"都出现在下一次 LLM 调用的 system 参数里 ✅
- `test_curator_triggered_on_complex_task`:3 步工具 + stop → SpyCurator.called == 1;<3 步场景下 called == 0 ✅
- `test_feedback_subscriber_records_success/failure`:record_* 写回 `.md` 文件,重新 `load_all` 能看到计数 +1 ✅
- 手工冒烟:`SkillRegistry.create` 写文件 → `match("早上好啊小布")` 命中 → 连续 `record_success/record_failure` 推置信度升降 → 符合 Laplace 平滑预期 ✅
- `mybuddy --help` 命令树、`mybuddy dream --help` 均正常 ✅

**踩坑**
- **Skill frontmatter 字段顺序**:`yaml.safe_dump` 默认 sort_keys=True 会按字母排序,人类阅读不友好(`archived` 跑到 `confidence` 前)。改用 `sort_keys=False` + `allow_unicode=True` 后,frontmatter 就按 `name / triggers / counts / confidence / archived / created_at / updated_at` 的语义顺序输出。
- **归档阈值需要最小样本**:早期实现里只要 `confidence < 0.2` 就 archived,导致刚建的 skill(confidence=0.3、一次失败后 Laplace 算出 0.0)立刻被归档。加了 `ARCHIVE_MIN_SAMPLES = 3` 的闸门后,新 skill 有"试错窗口"。
- **curator 异步 task 的生命周期**:`asyncio.create_task` 挂到当前 event loop,`agent.run` 返回后用户可能立刻开始下一轮输入。测试里 create_task 之后要 `await asyncio.sleep(0)` 两次让 curator coro 真的跑到;CLI 里由下一轮 `await agent.run` 让出控制权,天然对齐,没改动。
- **`build_context_section` 签名改动波及面**:有两个 test 文件用 monkeypatch 替换了这个方法,改成返回元组。Agent 内部 unpack 也要跟着改。好处是以后 related_claim_ids 的流向一目了然,feedback 回写到命题从 M5 遗留的"假接线"变成真通路。

**下一步 — M7 工具扩展**
- 接真实天气 API(替换 M2 mock)、翻译、Web 搜索,统一错误处理
- 笔记 / 日记工具:`write_note` / `search_notes`,落 SQLite 的新表
- `list_skills` 工具让 Agent 能自查自己手上有哪些 skill(目前 skill 是被动注入,不进工具注册表)
- HTTP API 骨架(`FastAPI`):先搭 POST /chat、GET /memory/search,复用现有 Agent / MemoryManager
- `trajectory export` CLI 子命令:导出 JSONL 为 DPO / SFT 格式,为未来本地 Hermes LoRA 预留

---

## M7 MVP 收尾 — 工具扩展 + CLI admin(2026-05-09 完成)

**目标**:让 MyBuddy 能真正每天自己用起来。补齐 M7 工具扩展(weather 真 API / translate / web_search / notes / list_skills),并加 `profile / reminders / skills` 三组 CLI admin 命令消除"必须 `sqlite3` 手改"的体验断层。M8(HTTP API + 本地 Hermes + 轨迹导出)按设计方案标注为可选,MVP 阶段不做。

**决策(与用户对齐)**
- MVP 范围 = M7 工具扩展 + 必要 CLI 命令,不做 HTTP / 本地 Hermes。
- 外部 API 策略:天气 open-meteo(免 key)、搜索 DuckDuckGo HTML(免 key)、翻译复用已装配的 LLM provider。
- notes:SQLite 为主存 + 同步写 Chroma(`mem_type="note"`),笔记搜索与对话记忆检索复用同一层 LTM。

**实现要点**
- **config 扩展 `config.py`**:新增 `ToolsConfig(weather_mock, web_search_max_results, http_timeout)`。mock 开关便于离线开发和单测,避免每次 pytest 都打到 open-meteo。
- **存储层 `storage/models.py`**:加 `Note` 表(title/content/tags_json/created_at/updated_at)。SQLAlchemy `Base.metadata.create_all` 在 `init_db` 里已有,新表自动建出。
- **ToolContext 扩 `tools/context.py`**:加 `provider` 和 `long_term` 两个槽位,对应 `get_provider() / get_long_term()`。这样 translate 能拿到 LLM、notes 能拿到 LTM,不用每个工具都做"注入模块全局变量"的 hack。`setup_memory_tool(ltm)` 现在在注入 memory_tool 模块全局 `_ltm` 的同时,也 `set_context(long_term=ltm)`,让 notes 复用同一对象。
- **weather `tools/weather.py`**:接 open-meteo —— 先 `geocoding-api/v1/search` 拿坐标,再 `api/v1/forecast?current=...&wind_speed_unit=kmh` 拿天气。`weather_code` 查表映射成中文简述(0-99 常见 20 种)。任何失败都走 `_mock_response(city, "fallback: ...")`,原返回结构不变。
- **translate `tools/translate.py`**:一次 LLM 调用,system prompt 强制"只输出译文",参数 `text + target_lang`(默认英文)。用 `cfg.llm.small_model` 省 token。
- **web_search `tools/web_search.py`**:POST `https://html.duckduckgo.com/html/?q=...`,带 UA。用单一正则抓 `result__a` 的 href/title + `result__snippet`,`html.unescape` + 简单 `_TAG_RE` 剥标签,避免引入 BeautifulSoup。DDG 把结果链接包在 `//duckduckgo.com/l/?uddg=<encoded>` 里,用 `urllib.parse.parse_qs` 解码回真 URL。60s TTL 的 in-memory dict 缓存,避免重复 query 刷官网。
- **notes `tools/notes.py`**:
  - `write_note(content, title="", tags=[])` → SQLite insert(title 缺省取正文首 30 字)→ 同步 `ltm.add(content, mem_type="note", uid=f"note_{sql_id}", extra_meta={sql_id, title, tags})`。Chroma 失败不回滚 SQLite。
  - `search_notes(query, top_k=5)` → `ltm.search(query, mem_type="note")` → 回填 SQLite 的 title/created_at/tags。无结果返回 `"没有相关笔记。"` 与 `recall_memory` 风格一致。
- **list_skills `tools/skill_tool.py`**:沿用 `setup_memory_tool` 的模块级注入套路 —— `setup_skill_tool(registry)` 把 SkillRegistry 存到模块全局,工具内部按 confidence 降序返回所有未归档 skill 的 `{name, triggers, confidence, steps_preview}` JSON。
- **CLI admin `cli_admin.py`**:三组 typer sub-app,`register(app)` 一次挂到主 `mybuddy` 下。
  - `profile show/set/unset` —— 核心字段 + top 10 高置信度命题表格。为避免 BGE-M3 首次下载拖垮 admin 命令,**`_load_profile` 不初始化 Chroma**,命题检索走 UserProfile 的 SQL 降级分支。
  - `reminders list [--all] / cancel <id>` —— 直接读 Reminder 表、把 pending 改 cancelled。不连 scheduler(`fire_reminder` 会查 status,MVP 这样够用)。
  - `skills list [--all] / show <name> / archive <name> / unarchive <name>` —— 直接 `SkillRegistry.load_all` + `save`。
- **CLI 装配 `cli.py`**:启动时 `set_context(provider=..., long_term=ltm)` 让新工具能拿依赖;`setup_skill_tool(skill_registry)` 和 memory_tool 对齐。主 typer 挂 admin 的 `register(app)` 调用。

**文件清单(本轮新增/修改)**
- 新增:`mybuddy/tools/translate.py`、`mybuddy/tools/web_search.py`、`mybuddy/tools/notes.py`、`mybuddy/tools/skill_tool.py`、`mybuddy/cli_admin.py`、`tests/test_tools_extra.py`(15)、`tests/test_cli_admin.py`(5)
- 修改:`mybuddy/config.py` + `config.example.yaml`(ToolsConfig)、`mybuddy/storage/models.py` + `storage/__init__.py`(Note 表)、`mybuddy/tools/context.py`(provider/long_term 槽)、`mybuddy/tools/memory_tool.py`(同步 context.long_term)、`mybuddy/tools/weather.py`(open-meteo + fallback)、`mybuddy/tools/__init__.py`(新工具导入副作用 + 新 getter)、`mybuddy/cli.py`(admin 挂载 + set_context 追加 provider/long_term)、`tests/test_tools.py`(weather 测试改为 mock 模式,避免发网络)

**验证结果**
- `uv run pytest tests/` → **118 passed**(M6 的 98 + M7 新增 20) ✅
- `uv run mybuddy --help` 命令树正确:`version / init / chat / dream / profile / reminders / skills` ✅
- `uv run python -c "…ToolRegistry.default().names()"` 默认注册 8 个工具:`list_skills / recall_memory / search_notes / set_reminder / translate / weather / web_search / write_note` ✅
- `test_weather_real_api` 用 `httpx.MockTransport` 验证 geocoding + forecast 两跳拼装、weather_code=2 → "局部多云" ✅
- `test_weather_fallback_on_404`:404 → 退回 mock,`note` 含 "fallback" ✅
- `test_web_search_parses_results`:3 条结果全部解析、DDG 重定向链接被还原成原 URL、HTML 标签被剥掉 ✅
- `test_write_note_creates_row_and_chroma`:SQLite 落盘 + Chroma 命中 ✅
- `test_profile_set_show_unset` / `test_reminders_list_and_cancel` / `test_skills_list_show_archive_unarchive` 三组 admin 流程端到端 ✅

**踩坑**
- **httpx.AsyncClient MockTransport 的 monkeypatch 姿势**:httpx 的 `AsyncClient(transport=...)` 是关键字参数。测试里用 `monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)` 把 `transport=` 强塞进去,比 `httpx.MockTransport` + 全局客户端装饰器干净。
- **BGE-M3 首次下载陷阱再次发作**:`mybuddy profile show` 第一版构造了真实 `LongTermMemory`,结果在 pytest 里把 BGE-M3(~2GB)触发下载,测试卡住。改为 `UserProfile(engine, None)` + 依靠 `search_claims` 的 SQL 降级分支,admin 命令启动瞬间完成。记得 admin 命令是为"每天随手看一下"设计的,不能有重加载。
- **DDG HTML 正则的边界**:第一版正则只匹配了 `<a class="result__a">`,没发现 DDG 把 URL 用 `//duckduckgo.com/l/?uddg=<encoded>` 包起来。加了 `_decode_ddg_redirect` 还原真 URL,不然 LLM 读到一串相对链接会困惑。
- **`setup_memory_tool` 现在有副作用**:不仅注入 memory_tool 模块的 `_ltm`,还同步到 ToolContext,让 notes 能复用。一开始让 notes 自带独立的"注入函数",后来嫌两套注入太吵,合并到一处更简单。`tools/context.reset()` 也要记得把 long_term/provider 清掉,避免跨测试污染。
- **typer sub-app 的 help 文字来自 docstring 第一行**:`reminders_cancel` 最初 docstring 写了三行的长解释,导致帮助面板把整段塞进去变成一坨墙。改成单行后整齐了。

**MVP 落地情况**
- 8 个内置工具:weather / set_reminder / recall_memory / write_note / search_notes / translate / web_search / list_skills。Agent 调用能覆盖"日常关心 + 外部查询 + 自身能力检索"的全部基础场景。
- 3 组 admin 命令:profile / reminders / skills。配合 `dream run`、`chat`,日常维护不再需要 sqlite3。
- 总测试数:118。整体架构符合设计方案"M1–M7"的 MVP 完整原型定义。
- 后续优化方向(M8+,非 MVP):本地 Hermes 适配、FastAPI 路由、trajectory export、notes 标签索引查询、tool 权限/成本控制。
