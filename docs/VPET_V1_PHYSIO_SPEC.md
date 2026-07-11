# 生理引擎规格(引擎侧,Python)

> 形态依据:PRODUCT_V1 §3.2「生理真相源在大脑」+ 宪法 5「照顾不勒索」。
> 位置:新包 `mybuddy/body/`(`physio.py` + `murmur.py`)。VPet 游戏数值系统与本引擎无任何关系。
> 状态:`FINAL`(2026-07-11);曲线公式、事务边界、日账本和时钟语义已冻结。

## 1. 三条曲线(0–100,浮点存整型读)

| 曲线 | 演化 | 事件影响 |
|---|---|---|
| `hunger` 饱腹 | 清醒 -`hunger_decay_per_hour`(默认 6.0)/h;睡眠 ×`0.5` | 投喂 +Δ(见协议食物目录);下限 0,**无任何惩罚后果**,只影响渲染与语气 |
| `energy` 精力 | 清醒 -5.0/h;睡眠 +20.0/h | 被吵醒即时 -5;共处会话不额外扣 |
| `mood` 心情 | 向 `mood_baseline`(60)回归,半衰期 8h | 投喂 +2~6(按食物);触摸 +2(日累计上限 +10);完成一次对话 +1(日上限 +5);被吵醒 -5 |

初始值固定为 `hunger=70, energy=70, mood=60`。`mood` 按
`baseline + (old-baseline) * 0.5 ** (hours/half_life)` 指数回归;hunger/energy 线性变化并 clamp 到 0–100。
跨越睡眠边界的时间段必须按本地作息切段积分,禁止用起点或终点状态覆盖整段。

**懒求值,无生理后台 tick**:每次读取按 `now - updated_at` 演化并落库。APScheduler 只负责共处提醒等业务定时,不参与曲线 tick。所有公开方法接受 `now`;默认统一走 `mybuddy._time.utcnow()`。

## 2. 作息

- config `physio.sleep_start="00:30"` / `sleep_end="08:30"`(本地时区,与 `scheduler.quiet_hours` 独立配置但语义呼应;`_in_quiet_hours`(scheduler/jobs.py:255)的解析逻辑可提公用)。
- `sleeping` 为派生状态:只表示当前处于睡眠窗,不会因用户发来消息而变成 false。睡眠中来交互 → `woken=True` 本次快照(mood -5、回复带困意由 living_state 措辞层处理);因此两字段可同时为 true,壳仍保持睡姿。

## 3. 配置(mybuddy/config.py)

```python
class PhysioConfig(BaseModel):
    enabled: bool = False            # v1 壳要求 true;false 时引擎行为与 phase-2 完全一致
    sleep_start: str = "00:30"
    sleep_end: str = "08:30"
    hunger_decay_per_hour: float = 6.0
    mood_baseline: float = 60.0
    mood_half_life_hours: float = 8.0
    murmur_daily_limit: int = 3
```
`Config.physio: PhysioConfig`。**`vpet.body_state_injection` 更名语义**见 EXPERIMENT §2(新键 `vpet.physio_injection`,旧键读到时打一次 deprecation warning 并按新键解释)。

`VPetConfig` 另增 `acceptance_mode: bool = False`;只有它为 true 时才允许非零
`MYBUDDY_TIME_OFFSET_MINUTES`,否则启动即报配置错误并退出。

## 4. 存储

三张表,均由 `init_db/create_all` 创建:

1. `physio_state`(单行):`id=1, hunger REAL, energy REAL, mood REAL, updated_at DATETIME, last_interaction_at DATETIME, woken_until DATETIME, last_levels_json TEXT`。
2. `physio_daily`(每日一行,`local_date` 主键):`touch_mood_gain REAL, chat_mood_gain REAL, touch_count INT, murmur_count INT, feed_items_json TEXT, touch_memory_written BOOL, work_stop_speech_count INT`。
3. `physio_cooldowns`(`kind` 主键):`last_emitted_at DATETIME`;kind 固定 `hunger|energy|mood`。

已有 `vpet_events` 继续作为事件审计账本,每次增量在 `context_json.physio_delta` 保存前后值与实际应用增量。共同记忆的日聚合状态以 `physio_daily` 为幂等锚,不依赖进程内变量。

**原子性合同**:

