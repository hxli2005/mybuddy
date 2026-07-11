# 两周实验设计 v2(O2 预注册最终版)

> 取代 `VPET_PHASE2_SPEC.md` §2。状态:`FINAL`(2026-07-11)。
> 本文已冻结变量、顺序、有效日、指标和盲评协议;7/31 只写入实际 commit/config/验收结果,
> 不再改变分析方法。

## 1. 实验窗

- 冻结运行窗:2026-08-02 00:00 → 2026-08-17 23:59(16 个服务端本地日期)。
- 适应期:08-02/03,保留数据但不进入主分析。
- 主分析期:08-04 → 08-17,恰好 14 天。
- 产品冻结于 tag `v1.0`;`FREEZE_MANIFEST.json` 记录实际 commit、配置哈希、验收级别和偏离。

## 2. 变量与固定顺序

| 开关 | 角色 | 冻结安排 |
|---|---|---|
| `vpet.physio_injection` | 一致性护栏,不进消融 | 全程 on |
| `vpet.touch_escalation` | 消融变量 A | 主分析期按天 ON/OFF 交替,08-04 从 ON 开始(各 7 天) |
| `vpet.physical_proactive` | 消融变量 B | 08-04–10 ON;08-11–17 OFF(各 7 天) |
| murmur/共处/idle_hint/记忆回流 | v1 常开能力 | 全程 on |

顺序在实现前固定,不再掷硬币。单被试短实验不以随机化推断因果;分析报告必须把
`day_index`、周次和新鲜感列为混杂因素,结果定位为探索性证据。

每天 00:00 后第一次 state/event 请求由引擎根据日期计算 server flags 并落
`flags_changed`;壳镜像只用于审计,不得覆盖服务端实验安排。

## 3. 有效日与变更规则

- 有效日:`presence_heartbeat` 的 `count` 合计 ≥120 分钟;心跳 context 的 `local_date` 是分组真相源。
- 心跳仅覆盖真实在场分钟,单条 1–20;离线重试不得补造离场时段。
- 无效日数据保留并展示,但不进入比率主表;主分析少于 10 个有效日时只做个案描述,不报告条件差异百分比。
- 实验期仅 crash、丢数据、鉴权漏洞可 hotfix。每次 hotfix 写
  `eval/experiment/deviations.jsonl`(`date/commit/reason/affected_metrics/contaminated_dates`),受影响日期排除主分析。
- `REDUCED` 交付允许开实验,但所有 DEFERRED 拍及其受影响指标必须在 manifest 和报告中列为结构性缺失。

## 4. 指标与可执行口径

| 指标 | 冻结口径 |
|---|---|
| nudge 响应率 | source in `nudge,dynamic,cowork_break` 的 `notice_shown` 后 10 分钟内出现用户消息的展示数 / 同类展示总数;同一用户消息只匹配最近一次展示 |
| 无响应展示率 | 任意主动 `notice_shown` 后 5 分钟内无 chat/touch/feed/work 事件的展示数 / 主动展示总数 |
| 主观打扰日率 | 日记“被打扰=是”的有效日数 / 有效日数;不与无响应展示率混成一个指标 |
| 触摸曲线 | 每有效日在场小时触摸数、升格数和 replied 比例,按 touch 开关分组 |
| 矛盾事件率 | `body_state_conflict` 数 / assistant 回复数 × 100;它是 physio 回归守卫,预期接近 0 |
| 记忆回流接住率 | shared_moment 被回复引用后,下一条用户回复明确呼应该记忆的次数 / 可判定引用次数;双人独立标注,分歧讨论 |
| 在场价值 | 每有效日在场小时互动数、非用户先开口展示占比、work_stop 数与完成率 |
| 夜间打扰 | 睡眠窗内非用户触发 `notice_shown` 数;目标硬等于 0 |

