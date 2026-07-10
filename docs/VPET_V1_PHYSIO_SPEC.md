# 生理引擎规格(引擎侧,Python)

> 形态依据:PRODUCT_V1 §3.2「生理真相源在大脑」+ 宪法 5「照顾不勒索」。
> 位置:新包 `mybuddy/body/`(`physio.py` + `murmur.py`)。VPet 游戏数值系统与本引擎无任何关系。

## 1. 三条曲线(0–100,浮点存整型读)

| 曲线 | 演化 | 事件影响 |
|---|---|---|
| `hunger` 饱腹 | 清醒 -`hunger_decay_per_hour`(默认 6.0)/h;睡眠 ×`0.5` | 投喂 +Δ(见协议食物目录);下限 0,**无任何惩罚后果**,只影响渲染与语气 |
| `energy` 精力 | 清醒 -5.0/h;睡眠 +20.0/h | 被吵醒即时 -5;共处会话不额外扣 |
| `mood` 心情 | 向 `mood_baseline`(60)回归,半衰期 8h | 投喂 +2~6(按食物);触摸 +2(日累计上限 +10);完成一次对话 +1(日上限 +5);被吵醒 -5 |

**懒求值,无后台任务**:状态存 `(hunger, energy, mood, updated_at)`;每次读取按 `now - updated_at` 应用演化并落库。所有函数接受注入的 `now`(测试仿 `tools/reminder._local_now` 的 monkeypatch 模式)。时间统一走 `mybuddy._time.utcnow`(见 §6 时钟模拟)。

## 2. 作息

- config `physio.sleep_start="00:30"` / `sleep_end="08:30"`(本地时区,与 `scheduler.quiet_hours` 独立配置但语义呼应;`_in_quiet_hours`(scheduler/jobs.py:255)的解析逻辑可提公用)。
- `sleeping` 为派生状态:处于睡眠窗且**近 10 分钟无交互**。睡眠中来交互 → `woken=True` 本次快照(mood -5、回复带困意由 living_state 措辞层处理)。

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

## 4. 存储

新表 `physio_state`(单行):`id=1, hunger REAL, energy REAL, mood REAL, updated_at DATETIME`,init_db create_all 自动建。事件影响不单独建表——已有 `vpet_events` 记录 feed/touch(phase-2 就位),曲线增量在 `context_json` 附 `physio_delta` 字段。

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

**消费点**:
- `AppState.vpet_*` 各端点:touch/feed/chat 时调 apply_*;`GET /api/vpet/state` 调 snapshot(协议 v2)。
- `synthesize_living_state(..., physio=snapshot)`:physio.enabled 时**取代** phase-2 的 `body_state` 参数路径——`_life_from_body_state` 改造为 `_life_from_physio`(阈值措辞沿用:饿→"肚子有点空,语气会更黏一点";tired→"有点没力气";sleeping/woken→带困意)。旧 `body_state` 参数保留但 deprecated(处置表)。
- `_body_state_conflicts` 守卫改读 physio snapshot(回归守卫,预期矛盾率≈0)。

## 6. 时钟模拟(验收依赖)

`mybuddy/_time.utcnow` 支持环境变量 `MYBUDDY_TIME_OFFSET_MINUTES`(int,默认 0,仅开发/验收用,README 注明生产不设)。全引擎时间已单点走 `_time`,此改动即全局生效。

## 7. 身体哼唧(murmur.py)

- 触发:snapshot 阈值**穿越**(高→低)时生成候选:饿/困/心情低,各自模板池 3–5 条,**小布 persona 口吻**(短句、不索取、不愧疚化——宪法 5:"有点饿了…不急"而非"再不喂我就要饿死了")。
- 限额:`murmur_daily_limit`(默认 3)/天;同曲线冷却 4h。
- 派送:enqueue 进现有 pending 队列 `source="body_murmur"`,经 drain 走正常在场门控;digest 时可丢弃(≤`greeting_discard_after_minutes` 同规则)。
- 遥测:每条 murmur 记 vpet_events `event="body_murmur"`。

## 8. 测试清单

- 演化:定点 now 注入,hunger/energy/mood 各验衰减与回归;睡眠窗判定与 woken。
- 事件:feed 按目录 Δ、touch 日上限、chat 日上限。
- living_state:physio.enabled=false 时输出与 phase-2 逐字节一致(等价性,仿 test_vpet.py 现有模式);enabled+hungry 时含真实措辞且不含"刚吃/杯水"。
- murmur:限额、冷却、穿越才触发(持续低不重复)。
- 时钟偏移:offset 环境变量生效。