- `snapshot/apply_*` 全部经过同一个 `_mutate(now, event)`;单次调用在 SQLite `BEGIN IMMEDIATE` 事务内完成“读→演化→日上限→事件增量→写”。
- 事务中不得 `await` 或调用 LLM/Chroma;SQLite busy 使用 50/100/200ms 退避最多 3 次,仍失败则返回 503,不得用旧值继续写。
- `client_event_id` 去重先于 `_mutate`;重复请求返回首次快照,绝不重复加值。
- Chroma shared_moment 写入在数据库事务提交后执行;失败写结构化日志并保留 `vpet_events.context_json.memory_pending=true`,由 Dream Job/启动修复任务补写。

## 5. 对外接口(引擎内部)

```python
class PhysioEngine:
    def __init__(self, engine: Engine, config: PhysioConfig): ...
    def snapshot(self, now: datetime | None = None) -> PhysioSnapshot
        # PhysioSnapshot: hunger/energy/mood(int), sleeping/woken(bool),
        # levels: hungry(<=30)/tired(<=30)/low(<=30)/bright(>=70)
    def apply_feed(self, item_id: str) -> PhysioSnapshot      # 未知 item 按 water 处理
    def apply_touch(self) -> PhysioSnapshot
    def apply_chat(self) -> PhysioSnapshot
```

`snapshot()` 只演化,不把轮询视为互动。`apply_feed/touch/chat()` 才更新
`last_interaction_at`。睡眠窗内且上次互动距今 ≥10min 时,首次互动应用一次 mood -5 并把
`woken_until=now+60s`;其后一分钟的快照可继续返回 `woken=true`,但不得重复扣分。

**消费点**:
- `AppState.vpet_*` 各端点:touch/feed/chat 时调 apply_*;`GET /api/vpet/state` 调 snapshot(协议 v2)。
- `synthesize_living_state(..., physio=snapshot)`:physio.enabled 时**取代** phase-2 的 `body_state` 参数路径——`_life_from_body_state` 改造为 `_life_from_physio`(阈值措辞沿用:饿→"肚子有点空,语气会更黏一点";tired→"有点没力气";sleeping/woken→带困意)。旧 `body_state` 参数保留但 deprecated(处置表)。
- `_body_state_conflicts` 守卫改读 physio snapshot(回归守卫,预期矛盾率≈0)。

## 6. 时钟模拟(验收依赖)

`mybuddy/_time.utcnow` 支持环境变量 `MYBUDDY_TIME_OFFSET_MINUTES`(int,默认 0,仅开发/验收用)。偏移在进程启动时读取并冻结,运行中不热改;改变值必须重启后端。所有 API 返回的 `server_time`、调度器 trigger、day_index、quiet_hours、日账本日期与生理演化都使用这一时钟。

生产启动(`vpet.acceptance_mode=false`)遇到非零偏移必须失败,防止模拟时间污染真实实验。壳不拥有独立模拟时钟,只消费协议的 `server_time` 和派生状态。

## 7. 身体哼唧(murmur.py)

- 触发:snapshot 阈值**穿越**(高→低)时生成候选:饿/困/心情低。模板池只提供语义意图,最终可见文字必须由引擎按 persona 生成或审定;短句、不索取、不愧疚化(宪法 1/5)。`last_levels_json` 记录上次已处理阈值,持续低状态不重复触发。
- 限额:`murmur_daily_limit`(默认 3)/天;同曲线冷却 4h。
- 派送:enqueue 进现有 pending 队列 `source="body_murmur"`,经 drain 走正常在场门控;digest 时可丢弃(≤`greeting_discard_after_minutes` 同规则)。
- 遥测:每条 murmur 记 vpet_events `event="body_murmur"`。

## 8. 测试清单

- 演化:定点 now 注入,三曲线衰减/回归、跨睡眠边界切段、clamp;睡眠窗判定与 woken 一次扣分。
- 事件:feed 按目录 Δ、touch/chat 日上限、跨日重置、重复 `client_event_id` 不重复应用。
- 并发:两个线程同时 feed/touch 无丢更新;busy 重试耗尽返回 503;事务内无网络调用。
- living_state:physio.enabled=false 时输出与 phase-2 逐字节一致(等价性,仿 test_vpet.py 现有模式);enabled+hungry 时含真实措辞且不含"刚吃/杯水"。
- murmur:限额、持久冷却、穿越才触发、重启后不重复。
- 时钟偏移:acceptance 模式生效且需重启;生产模式非零即拒绝启动;API/day_index/日账本同钟。
