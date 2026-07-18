# 编码 session 协调板

> 这里只放当前施工状态，不放设计。产品和架构以 `DESIGN.md` 为唯一权威，完成
> 历史以 Git提交为准。本文件始终覆盖更新，不追加成长篇开发日志。

## 当前状态

- 当前写入者：无
- 当前任务：无
- 最近完成：S5 触碰反射→原始事实→心智理解
- 下一任务：S6 presence→ambient→实际显示→shown（READY）
- 工作区要求：S6 开始编码前必须确认 S5 提交后工作区干净

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
| S6 | READY | presence→ambient→实际显示→shown | S5 | 未显示不入历史、未回应零痕迹 |
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

- 任务：S5 触碰反射→原始事实→心智理解
- 提交：本提交
- 跑过的命令：BuddyShell构建0警告；C#回归`23 passed`；Python回归`243 passed`；ruff、diff检查；真实WPF窗口与localhost body服务
- 在线证据：头部瞬时反射→`body_touch{zone:head}`→心智状态改动→气泡→shown；动画随后从touch exit重建`read` baseline，四文件和截图在`data/mini-s5-evidence/`
- 她显示的话：“呀，碰到我头发了。我刚还低着头看书呢。”；WPF日志`shown confirmed=True`后history才追加对应`shared_expression`
- 离线证据：停服后触碰仍完成本地反射并恢复`idle.default`安全姿态，日志为`body_touch_unreported`；history行数`4→4`且没有outbox文件
- 红线证据：身体协议只上报`event_id + touch_head/touch_body`，拒绝身体夹带意义；state/history无warmth、好感、亲密或关系计分字段
- 模型说明：当前config和环境无API key，本次S5桌面验收用明确标注的确定性候选提供器；没有伪称为新外部模型输出，S1真实模型轨迹保持不动
- 删除的旧路径：删除`TouchLayer`的首次/五次/30秒窗口/升格/outbox补报政策，同时删除TouchEscalation设置与示例配置
- 剩余阻塞：无；S6可以领取
- 她哪里更活了：碰到她时，身体先本能地动一下，心智再把这一次真实触碰接成自己的当下，而不是加一分或积成日后补报的债

交接只允许保留最近一次。禁止粘贴完整diff、长测试日志、未来设计和“顺便发现”
清单；这些分别属于Git、测试产物、`DESIGN.md`和当前任务之外。
