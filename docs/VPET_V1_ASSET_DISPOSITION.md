# phase-2 资产处置表(PM 轮批准条件 #5)

> 原则:O1 验证性一跳已证明后端桥是活的——**后端近乎全保**;报废集中在 C# 宿主绑定与 body_state 上行语义。
> `retire` ≠ 立删:v1 冻结前只标记不删除;`vpet-plugin/` 整体冻结为后端调试探针,**v1 交付后**再删。

## Python(mybuddy/)

| 模块/函数 | 处置 | 说明 |
|---|---|---|
| token 鉴权(web.py `_authorize_request` / api.py middleware) | **keep** | 原样服务壳 |
| `/api/vpet/chat`、升格批准链、`agent_lock`、`_short_vpet_reaction`、`_vpet_day_index`、事件模板 | **keep** | 协议 v2 仅增量(truncated/两句裁剪) |
| drain/digest/overdue 全套 | **keep** | v2 新增两个 source 的分支 |
| vpet_events 遥测 + flags 快照 | **keep** | 字段够用,新事件直接复用 |
| `Agent.run(source/enable_tools/meta)` | **keep** | cowork 收尾语复用 |
| `normalize_body_state` | **adapt→仅容错** | 只剩"接受并忽略旧客户端字段"用途;不再进任何语义路径 |
| `chat_payload/vpet_chat_payload/Agent.run` 的 `body_state` 透传链 | **retire(标记)** | 参数保留一版打 deprecation,physio 参数取代;v1.1 清理 |
| `synthesize_living_state(body_state=...)` + `_life_from_body_state` | **adapt** | 改造为 `physio=` + `_life_from_physio`(PHYSIO §5);措辞资产全保留 |
| `_body_state_conflicts` 守卫 | **adapt** | 数据源改 physio snapshot;角色降为回归守卫 |
| `VPetConfig.body_state_injection` | **adapt** | 新键 `vpet.physio_injection`,旧键别名+警告(EXPERIMENT §2) |

## C#(vpet-plugin/ → buddyshell/)

| 文件 | 处置 | 去向 |
|---|---|---|
| BridgeModels / BridgeClient / EventAggregator / PresenceGate / DrainWorker / ActionMapper / SettingsView | **port** | SHELL_SPEC §6 映射表 |
| VPetHostAdapter(415 行)/ MyBuddyBridgePlugin / MyBuddyTalkAPI / mod/1114_MyBuddyBridge | **retire** | VPet 宿主专属;探针期冻结,v1 后删 |
| MyBuddy.VPetPlugin.csproj | **retire** | 同上 |

## 测试(tests/)

| 测试 | 处置 |
|---|---|
| test_vpet.py:gate 三态/dedup/drain 三分/token/telemetry | **keep**(语义不变) |
| test_vpet.py:body_state 注入链三态、living_state 让位(body_state 版) | **adapt** → physio 版等价测试(PHYSIO §8),旧测试改为"deprecated 字段被忽略" |
| test_web.py | **不许碰**(KICKOFF 铁律 2) |
| 新增 | test_physio.py、协议 v2 测试(PROTOCOL §8) |

## 文档

| 文档 | 处置 |
|---|---|
| VPET_PHASE2_SPEC.md / RUNBOOK / MANUAL_QA | **keep as history**,页首加一行"v1 起以 VPET_V1_* 规格包为准" |
| VPET.md(对外集成说明) | **adapt**(v1 冻结后重写为壳的使用说明,编码期不动) |
