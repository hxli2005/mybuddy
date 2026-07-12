# BuddyShell 动画状态机集中验收

> 自动采集：完成（2026-07-12）
> 人工视觉结论：`PASS`
> Gate A：`PASS`

本页只验动画整改，不重复六拍业务验收。自动证据位于 [`automated/`](automated/)，总结果见 [`RESULT.json`](automated/RESULT.json)。自动测试证明转场顺序、素材路径、图层同步、去重、恢复目标和首帧耗时；人工只判断动作观感是否完整、自然。

| 编号 | 动作链 | 人工必须观察 | 截图与日志 |
|---|---|---|---|
| V1 | Idle→Sleep→Idle | 拉被子 A 只一次；B 稳定循环；起床 C 完整 | [`V1`](automated/V1-idle-sleep-idle/) |
| V2 | Idle→Think→Reply→Idle | 挠头 A 不重复；收到回复后先播 Think C，再做回复表情 | [`V2`](automated/V2-think-reply/) |
| V3 | Idle→Work→Stop→Idle | 桌子只落一次、只收一次 | [`V3`](automated/V3-work-stop/) |
| V4 | Work→TouchHead→Work | 触摸 A/B/C 完整；结束后重播 Work A 落桌，再进入写字 B | [`V4`](automated/V4-work-touch-work/) |
| V5 | Work→Feed→Work | 食物在身体前后层之间；结束后重播 Work A 重建桌子，再进入 B | [`V5`](automated/V5-work-feed-work/) |
| V6 | Think→Touch→Think→Reply | 触摸完整；未回复时回 Think B；回复到达后 Think C 再表情 | [`V6`](automated/V6-think-touch-reply/) |
| V7 | Sleep→Touch→Sleep | 触摸完整；结束后重播 Sleep A 重建被子场景，再进入 B | [`V7`](automated/V7-sleep-touch-sleep/) |
| V8 | Eat/Drink 五种 item | 五种食物沿手部轨迹移动；Eat 宽度 57–65/500、Drink 77–78/500；遮挡关系可接受 | [`V8`](automated/V8-five-foods/) |
| V9 | 断网触摸/投喂 | 本地动作仍完整并恢复；窗口不卡死 | [`V9`](automated/V9-offline-local-completion/) |
| V10 | 快速点击 10 次 | 只出现一个有效视觉 session，不叠播 | [`V10`](automated/V10-rapid-touch-dedup/) |

量化证据：

- 首次触摸帧：[`first-frame-latency.json`](automated/V10-rapid-touch-dedup/first-frame-latency.json)，门槛 `<100ms`；
- 多层漂移：[`RESULT.json`](automated/RESULT.json) 中 `layer_drift_ms=0`；
- 空白合成帧：`blank_composited_frames=0`；
- 每条场景目录的 `transition-log.json` 记录 plan、phase、generation、correlation、恢复目标与相对素材路径；
- 最终窗口 soak：[`runtime-soak-final.json`](runtime-soak-final.json)；
- 最终真实随机动作 soak：[`real-random-soak.json`](real-random-soak.json)。

## 人工签字

请在最终 Release 窗口集中操作后填写：

- [x] 动作完整，无重复起手、半途站住或错误恢复；
- [x] A→B→C 无明显闪白、跳站；
- [x] 工作、睡眠、thinking 被打断后恢复正确；
- [x] 食物大小与前后遮挡可接受；
- [x] 快速点击不叠播，窗口始终响应；
- [x] 未发现新的 P0/P1 动画问题。

人工结论：`PASS`（用户反馈“测下来没明显问题”）
验收人：用户，本轮集中验收
验收时间：2026-07-12
P2 记录：主窗体积与视觉问题另列 UI 整改，不阻塞动画 Gate；已改为紧凑抽屉式布局。
