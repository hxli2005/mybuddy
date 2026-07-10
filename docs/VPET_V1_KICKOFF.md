# v1 开工指令(Windows 侧 Codex 入口文档)

> 你是本仓库 v1(小布桌宠最终版)的实现工程师。交付日 **2026-08-01 硬**。
> 产品真相源:`docs/VPET_PRODUCT_V1.md`(形态与宪法,不许改);本文是施工总纲。
> O1 验证性一跳已于 2026-07-11 通过:后端桥全链路(chat→LLM→气泡)在真机验证 OK,你在坚实地基上施工。

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
| 7/12 | **Spike:VPet.Core 嵌入 hello-world**(空白 WPF 窗 + Core NuGet + 加载默认宠物素材 + 播 idle/摸头) | 出结论:走 `VPetCoreHost` 还是 `FramePlayerHost`(见 SHELL_SPEC §3);**不改日期只改实现** |
| 7/13–16 | 并行:引擎侧(physio 引擎 + 协议 v2 端点 + living_state 接生理)/ 壳骨架(窗口/托盘/气泡/AnimationHost) | 引擎:pytest+ruff 绿;壳:能显示宠物+气泡说一句话 |
| 7/17–22 | 集成:六拍逐拍点亮(顺序:拍3→拍1→拍5→拍2→拍4→拍6,由易到难) | 每拍按 ACCEPTANCE 出证据包 |
| **7/24** | **检查点**:六拍应全绿;<4 拍绿 → 砍拍不砍日期,缺拍记入 v1.1 清单 | 检查点报告 |
| 7/25–31 | 周检三项跑一轮 + **只修不加** + 7/31 冻结(tag v1.0 + config 哈希) | 冻结 checklist(EXPERIMENT §5) |

## 铁律(违反 = 验收不过)

1. **宪法优先**:实现细节冲突时,`VPET_PRODUCT_V1.md` §1 八条宪法是最高裁决;其次本规格包;历史文档(VPET_PHASE2_SPEC.md)与本包冲突处一律以本包为准。
2. **不碰清单**:`study-guide/`、`CLAUDE.md`、`docs/VPET_PRODUCT_V1.md`、`tests/test_web.py`、**`vpet-plugin/`(冻结为调试探针,v1 交付后才删,见处置表)**。
3. 引擎侧每次收工前:`.venv/bin/python -m pytest tests/ -q` + `ruff check mybuddy tests` 全绿(Windows 侧用对应 venv 路径);壳侧:`dotnet build` 零警告目标。
4. 中文注释,贴现有代码口吻;commit 规范:`feat(physio)/feat(shell)/feat(bridge)/test/docs` 前缀,小步提交。
5. 范围防线:任何不在六拍必需路径上的想法(TTS/注意力完整版/关系行为参数进阶/QQ)→ 记入 `docs/V2_PARKING.md`,不写代码。**Codex 速度不是范围膨胀的燃料。**
6. 每日收工汇报:改了什么(对哪份 spec 哪节)/ 偏离+理由 / 测试输出尾部 / 明日计划。人类每天 ~2h 在场做 UI 眼睛验收,把需要人眼的项攒成清单等他。

## 完工定义(v1)

六拍证据包齐(ACCEPTANCE 规格)+ 周检三项一轮 + 冻结 tag `v1.0` + 实验预注册文档填毕。8/1 交付物 = tag + 证据包目录 + 一条能跑的 `buddyshell.exe` 启动路径。
