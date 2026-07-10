# 小布壳规格(buddyshell/,C# WPF)

> 形态依据:PRODUCT_V1 §3 + 宪法 1/2(一魂一声;壳无策略,只渲染与传感)。
> 新顶层目录 `buddyshell/`,net8.0-windows WPF 应用(非插件)。`vpet-plugin/` 冻结为探针,不改不删。

## 1. 项目结构

```
buddyshell/
  BuddyShell.csproj          # net8.0-windows, UseWPF, VPet-Simulator.Core(spike 通过时)
  App.xaml(.cs)              # 单实例;异常兜底(任何未捕获异常不崩窗,状态点变红)
  MainWindow.xaml(.cs)       # 透明/置顶/无边框/可拖/记住位置/多屏安全
  Anim/IAnimationHost.cs     # 统一动画接口(见 §3)
  Anim/VPetCoreHost.cs       # 实现 A:嵌 VPet.Core graph
  Anim/FramePlayerHost.cs    # 实现 B:自写帧播放器,读同一套素材目录
  Anim/AnimationIntent.cs    # 意图枚举 + ActionMapper 移植(action/expression/idle_hint → 具体动画)
  Bubble.xaml(.cs)           # 气泡:speech.text(≤2句)、interrupt 置顶、persistent 卡片(overdue)
  ChatPanel.xaml(.cs)        # 简易聊天面板:输入 + 最近 20 轮;truncated 时自动展示全文
  FoodTray.xaml(.cs)         # 食盘:五样(协议 §5 目录硬编码),拖到宠物身上 = feed 事件
  TouchLayer.cs              # 头/身两区命中;移植 EventAggregator(30s 聚合/误触过滤/升格启发式)
  Presence.cs                # 移植 PresenceGate:GetLastInputInfo + 全屏检测 + user_back 流程
  Notices.cs                 # 移植 DrainWorker:轮询/digest/overdue/物理主动约束(冷却45min/日上限/今天安静)
  StateLoop.cs               # 新:GET /api/vpet/state 20s 轮询 → 驱动 idle/生理渲染/warmth
  Bridge/*.cs                # 移植 BridgeClient + BridgeModels,升 v2 字段
  Tray.cs                    # 托盘:陪我干活(work_start/stop)/今天安静/设置/这句好·差(POST /api/feedback)/退出
  SettingsWindow.xaml(.cs)   # URL/token/开关镜像/TTS(占位,默认关不实现)/时钟偏移(验收用)
  assets/pet/                # VPet 默认宠物素材拷贝(见 §4 授权义务)
```

## 2. 行为规格(逐条对宪法)

- **沉默默认(宪法 3)**:无事件时壳只做 idle 渲染;一切开口都来自引擎响应或 drain。壳内**不存在**任何本地台词表。
- **打扰贵于错过(宪法 4)**:Notices 的物理主动约束沿用 phase-2 裁决(全屏/演示静默、45min 冷却、日上限、"今天安静");digest 一句 + overdue 持久卡,永不逐条轰炸。
- **渲染生理(§3.2)**:StateLoop 按 `physio.levels` 切基线姿态(hungry→蔫;tired→打盹倾向;bright→轻快);`sleeping=true` → 睡觉动画 + 拦截 drain 播报(攒到醒);`idle_hint` 驱动闲时动作,优先级 sleep > work > hint。
- **投喂**:拖拽食物 → 播吃动画(本地即时,反射层)→ 异步 POST feed → 响应若带语音则气泡。断网时吃动画照播,事件入本地重发队列(至多存 24h)。
- **触摸**:命中即播原生反射动画(素材自带);升格申请逻辑 = 移植 EventAggregator 原样(当天首次/30s count≥5,`want_reply=true`,预算与批准在引擎)。
- **聊天**:发送瞬间播 thinking 动画(延迟掩蔽);超时 15s 气泡示歉 + 状态点黄;token 401 → 状态点红 + 设置窗提示。**任何网络失败不冻结 UI**。
- **共处**:托盘"陪我干活"→ work_start,宠物进 work 姿态;再点结束 → work_stop,气泡收尾语。
- **反馈**:托盘"这句好/差"→ POST /api/feedback(phase-2 端点零改动),常开基础设施。

## 3. IAnimationHost(spike 的隔离层)

```csharp
public interface IAnimationHost {
    void Play(AnimationIntent intent, bool loop = false);   // Idle/Read/Write/Nap/Gaze/Stretch/Work/Sleep/
                                                            // TouchHeadReflex/TouchBodyReflex/Eat/Think/
                                                            // Happy/Sad/Worried/Alert/Neutral...
    void SetBaseline(PhysioLevels levels, double warmth);   // 姿态基线
    event EventHandler<TouchZone> TouchDetected;            // 头/身命中(含命中区几何)
}
```
- **实现 A `VPetCoreHost`**(7/12 spike 通过时):包 `VPet-Simulator.Core` 的 graph/display,把 intent 映射到 graph 动画名;touch 区取 Core 的判定或按素材元数据自算。
- **实现 B `FramePlayerHost`**(spike 失败时):自写帧序列播放器,直接读 `assets/pet/` 的 VPet 素材目录结构(png 帧 + 目录名即状态),intent→目录映射表可配置。
- 除这两个文件外,**全壳代码不得 import 任何 VPet 命名空间**——spike 结果只波及一个文件。

## 4. 素材与授权(义务,不可省)

`assets/pet/` 拷贝 VPet 默认宠物动画;设置窗"关于"页 + 仓库 README 注明:素材版权归虚拟主播模拟器制作组,非商用使用,附 GitHub 链接。商业化前需邮件授权(PRODUCT_V1 §4)。

## 5. 设置持久化

`%APPDATA%/BuddyShell/settings.json`(URL/token/开关镜像/位置/今天安静的当日日期)。开关镜像随每个请求带 `X-MyBuddy-Client-Flags`(phase-2 机制)。

## 6. 移植映射(来源 vpet-plugin/,处置表联动)

| 来源 | 去向 | 改动 |
|---|---|---|
| BridgeModels.cs | Bridge/ | +v2 字段(state/physio/idle_hint/warmth/truncated) |
| BridgeClient.cs | Bridge/ | +GET state;端点常量升 v2 |
| EventAggregator.cs | TouchLayer.cs | 事件源从 VPet 事件改为 IAnimationHost.TouchDetected |
| PresenceGate.cs | Presence.cs | 原样 + 全屏检测保留 |
| DrainWorker.cs | Notices.cs | 展示层从 VPet Say 改为 Bubble;约束逻辑原样 |
| ActionMapper.cs | Anim/AnimationIntent.cs | 目标从 VPet 动作名改为 intent 枚举 |
| MyBuddyPlugin/BridgePlugin/TalkAPI/VPetHostAdapter | **不移植**(退役,见处置表) | — |

## 7. 验收钩子

- 时钟偏移设置(SettingsWindow)与引擎 `MYBUDDY_TIME_OFFSET_MINUTES` 配套,ACCEPTANCE 用。
- 全局日志 `%APPDATA%/BuddyShell/logs/`,证据包引用。
