# VPet v1 编码完成审计

审计时间:2026-07-11。本文只签“编码与自动验证”，不代替 `VPET_ACCEPTANCE.md` 要求的人眼、真时与连续日历证据。

| 路径 | 已实现 | 自动证据 | 尚需现场证据 |
|---|---|---|---|
| 拍1 回场问候 | Presence 30min 离场门、`user_back` 独立生成、近期真实话题提示、digest 优先且实际展示后 ack | `test_user_back_generates_memory_aware_greeting_without_touch_budget`；WPF Release build | 离开→回来录屏、实际话题体验签署 |
| 拍2 安静 idle | state 优先级 `sleep > work > physio/living_state`；read/write/gaze/stretch 动画；无本地台词 | `test_vpet_state_shape_and_idle_priority`；Core 30min spike | 3min 无交互 + 10s 动画录屏 |
| 拍3 触摸 | 按下即时反射；短按/移动/长按过滤；头身分区；当天首次与 30s 第5次服务端复核 | `test_touch_restart_cannot_repeat_first_touch_escalation`；spike 命中区证据 | 两次触摸录屏与 `<100ms` 帧判 |
| 拍4 投喂记忆 | 五食物目录、幂等生理增量、日聚合 shared_moment、失败启动修复、聊天记忆回流 | `test_feed_updates_physio_once_and_aggregates_shared_moment`、`test_failed_feed_memory_is_repaired_from_committed_event` | 拖拽录屏、晚间至多两轮回流截图 |
| 拍5 回场 digest | stale greeting 丢弃、nudge digest、overdue 持久卡、murmur 丢弃、展示 ack | `test_vpet_pending_drain_digest_three_way` | 偏移版录屏 + 一次真实3h复验 |
| 拍6 困意与晚安 | 睡眠窗切段、`sleeping+woken`、夜间 pending 不 drain、壳睡姿与主动门控 | `test_evolution_splits_across_both_sleep_boundaries`、`test_vpet_chat_does_not_drain_pending_while_physio_is_sleeping` | 00:40 对话/睡姿录屏 + 一次真时复验 |

## 横向合同

- 双服务路径:FastAPI 与 `web.py` 均实现 bridge v2；FastAPI `Request` 解析与 400 错误形状有回归测试。
- 幂等与并发:SQLite `BEGIN IMMEDIATE`、busy 退避/503、并发触摸/投喂、并发 `client_event_id` 均有测试。
- 时钟:生产拒绝偏移；state/日账本/调度器统一使用模拟服务端钟，APScheduler 触发会换算回真实 UTC。
- 壳边界:除 `VPetCoreHost.cs` 外无 VPet 命名空间；chat 不重放，event outbox 指数退避且24h dead-letter。
- 发布防伪:`vpet_acceptance_capture.py` 初始固定 FAIL；`vpet_acceptance_verify.py` 拒绝未审计 PASS；冻结缺 `RESULT.json` 直接失败。

## 当前自动验证

- Python:`310 passed`
- Ruff:`All checks passed`
- WPF Release:`0 warnings / 0 errors`
- Core spike:`1800s`,`unhandled_exceptions=0`，见 `spike/decision.md`
- 可执行路径:`dist/BuddyShell/BuddyShell.exe`
- 发布 DLL SHA-256:`cf9bb30af7a862294d938b09cfaae8c63a44c379d1e4cb175595de6582d9dc29`

当前不具备 `FULL` 发布签署条件：六拍屏幕证据、拍5/6真时复验、2026-07-25..31 七个有效日周检尚未按真实时间发生。
