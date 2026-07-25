# MyBuddy — Evidence-Grounded AI Agent

> A stateful, evidence-grounded LLM agent runtime.

MyBuddy 是一个本地运行的长期陪伴 Agent。它把模型输出视为**待验证的候选决策**，
而不是可以直接写入状态的事实：每次回合由模型通过 tool calling 提交结构化决策包，
引擎再验证证据、行为约束与状态迁移，只有整包通过才更新记忆和历史。

项目重点不是聊天 UI，而是三个 Agent 工程问题：

- 如何让长期记忆可追溯，降低“记忆幻觉”；
- 如何让 Agent 只把真实发生、被环境确认的事件当作经历；
- 如何在模型输出不稳定时保持状态一致，并留下可复现的失败证据。

## 30 秒看懂一次回合

```text
user event / environment receipt
                │
                ▼
       bounded context selection
                │
                ▼
  LLM tool call → CandidateBundle
                │
                ▼
  Pydantic shape validation
  + evidence / invariant validators
          │                 │
       accepted          rejected
          │                 │
          ▼                 ▼
 prepare state,         retain raw candidate
 history, memory        + rejection reasons
          │                 │
          ▼                 ▼
 per-file atomic        safe fallback;
 replacement            no candidate memory
          │
          ▼
 pending expression ── shown acknowledgement ──► shared history
```

一次决策包同时描述有限状态变化、记忆操作、回复动作及其证据 ID。核心数据结构见
[`CandidateBundle`](mybuddy/mind.py#L132)，集中校验入口见
[`validate_bundle`](mybuddy/mind.py#L1947)。

## 核心设计

### 1. 模型是提案者，代码是写入者

模型只能提交固定 schema，不能直接修改文件或自由扩展状态字段。Pydantic 拦截结构错误，
领域校验器继续检查“不编造、证据匹配、纠错不抹历史、有限状态迁移”等约束。失败候选的
原文与拒因写入 `failures.jsonl`，用于定位模型、prompt 或校验器问题。

### 2. 证据化长期记忆

记忆分为用户事实、自身经历、共同经历和模式。事实写入必须引用当前输入、既有历史事件或
真实活动收据；`record / integrate / recall / correct / forget` 都有显式语义。对书籍内容的
理解可以被后续段落修订，但会保留前后版本、修订依据和时间，避免用新答案覆盖过去。

### 3. Agent 与环境之间使用收据

本地 HTTP 边界 [`BodyBridge`](mybuddy/body_api.py#L123) 顺序处理：

1. 确认上一条表达是否真正展示；
2. 核验阅读或移动是否真正完成；
3. 处理至多一个幂等事件；
4. 返回当前非破坏视图。

未展示的回复不会进入共同历史；中断或失败的活动不会被写成 Agent 的真实经历；重复
`event_id` 不会再次执行模型回合。

### 4. 可审计的持久化与失败恢复

运行状态保存在四个可读文件中：

```text
state.json       当前状态与待确认输出
history.jsonl    只追加的已发生事件
memories.json    长期记忆与当前理解
failures.jsonl   未获准写入的候选及拒因
```

写入前先在目标目录完整生成临时文件并 `fsync`，随后逐文件原子替换；进程内替换失败会回滚
已替换文件。这个设计优先可读性与可调试性，不把它包装成数据库级跨文件事务。

### 5. 有界上下文与模型适配

引擎按历史窗口和字符预算选择上下文，核心记忆优先，旧理解只在相关问题出现时冷加载。
DeepSeek 与 OpenRouter 共用
[`OpenAICompatibleProvider`](mybuddy/llm/openai_compatible.py)，包含 tool choice 约束、
瞬态错误重试和统一 usage 解析。

## Evaluation

离线测试覆盖候选解析、证据校验、记忆操作、失败回滚、事件幂等、展示确认、时间跳跃、
活动中断和长期连续性。

| 检查 | 结果 | 是否需要 API Key |
|---|---:|:---:|
| `pytest` 单元与集成测试 | **1014 passed** | 否 |
| `ruff` 静态检查 | **passed** | 否 |
| 固定对抗场景的真实模型回归 | 脚本可重复运行并输出 JSON 报告 | 是 |

以上离线结果于 2026-07-25 在 Python 3.12 环境复验。

真实模型回归会重复检查无证据共同回忆、诱导编造、公开纠错、长期沉默和有据回忆等场景：

```bash
uv run python scripts/personality_regression.py \
  --config config.yaml \
  --runs 3 \
  --data-dir data/regression-001
```

每次运行使用新目录，保留逐场景输出、规则失败、候选拒因和汇总报告。

## Quick start

要求 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --extra api --extra dev
cp config.example.yaml config.yaml
export DEEPSEEK_API_KEY="your-key"

# 运行一个完整心智回合
uv run python -m mybuddy.mind "我最近开始准备 AI Agent 岗位面试"

# 启动本地 Agent API
uv run mybuddy web --config config.yaml --data-dir data/demo
```

发送一个幂等事件：

```bash
curl -sS http://127.0.0.1:8000/api/body/step \
  -H 'Content-Type: application/json' \
  -d '{
    "event": {
      "event_id": "demo-001",
      "type": "chat",
      "content": "我最近开始准备 AI Agent 岗位面试"
    }
  }'
```

把响应中的 `expression.id` 作为下一次请求的 `shown_id` 传回后，这句话才会进入共同历史。

## 代码导航

```text
mybuddy/
  mind.py                 Agent loop、候选 schema、校验器、记忆与持久化
  body_api.py             FastAPI 边界、事件幂等与 acknowledgement
  config.py               类型化配置与环境变量展开
  llm/                    OpenAI-compatible 模型适配
scripts/
  personality_regression.py
  personality_regression_cases.json
tests/
  test_mind.py            核心决策、证据与恢复
  test_body_api.py        环境边界与幂等
  test_longitudinal.py    跨时间连续性
```

## Trade-offs

- 面向本地单用户、单写者场景，不是通用工作流或多 Agent 框架。
- JSON/JSONL 便于审计，但不适合高并发或大规模检索；当前没有 RAG、向量库或工具执行层。
- 单文件替换是 crash-safe 的，多文件提交不是断电条件下的完整 ACID 事务。
- 行为校验包含中文语义启发式规则，因此修改规则后必须运行正反向对抗回归。

## License

[MIT](LICENSE)
