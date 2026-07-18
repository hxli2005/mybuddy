# 编码 session 协调板

> 这里只放当前施工状态，不放设计。产品和架构以 `DESIGN.md` 为唯一权威，完成
> 历史以 Git提交为准。本文件始终覆盖更新，不追加成长篇开发日志。

## 当前状态

- 当前写入者：无
- 当前任务：无
- 最近完成：S10 真实-key触碰/quiet/ambient三腿验收
- 下一任务：S11 BLOCKED；陈旧 ambient 在重启后“弃或送”待所有者一句话裁决
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

- 任务：S10 真实-key触碰/quiet/ambient三腿验收
- 提交：本提交
- 跑过的命令：Python `45 passed`；ruff、format、diff检查；WPF编译`0 warnings / 0 errors`；身体测试`4/4`；OpenRouter `deepseek/deepseek-v3.2`真实触碰、quiet、ambient与shown
- 触碰证据：`data/mini-s10-touch-real/`保留首次实测拒绝，定位为`self_experience`漏接`body_touch`；修复后的`data/mini-s10-touch-real-fixed/`按`body_touch→memory_operation→shared_expression`提交，记忆逐id引用原始触碰，failures为0
- quiet证据：`data/mini-s10-quiet-real/`第二次候选推进三件自身生活并落三条有证据记忆；第一次仅为无target的`integrate`结构错误，没有触碰词表误伤，最终无表达、pending为空
- ambient证据：`data/mini-s10-ambient-real/`一次通过三件自身生活、一条记忆与shown，failures为0，最终baseline为read、pending为空
- 她显示的话：“嗯？有人碰了我的头。”、“\"从明天起，做一个幸福的人...\" 轻声念着，目光停留在泛黄的书页上。”
- 结构修复：`self_experience`现在接受自身生活或`body_touch`证据；触碰内容仍须逐条引用`body_touch`，聊天不能洗白；没有实证支持放宽宁拒词表
- ACL：`.pytest-cache-s7/`、`data/mini-s8-evidence/`、`data/mini-s9-replay/`已递归补普通用户`li`权限，并以该用户读回三个样本文件
- 尺寸：形状测试同口径为29个机器侧Python/C#文件、3448/5000行，断言通过
- 模型说明：真实key来自现有配置，日志未输出key；首次失败目录与三腿成功目录均原样保留
- 剩余阻塞：无
- 她哪里更活了：身体真正碰到她时，那一下现在能成为她自己的经历；没人碰她时，她也能安静过自己的午后并只在实际显示后留下话

交接只允许保留最近一次。禁止粘贴完整diff、长测试日志、未来设计和“顺便发现”
清单；这些分别属于Git、测试产物、`DESIGN.md`和当前任务之外。