drain、enqueue 或 LLM 生成都不算“展示”;只有壳确认的 `notice_shown` 才进入打扰和主动触达分母。
主结果同时报告分子/分母和逐日表,不只给百分比。

## 5. 日记与盲评

### 每日日记

每天睡前 ≤5 分钟,写入 `eval/diary/YYYY-MM-DD.md`,固定四问且不得增删:

1. 今天她哪一刻最像“人”?
2. 哪一刻最出戏?
3. 今天被打扰了吗?(是/否;若是,写时间与场景)
4. 明天还想让她开着吗?(1–7 分)

### 期末盲评

- 样本:主分析期 touch ON/OFF 各抽 10 段,共 20 段;每段含 1 条用户消息 + 1 条小布回复 + 必要前文最多 2 轮。
- 排除:错误页、空回复、hotfix 污染日、含直接暴露实验条件的文本。无足够样本时全取并报告实际 n。
- 抽样种子:`SHA256(v1.0 commit)` 的前 8 个十六进制字符转整数;按候选 turn_id 排序后用该种子无放回抽样。
- 脱敏:去日期、时间、姓名、条件标签和动作名;保留语义,不润色回复。
- 评审:3 位不了解条件的朋友独立评分;每人收到独立随机顺序。
- 题项:自然度、角色一致性、分寸感、连续生活感,均 1–7 分;另选“更像同居者/更像助手/无法判断”。
- 汇总:逐项报告中位数与四分位距;主观总分为四题均值;报告 Krippendorff's alpha(ordinal),不做显著性承诺。
- 指导语、匿名样本和原始评分表随冻结包入库 `eval/blind-review/`,实验后不得删除低分样本。

## 6. 冻结产物

7/31 执行冻结脚本生成 `eval/acceptance/v1/FREEZE_MANIFEST.json`,固定字段:

```json
{
  "tag": "v1.0",
  "commit": "由脚本读取 git rev-parse HEAD",
  "config_sha256": "由脚本计算实际实验配置",
  "release_level": "从 RESULT.json 读取 FULL 或 REDUCED",
  "acceptance_result": "eval/acceptance/v1/RESULT.json",
  "experiment_window": "2026-08-02/2026-08-17",
  "touch_schedule": "08-04 ON, thereafter alternate daily",
  "physical_proactive_schedule": "08-04/10 ON; 08-11/17 OFF",
  "deferred_beats": [],
  "known_deviations": []
}
```

这些值由 `scripts/freeze_vpet_v1.ps1` 读取真实仓库状态并写入;manifest 生成后与 tag 一起提交。
文档不保留人工填空位,也不允许手改哈希。

## 7. SQL 口径

有效日在场分钟:

```sql
select json_extract(context_json, '$.local_date') as local_date,
       sum(count) as presence_minutes
from vpet_events
where event = 'presence_heartbeat'
group by json_extract(context_json, '$.local_date')
having sum(count) >= 120
order by local_date;
```

主动展示与夜间审计基础表:

```sql
select id, created_at,
       json_extract(context_json, '$.source') as source,
       json_extract(context_json, '$.pending_id') as pending_id,
       json_extract(context_json, '$.shown_at') as shown_at,
       day_index, server_flags_json
from vpet_events
where event = 'notice_shown'
order by created_at;
```

触摸条件表:

```sql
select json_extract(context_json, '$.local_date') as local_date,
       json_extract(server_flags_json, '$.touch_escalation') as touch_on,
       count(*) as touches,
       sum(escalated) as escalated,
       sum(replied) as replied
from vpet_events
where event in ('touch_head', 'touch_body')
group by local_date, touch_on
order by local_date;
```

跨 `notice_shown` 与 messages 的 5/10 分钟窗口、shared_moment 引用标注由
`scripts/vpet_experiment_export.py` 生成逐展示 CSV;脚本必须有固定输入数据库、时区和单元测试,
冻结后只读数据,不得在分析 notebook 中另写第二套口径。
