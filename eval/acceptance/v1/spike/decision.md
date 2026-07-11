# VPet 动画宿主 spike 裁决

- 裁决时间:2026-07-11T13:10:33+08:00
- 结论:`PASS — 锁定 VPetCoreHost`
- 证据:[`core-evidence.json`](core-evidence.json)
- 证据 SHA-256:`ce87aedc35d03d16688160f5b86c368eb3791d1b518cb78fd201d0371d842988`

四项门槛均通过:

1. 默认素材可见:从 Steam 默认 `0000_core/pet/vup` 成功加载,当前动画 8 帧;Windows 可见窗口枚举为 `461118|小布`。
2. idle 连续播放:诊断接口在启动与 30 分钟检查点均报告 `idle_playing=true`;窗口进程始终 `Responding=true`。
3. 头/身命中可区分:与真实事件处理共用的命中函数分别返回 `Head`、`Body`,且运行期间真实 `touch_head` 已成功上报。
4. 30 分钟稳定:从 12:40:32 到 13:10:32 连续 1800 秒,`unhandled_exceptions=0`,`stable_30_minutes=true`。

因此 v1 固定走 `VPetCoreHost`;`FramePlayerHost` 仅保留为设置项可选的兼容降级,不再重开宿主路线讨论。
