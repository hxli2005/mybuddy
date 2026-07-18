# 编码 session 协调板

> 这里只放当前施工状态，不放设计。产品和架构以 `DESIGN.md` 为唯一权威，完成
> 历史以 Git提交为准。本文件始终覆盖更新，不追加成长篇开发日志。

## 当前状态

- 当前写入者：无
- 当前任务：无
- 最近完成：S10 真实-key触碰/quiet/ambient三腿验收
- 下一任务：S11 BLOCKED；代码与 zip 已就绪，只差无 Python/.NET/Steam 干净 Windows 截图验收
- 工作区要求：保持提交后干净；新任务先更新本板再领取

## 任务流水线

状态只用 `BLOCKED / READY / ACTIVE / DONE`；同一时刻最多一个编码任务为 ACTIVE。

| ID | 状态 | 纵向闭环 | 依赖 | 完成证据 |
|---|---|---|---|---|
| S0 | DONE | 固定当前文档基线 | 无 | 文档独立提交，`diff --check`通过 |
| S1 | DONE | 经历→一次心智包→四文件提交/拒绝 | S0 | 真实模型输出、四文件、失败原文 |
| S2 | DONE | `/api/body/step`→幂等event→shown确认 | S1 | shown前后history差异、重复请求不重做 |
| S3 | DONE | WPF输入→step→气泡→shown | S2 | 真实窗口台词、断线不补发 |
| S4 | DONE | 时间/生活→baseline→身体持续呈现 | S3 | 当场生活事件、断线安全姿态、恢复基线 |
| S5 | DONE | 触碰反射→原始事实→心智理解 | S4 | 离线不回补、无关系计分、恢复基线 |
| S6 | DONE | presence→ambient→实际显示→shown | S5 | 未显示不入历史、未回应零痕迹 |
| S7 | DONE | 四类记忆与五动词进入同一心智步 | S6 | record/correct/forget及模式证据轨迹 |
| S8 | DONE | 删除旧路径→完整纵向验收 | S7 | 单一写入路径、遗留清单归零、真实轨迹 |
| S9 | DONE | 真实输入→语义证据→真实-key复验 | S8 | UTF-8原文、伪造整包可读拒绝、干净目录真实轨迹 |
| S10 | DONE | 真实-key触碰/quiet/ambient三腿验收 | S9 | 三腿真实轨迹、误伤签名、shown与四文件一致 |
| S11 | BLOCKED | 免费BYOK zip→内置授权素材→单写者首次运行 | S10 | 无Python/.NET/Steam干净机只填key即说出原话；中文用户/中文空格路径/8000占用/Defender说明/双开/退出/崩溃恢复全留证 |

解除依赖时只把下一项改为 READY，不提前铺后续任务。只读评审不占ACTIVE名额，
但不得修改文件；评审意见由当前写入者筛选后实现，不直接写入本板。

## 领取规则

1. 确认依赖任务已经提交，工作区干净。
2. 将一项 READY 改为 ACTIVE，并填写“当前写入者”和“当前任务”；这个领取改动
   与本刀代码放在同一提交，不单独制造协调提交。
3. 本刀只修改完成闭环必需的文件；替换旧路径时同步删除其配置和专属测试。
4. 完成后实际运行并提交代码，将该项改为 DONE、下一项改为 READY，再覆盖下面
   的最近交接。不能完成则改为 BLOCKED，只写一个可验证的具体阻塞条件。

## 最近一次交接

- 任务：S11 免费 BYOK zip 首次运行（代码已就绪，干净机验收阻塞）
- 提交：本提交
- 实现：首次只收 OpenRouter key 并用 Windows CurrentUser DPAPI 加密；WPF 自启 PyInstaller onedir 心智桥；命名 mutex、固定端口、数据目录 OS 锁与父 PID 收尾共同守住单写者
- 诚实连接：响应新增瞬时 `mind_status=not_run/accepted/rejected/unavailable`，模型失败的静态接住不再显示“已连接”
- ambient 裁决：先确认匹配 `shown_id`；无收据且已跨本地日期的 ambient pending 才弃，已提交生活与记忆保留
- 分发：`scripts/build_share.ps1` 产出 `dist/MyBuddy-S11-win-x64.zip`（119.4 MiB），内含自包含 .NET/WPF、自包含 Python 心智桥、248 帧必需 VPet 动画、使用说明与带查阅日期的授权摘要；产物秘钥扫描干净
- 本机验收：Python `53 passed`；Ruff/format/diff 绿；WPF 编译 `0 warnings / 0 errors`；身体/DPAPI `5/5`；已打包引擎 HTTP 启动、第二写者拒绝、父 PID 消失自退、中文空格路径解压与 248 帧计数通过
- 真实 key：`data/mini-s11-package-real/`保留内联 PowerShell 再次把中文烧成问号的失败轨迹，不计成功；`data/mini-s11-package-real-utf8/`从固定 UTF-8 文件读取原文，已打包引擎返回 `accepted` 并 shown
- 她显示的话：“你回来啦。忙完了就好，我一直在呢。”
- 尺寸：形状测试同口径为31个机器侧 Python/C# 文件、3803/5000行，断言通过
- 唯一阻塞：当前机器有 Python/.NET/Steam，且 Codex 进程环境启动 WPF 时系统字体 URI 初始化失败；须在无运行时/无 Steam 的干净 Windows 以截图补齐首次只填 key、中文用户、8000 占用、双开、退出、崩溃恢复与 Defender 全轨迹
- 她哪里更活了：她现在能从一个不带环境的 zip 醒来，同时不会把模型失联说成“我连上了”，也不会在第二天把没说出口的昨日台词冒充“刚刚”

交接只允许保留最近一次。禁止粘贴完整diff、长测试日志、未来设计和“顺便发现”
清单；这些分别属于Git、测试产物、`DESIGN.md`和当前任务之外。
