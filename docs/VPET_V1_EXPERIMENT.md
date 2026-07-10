# 两周实验设计 v2(O2 语义重推导 + 预注册)

> 取代 VPET_PHASE2_SPEC.md §2(其第一变量 `body_state_injection` 的上行语义已被"生理入脑"废除)。
> 冻结日(7/31)前本文件填毕即视为**预注册**;此后改动 = 协议偏离,记入 deviation log。

## 1. 实验窗

2026-08-02 → 08-17(14 天)。08-02/03 为适应期(acclimation),单独标注不进主分析。产品冻结于 tag `v1.0`,config 哈希记录于 §6。

## 2. 变量(v2 语义)

| 开关 | 角色 | 排期 |
|---|---|---|
| `vpet.physio_injection`(新键,取代 body_state_injection) | **一致性护栏,常开,不进 ABAB**;度量 = 矛盾事件率(`_body_state_conflicts` 读 physio) | 全程 on |
| `vpet.touch_escalation` | 消融变量 A | **按天交替**,起始值 = 掷硬币(§6 记录) |
| `vpet.physical_proactive` | 消融变量 B | **按周切**(week1/week2),顺序 = 掷硬币 |
| murmur/共处/idle_hint/记忆回流 等 v1 新能力 | 常开,不进消融矩阵 | 全程 on |

## 3. 有效日与数据规则

- **有效日**:当日 Windows 在场 ≥2h(以 vpet_events 的 presence 相关事件 + user_back 间隔推算;SQL 见 §7)。无效日数据保留但标记,不进比率类指标。
- 每日 `day_index` 已由引擎落遥测;分析把 day_index 作协变量(前/后半对照新鲜感)。
- **变更控制**:实验期仅 crash/丢数据可 hotfix;每次 hotfix 记 deviation log(日期/改动/影响面),当日标"污染日"。其余一切改动等 8/18。

## 4. 指标(全部可 SQL,口径锁定)

| 指标 | 口径 |
|---|---|
| nudge 响应率 | `messages` 中 `meta.pending_source in ('nudge','dynamic')` 的 assistant 行,同 session 10 分钟内出现 user 行的比例 |
| 打扰率 | 物理主动展示后 5 分钟内用户无任何交互且当日日记标"被打扰"的次数 / 物理主动总次数 |
| 触摸曲线 | 日触摸事件数与升格占比,按 touch_escalation 条件分组 |
| 矛盾事件率 | `vpet_events.event='body_state_conflict'` 每百条回复(预期≈0,回归守卫) |
| 记忆回流接住率 | shared_moment 被引用的回复后,用户回应中呼应该记忆的比例(人工标注,样本小,配日记) |
| 在场价值 | 日互动次数、非用户先开口的互动占比、共处 session 数与完成率 |

## 5. 日记与盲评

- **日记**:每日 ≤5 分钟,固定四问(今天她哪一刻最像"人"?哪一刻出戏?被打扰了吗?明天还想让她开着吗?)存 `study-notes` 之外的 `eval/diary/`(入库,论文可引)。
- **盲评 v0**:实验结束后,按 turn_id 伪随机(种子=冻结 tag 哈希)抽 A/B 条件各 10 段对话,去时间戳去条件标签,2–3 位朋友按 1–7 分人味盲评;协议细则(指导语/顺序随机)冻结前定稿于本节。

## 6. 预注册记录(冻结日填写)

```
冻结 tag:v1.0(commit ___)   config 哈希:___
掷硬币:touch_escalation 起始 = ___;physical_proactive week1 = ___
六拍验收证据包路径:___      已知偏离:___
```

## 7. 有效日 SQL(口径附录)

```sql
-- 当日在场分钟数(近似):user_back 到下一次 idle 暂停之间的窗口和
SELECT date(created_at) d, ... FROM vpet_events WHERE event IN ('user_back','pending_drained') ...
-- 编码期要求:确保 presence 相关事件足以还原在场区间;不足则在 StateLoop 轮询里
-- 每 20 分钟落一条 event='presence_heartbeat'(count=在场分钟),本条为编码约束。
```
**编码约束**:壳每 20 分钟在场心跳 `presence_heartbeat` 落遥测——没有它,有效日规则无法执行。
