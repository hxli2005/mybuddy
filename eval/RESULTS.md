# 评测结果日志(RESULTS)

> 每一轮"评测 → 发现 → 优化 → 复测 → 保留版本"都在此追加记录,最新在最上。
> 目标:三维(长期记忆 / 情感支持 / 个性化与主动性)都没有明显短板,并在长期记忆上做出长板。
> 版本保留:每次优化生效后打 git tag(`eval/<维度>-cN`),便于回溯与对比。

指标说明:`Hit@k` = 前 k 条命中任一 gold 的比例;`MRR` = 首个 gold 命中名次的倒数均值。

---

## 第 1 轮 · 长期记忆召回 — 2026-06-17

**数据集**:`eval/data/memory_zh.json`(22 张记忆卡 / 20 条 query;kind = direct 9 / paraphrase 9 / temporal 2)。
**评测脚本**:`uv run python eval/memory_eval.py --mode both`。
**口径**:top_k=5;hybrid 用 OpenRouter `text-embedding-3-small` + RRF(k=60)。

### 基线 vs 优化(同一数据集)

| 桶 | 指标 | 词法基线 | 词法+语义RRF | Δ |
|---|---|---|---|---|
| 总体 | MRR | 0.617 | **0.767** | **+0.150** |
| 总体 | Hit@3 | 0.70 | **0.85** | +0.15 |
| direct | MRR | 0.944 | 1.000 | +0.056 |
| **paraphrase(换词)** | MRR | 0.315 | **0.593** | **+0.278** |
| paraphrase | Hit@3 | 0.444 | **0.778** | +0.334 |
| temporal | MRR | 0.500 | 0.500 | 0 |

### 发现

1. **词法召回在"换词类"query 上明显塌陷**(MRR 0.315):用户换个说法问(如"我能吃虾吗"对应"海鲜过敏")就召回不到。语义 RRF 能补回大半,换词类 MRR 近翻倍。
2. **生产代码没真正用上已配置的语义层**:`config.yaml` 里 `embedding.enabled: true`,自动注入路径(`manager.py`)有传 `use_semantic`,但**`recall_memory` 工具**(用户显式问"你还记得…"时走的路径)调 `ltm.search` 时漏传,跑的是纯词法——等于把上面的 +0.28 白白丢掉。

### 优化(本轮采纳)

- `mybuddy/tools/memory_tool.py`:`recall_memory` 的 `ltm.search(...)` 加 `use_semantic=True`,让显式召回也走词法+语义 RRF 融合。
- 影响面:236 测试全绿;召回质量按上表提升。

### 保留版本

- 标记:`eval/memory-c1`(本轮优化生效后的快照)。

### 仍存在的短板(下一轮候选)

- **时序推理持平(MRR 0.5)**:语义召回对"这周三我有哪些安排"这类时间约束帮助有限——缺显式时序过滤 / 推理。这与公开 benchmark(LoCoMo/LongMemEval)最拉差距的维度一致,是记忆维度后续优化的重点。
- 数据集偏小(20 条),后续可扩到 50+ 并加入多跳题。
