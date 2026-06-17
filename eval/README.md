# MyBuddy 评测框架

围绕三个能力维度做"评测驱动优化",每轮记录结果、优化生效后保留版本(见 `RESULTS.md`)。
目标:三维都没有明显短板,并在**长期记忆**上做出长板。

## 三个维度与对标

| 维度 | 评什么 | 对标的公开方法 | 本仓库实现 |
|---|---|---|---|
| 长期记忆 | 召回质量:直问 / 换词 / 时序 / 多跳 | LoCoMo、LongMemEval、DMR | 自建集 `memory_eval.py`(Hit@k/MRR)+ 公开基准 `locomo_eval.py`(LoCoMo 检索召回) |
| 情感支持 | 共情 / 有用性 / 策略恰当度 | ESConv、HEART | 计划:LLM-as-judge 场景集 |
| 个性化与主动性 | 人格一致性 / 画像利用 / nudge 时机 | PersonaLens、ProactiveEval | 计划:LLM-as-judge 场景集 |

> 公开 benchmark 多为英文 QA,与"中文陪伴"场景不完全对齐;这里用自建中文小集做可复现的相对评测(消融、前后对比),而非追求绝对刷榜分数。跨厂商绝对分不可直接比(模型 / prompt / judge 口径不同)。

## 已实现:长期记忆召回(`memory_eval.py`)

```bash
uv run python eval/memory_eval.py --mode lexical   # 纯词法基线(离线)
uv run python eval/memory_eval.py --mode both      # 词法 vs 词法+语义RRF(hybrid 需联网做 embedding)
```

- 数据:`data/memory_zh.json`(37 卡 / 52 query)——`corpus` 为记忆卡(`days_ago` 设时间差,供时序题),`queries.gold` 为应被召回的卡片 id;`kind` ∈ {direct, paraphrase, temporal, multihop}。
- 指标:Hit@1 / Hit@3 / **Recall@5**(多 gold 覆盖率)/ MRR,总分并按 kind 分桶。
- 在临时目录里独立建库,不污染 `data/`。
- 召回侧已实现:语义 RRF 融合(c1)、时态感知 top-k 重排(c2,`最近/现在/以前` → recency 偏新/旧)。

## 公开基准:LoCoMo 检索召回(`locomo_eval.py`)

```bash
# 数据集不入库,先下载(2.7MB)
mkdir -p eval/data/external
curl -L -o eval/data/external/locomo10.json \
  https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

uv run python eval/locomo_eval.py --samples 2 --tiers 5,10,20
```

- 测的是**检索召回**(证据 turn 是否进 top-k),按 LoCoMo 的 category 分桶(单跳/多跳/时序/开放域;对抗类排除)。
- ⚠️ 这**不等于** LoCoMo 排行榜的端到端问答分(那是 LLM-judge 打答案正确率),口径不同、不可直接比。turn 级检索(数百选 k)是偏难设定。
- 最新结果与解读见 `RESULTS.md` 第 3 轮。

## 记录与版本约定

- 每轮结果追加到 `RESULTS.md`(最新在最上),含:数据集、口径、基线 vs 优化、发现、采纳的优化、保留版本、仍存在的短板。
- 优化生效后打 tag:`eval/<维度>-cN`(如 `eval/memory-c1`),作为可回溯的"保留版本"。

## 待补维度(脚手架)

- `emotion_eval.py`(情感支持):一组带情绪的用户输入场景,跑 `EmotionDetector` + 对话回复,用 LLM-as-judge 打 共情 / 有用性 / 是否过度共情。
- `persona_eval.py`(个性化与主动性):带画像与历史的多轮场景,judge 人格一致性、是否正确利用画像、主动 nudge 触发是否恰当。
