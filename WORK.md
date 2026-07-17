# 编码 session 协调板

> 这里只放当前施工状态，不放设计。产品和架构以 `DESIGN.md` 为唯一权威，完成
> 历史以 Git提交为准。本文件始终覆盖更新，不追加成长篇开发日志。

## 当前状态

- 当前写入者：无
- 当前任务：无
- 最近完成：S2 `/api/body/step`→幂等event→shown确认
- 下一任务：S3 WPF输入→step→气泡→shown（READY）
- 工作区要求：S3 开始编码前必须确认 S2 提交后工作区干净

## 任务流水线

状态只用 `BLOCKED / READY / ACTIVE / DONE`；同一时刻最多一个编码任务为 ACTIVE。

| ID | 状态 | 纵向闭环 | 依赖 | 完成证据 |
|---|---|---|---|---|
| S0 | DONE | 固定当前文档基线 | 无 | 文档独立提交，`diff --check`通过 |
| S1 | DONE | 经历→一次心智包→四文件提交/拒绝 | S0 | 真实模型输出、四文件、失败原文 |
| S2 | DONE | `/api/body/step`→幂等event→shown确认 | S1 | shown前后history差异、重复请求不重做 |
| S3 | READY | WPF输入→step→气泡→shown | S2 | 真实窗口台词、断线不补发 |
| S4 | BLOCKED | 时间/生活→baseline→身体持续呈现 | S3 | 当场生活事件、断线安全姿态、恢复基线 |
| S5 | BLOCKED | 触碰反射→原始事实→心智理解 | S4 | 离线不回补、无关系计分、恢复基线 |
| S6 | BLOCKED | presence→ambient→实际显示→shown | S5 | 未显示不入历史、未回应零痕迹 |
| S7 | BLOCKED | 四类记忆与五动词进入同一心智步 | S6 | record/correct/forget及模式证据轨迹 |
| S8 | BLOCKED | 删除旧路径→完整纵向验收 | S7 | 单一写入路径、遗留清单归零、真实轨迹 |

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

- 任务：S2 `/api/body/step`→幂等event→shown确认
- 提交：本提交
- 跑过的命令：定向测试 `13 passed`；完整 Python 回归 `237 passed`；ruff、diff检查；真实 localhost HTTP 四次step
- 真实输出/她显示的话：“忙完啦。过来歇一会儿，我刚好也把书合上了。”；本机无模型key，HTTP验收使用固定结构包provider
- 写入的数据证据：`data/mini-s2-evidence/`；shown前2条（用户经历、自身生活），shown后仅追加1条同expression_id的`shared_expression`
- 幂等证据：`api-real-001`首次`processed`、重复`duplicate`，provider调用始终1次；重复shown不再追加history
- 删除的旧路径：无；未接WPF，未修改旧VPet桥，未施工S3
- 剩余阻塞：无；S3可以领取
- 她哪里更活了：她说出口的话不再因读取就消失，只有身体真的显示后才成为两人共同发生过的事

交接只允许保留最近一次。禁止粘贴完整diff、长测试日志、未来设计和“顺便发现”
清单；这些分别属于Git、测试产物、`DESIGN.md`和当前任务之外。
