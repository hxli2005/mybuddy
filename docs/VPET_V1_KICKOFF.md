# v1 开工指令(Windows 侧 Codex 入口文档)

> 你是本仓库 v1(小布桌宠最终版)的实现工程师。交付日 **2026-08-01 硬**。
> 产品真相源:`docs/VPET_PRODUCT_V1.md`(形态与宪法,不许改);本文是施工总纲。
> O1 验证性一跳已于 2026-07-11 通过:后端桥全链路(chat→LLM→气泡)在真机验证 OK,你在坚实地基上施工。
> 状态:`FINAL`(2026-07-11);本规格包不存在留给实现者自行裁决的架构分叉。

## 阅读顺序

1. `VPET_PRODUCT_V1.md` —— 她是什么(八条宪法 + 六拍 + 反形态)。**所有实现争议以宪法裁决。**
2. `VPET_V1_PROTOCOL_V2.md` —— 壳↔引擎协议(先读,它是两侧的合同)
3. `VPET_V1_PHYSIO_SPEC.md` —— 引擎侧新系统:生理曲线
4. `VPET_V1_SHELL_SPEC.md` —— 壳(buddyshell/,C# WPF)
5. `VPET_V1_ASSET_DISPOSITION.md` —— phase-2 资产逐模块处置(动手前必读,防止重复造/误删)
6. `VPET_ACCEPTANCE.md` —— 六拍验收与证据规格(你的完工定义)
7. `VPET_V1_EXPERIMENT.md` —— 冻结后实验(影响遥测字段,编码期只需保证字段齐)

## 排期与你的任务块

**日期语义 = 最晚检查点,不是配速器。** 允许连续作业、整体提前:代码提前完成 → 六拍验收提前 → 周检提前开跑 → 冻结提前,多出的全是 8/1 前的缓冲。但两样不可压缩:①每拍验收需人眼+真机(人类每天 ~2h 在场,把待验项攒成清单等他);②周检三项按定义需 7 个日历天、拍 5/6 各需一次真时复验。**提前完成一个块 ≠ 跳过该块的验收。**

| 日期 | 任务 | 完成定义 |
|---|---|---|
| 7/12 | **Spike:VPet.Core 嵌入 hello-world**(空白 WPF 窗 + Core NuGet + 默认素材 + idle + 头身命中) | 18:00 按 PRODUCT §4 四项门锁定 `VPetCoreHost` 或 `FramePlayerHost`;之后不重开路线争论 |
| 7/13–16 | 并行:引擎侧(physio + 协议 v2 + living_state)/ 壳骨架(窗口/托盘/气泡/AnimationHost) | 引擎:pytest+ruff 绿;壳:显示宠物、state 驱动 idle、聊天气泡闭环 |
| 7/17–22 | 集成:六拍逐拍点亮(顺序:**拍3→拍4→拍5→拍1→拍6→拍2**,先暴露跨层风险) | 每拍完成当天生成自动证据,次日集中做人眼验收,不把证据积到最后 |
| **7/24** | **检查点**:六拍应全绿;未全绿立即停止新增,只修六拍路径 | 检查点报告,逐拍 `PASS/FAIL/证据缺口` |
| 7/25–31 | 周检三项跑一轮 + **只修不加** + 7/31 冻结(tag v1.0 + manifest) | 连续 7 个有效日 + `EXPERIMENT §6` 冻结产物 |
| **7/27** | **范围锁**:仍失败的拍改标 `DEFERRED` 进 v1.1;剩余时间只修 crash/丢数据/鉴权/UI冻结与证据缺口 | 冻结候选清单,交付级别预判 `FULL/REDUCED` |

## 铁律(违反 = 验收不过)

1. **宪法优先**:实现细节冲突时,`VPET_PRODUCT_V1.md` §1 八条宪法是最高裁决;其次本规格包;历史文档(VPET_PHASE2_SPEC.md)与本包冲突处一律以本包为准。
2. **不碰清单**:`study-guide/`、`CLAUDE.md`、本轮已定稿的 `docs/VPET_PRODUCT_V1.md`、`tests/test_web.py`、**`vpet-plugin/`(冻结为调试探针,实验结束后才允许单独决策,见处置表)**。若实现与定稿文档冲突,先停工提交决策记录,不得静默改产品定义。
3. 引擎侧每次收工前:`.venv/bin/python -m pytest tests/ -q` + `ruff check mybuddy tests` 全绿(Windows 侧用对应 venv 路径);壳侧:`dotnet build` 零警告目标。
4. 中文注释,贴现有代码口吻;commit 规范:`feat(physio)/feat(shell)/feat(bridge)/test/docs` 前缀,小步提交。
5. 范围防线:任何不在六拍必需路径上的想法(TTS/注意力完整版/关系行为参数进阶/QQ)→ 记入 `docs/V2_PARKING.md`,不写代码。**Codex 速度不是范围膨胀的燃料。**
6. 每日收工汇报:改了什么(对哪份 spec 哪节)/ 偏离+理由 / 测试输出尾部 / 明日计划。人类每天 ~2h 在场做 UI 眼睛验收,把需要人眼的项攒成清单等他。

## 已冻结的工程裁决

1. **路线**:只交付 O2 `buddyshell/`;Core 失败即换 `FramePlayerHost`,O1 永不承接 v1 产品功能。
2. **时钟**:模拟时间只由引擎持有;壳读取 `/api/vpet/state.server_time`,不维护第二套偏移时钟。
3. **共处计时**:50 分钟提醒由引擎现有 APScheduler 持久任务负责;壳只上报 start/stop。
4. **可审计性**:壳每 20 分钟上报 `presence_heartbeat`,每次真正显示主动内容后上报 `notice_shown`;没有展示确认的 drain 不计作打扰。
5. **并发**:生理状态所有读取演化和事件增量走数据库原子事务;日上限、冷却与聚合均持久化,不使用仅进程内计数。
6. **交付状态**:六拍全绿标 `FULL`;到期存在缺拍则如实标 `REDUCED`,缺拍进入 v1.1。无论哪种状态都不伪造 PASS、不回退 O1。

## 完工定义(v1)

目标完工 = 六拍证据包齐(ACCEPTANCE 规格)+ 周检三项一轮 + 冻结 tag `v1.0` + 冻结清单生成。8/1 交付物 = tag + `FREEZE_MANIFEST.json` + 证据包目录 + 一条能跑的 `buddyshell.exe` 启动路径。若有 `DEFERRED`,manifest 必须标 `release_level=REDUCED` 并列出原因,不得写成完整通过。
