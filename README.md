# MyBuddy mini

MyBuddy mini 是一个本地运行、由用户拥有的最小人格引擎；小布是唯一实现。她沿
真实时间继续生活，因自己的经历和双方真正发生的经历而改变，同时长期保持为同
一个人。完整产品与架构边界只见 [`DESIGN.md`](DESIGN.md)。

## 唯一闭环

```text
身体观察 / shown 收据
  → POST /api/body/step
  → 到点在 read / walk 间轮转
  → read 从真实 TXT 取下一段，身体 completed 后一次模型调用解释原文
  → walk 用左右动画真实移动窗口，固定收据核验起终点与当前屏边界
  → read/walk 的物理形状与 A/B/C 素材只列在同一严格 JSON 动作目录
  → interrupted / failed 不写成人生
  → 四条红线集中校验
  → 四份 JSON/JSONL 原子提交或整包拒绝
  → 下一具体活动 + 至多一个待显示表达
  → 身体真正显示后才成为共同历史
```

权威数据只有 `state.json`、`history.jsonl`、`memories.json` 和
`failures.jsonl`。身体只持久化最后一个 `shown_id` 收据。

## 运行

Python 3.12+：

```powershell
uv sync --extra api --extra dev
Copy-Item config.example.yaml config.yaml
uv run mybuddy web
```

Windows 身体：

```powershell
.\.dotnet-sdk\dotnet.exe run --project .\buddyshell\BuddyShell.csproj
```

## 免费分享 zip

构建机需有授权可用的 VPet `0000_core/pet/vup` 素材目录：

```powershell
.\scripts\build_share.ps1 -PetRoot "D:\path\to\0000_core\pet\vup"
```

产物为 `dist/MyBuddy-S16.1-win-x64.zip`；收件人不需安装 Python、.NET 或 Steam，
解压后双击 `BuddyShell.exe`，首次只输入自己的 OpenRouter key。包内
`THIRD_PARTY_NOTICES.txt` 记录动画归属与免费分发边界。
包根目录的 `小布读本.txt` 是她实际读取的 UTF-8 来源；第一段是书名，正文以空行
分段，换书时更换书名会从头建立新进度。

核心不包含任务工具、外部平台、数据库、调度器、后台队列或第二套协议。桌面身体
只保留窗口、动画、气泡、聊天、触碰、presence、动作/shown 收据和离线安全姿态。
