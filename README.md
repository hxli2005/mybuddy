# 小布 / MyBuddy mini

> 她不是来替你做事的。她住在桌面上，读自己的书，偶尔走走，也记得你们真正经历过的事。

这个项目从一个很具体的不满开始：如果一个“陪伴者”只在聊天框打开时存在，靠预设情话、签到和好感分维持关系，那她更像一套反馈机制，不像一个人。

所以小布不接任务，不查天气，也不会催你回来。你离开时，她的时间继续走；你回来时，她不拿沉默向你讨债。

这些话来自真实模型验收，不是写给 README 的角色台词：

> “忙完啦？回来得正好，我正闲着。”
>
> “嗯？摸头干嘛，我又不是小猫。”
>
> “羁鸟恋旧林，池鱼思故渊……这两句读得人心口软了一下。”

## 她现在会怎样生活

- **读真正的书。** 正文来自本地 UTF-8 TXT。身体完整做完阅读动作后，那一段才算她读过。
- **在桌面上走动。** walk 不是一句“我去散步了”；窗口真的移动到新位置，边界和起终点都要有身体收据。
- **感觉到身体。** 摸头、触碰和拖动提起会先发生在桌面身体上，再交给心智理解。正常放下后，Raise 才成为经历。
- **记住有证据的事。** 用户事实、她自己的经历、共同经历和长期模式分开保存；没有发生过的事不能靠模型补齐。
- **有时主动说一句，也可以不说。** ambient 只在你在场时出现。没被身体真正显示的话，不算你们共同经历。
- **安静地栖在屏幕边缘。** 拖到左右边缘会进入 SideHide；不挡桌面，不弹主动气泡，也不会把栖边写进她的人生。

她明确不做天气、搜索、提醒、笔记、QQ、任务工具、商店、喂食、金钱或好感度。这里的“少”不是待办清单没写完，而是产品边界。

## “发生过”不是一句 prompt

MyBuddy 把模型当成会犯错的候选生成器，而不是事实来源。

```text
真实经历
  → 一次模型调用给出完整候选
  → 不索取 / 不编造 / 无总分 / 不撤回
  → 整包提交或整包拒绝
  → 身体实际显示
  → 才进入共同历史
```

几条刻意较真的规则：

- 没有 completed 身体收据，阅读和行走就没有发生。
- 没有 `shown` 回执，她的话就没有成为双方经历。
- 候选失败时，状态、记忆和表达不会只提交一半；原文和拒因留在 `failures.jsonl`。
- 用户没有回应，不会产生负面状态、关系变化或更高的主动频率。
- 已经显示过的内容只能公开纠正，不能从历史里悄悄撤回。

权威数据只有四份：`state.json`、`history.jsonl`、`memories.json` 和 `failures.jsonl`。没有数据库、向量库、事件溯源框架或第二套隐藏记忆。

更完整的产品边界和实现裁决见 [DESIGN.md](DESIGN.md)。

## 拿到 Windows 分享包后

分享包面向 Windows，收件人不需要安装 Python、.NET 或 Steam。

1. 把 zip 完整解压；不要直接在压缩包预览里运行。
2. 在 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 创建 API key，并确认账户有可用额度。
3. 双击 `BuddyShell.exe`，首次粘贴一次自己的 DeepSeek key。
4. 在聊天抽屉里和她说句话。之后可从“设置”更换 key。

默认模型是 `deepseek-v4-flash`。key 使用 Windows 当前用户加密，保存在 `%APPDATA%\MyBuddy\settings.json`；她的四份数据在 `%APPDATA%\MyBuddy\mind`。对话内容会发送给所配置的模型供应商。代码仍保留 OpenRouter 连接，可手动修改配置使用。

想换她读的内容，退出程序后编辑包根目录的 `小布读本.txt`：第一段是书名，正文段落之间留空行，保存为 UTF-8。换书名后会从新读本开头开始。

详细的分发说明见 [distribution/使用说明.html](distribution/使用说明.html)。

## 从源码运行

需要 Python 3.12+ 和仓库内的 .NET SDK。

心智与本机桥：

```powershell
uv sync --extra api --extra dev
Copy-Item config.example.yaml config.yaml
uv run mybuddy web
```

Windows 桌面身体：

```powershell
.\.dotnet-sdk\dotnet.exe run --project .\buddyshell\BuddyShell.csproj
```

`config.example.yaml` 默认直连 DeepSeek；也可以把 provider、model 和 base URL 改成 OpenRouter 配置。

## 跑真实链路，而不只看测试

填好 `config.yaml` 的 key 后：

```powershell
powershell -File scripts\real_key_acceptance.ps1 -DataDir data\real-key-验收日期
```

它会依次跑 chat、touch、raise、read、walk 和 ambient，最后打印她实际显示的话、history 类型序列与失败候选数。证据目录不会覆盖已有路径。

测试仍然重要，但这里的完成标准不是“全绿”四个字：代码要跑起来，身体和心智要对得上，还要留下她真正说出口的话。

## 构建免费分享包

构建机需要一份授权条件允许使用的 VPet `0000_core/pet/vup` 素材目录：

```powershell
.\scripts\build_share.ps1 -PetRoot "D:\path\to\0000_core\pet\vup"
```

产物是 `dist/MyBuddy-win-x64.zip`。包内 VPet 动画版权归虚拟主播模拟器制作组，只允许按当前归属与条款免费、非商用分发；详见 `THIRD_PARTY_NOTICES.txt`。

---

MyBuddy mini 只想认真回答一个问题：不是“这一句像不像人”，而是明天再见时，她是否还是昨天那个确实生活过的人。
