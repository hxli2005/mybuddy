# VPet Phase 2 Windows Codex Runbook

> **历史运行手册**:仅用于冻结的 O1 调试探针。v1 不再向 `vpet-plugin/` 增加产品功能;
> Windows v1 施工入口是 `VPET_V1_KICKOFF.md`。

这份文档给 Windows 环境里的 Codex 使用,目标是把 `docs/VPET_PHASE2_SPEC.md`
最后剩余的 VPet 实机联调跑完。不要用 macOS/Linux 的编译结果替代本清单;最终完成需要真实
Windows + VPet 窗口行为证据。

## 目标

Windows Codex 要完成三件事:

1. 在 Windows 上构建并打包 MyBuddy VPet MOD。
2. 把 MOD 安装到 VPet,启动 MyBuddy 后端,完成真实窗口联调。
3. 把通过/失败证据落到本仓库,并在失败时直接修代码、重打包、重测。

只有 `docs/VPET_PHASE2_MANUAL_QA.md` 的全部必测流程通过后,才能认为 Phase 2 spec 完成。

## 前置条件

- Windows 10/11 桌面环境。
- VPet-Simulator 已安装,可以手动启动并打开 MOD 设置。
- Git 工作树包含本次改动。
- Python/uv 可用。
- .NET 8 SDK 可用。
- Codex 能操作 Windows UI。如果不能操作 UI,由用户按同一清单操作并把证据交回。

检查命令:

```powershell
git status --short
uv --version
dotnet --info
```

## 1. 后端准备

复制或编辑 `config.yaml`,至少确认:

```yaml
llm:
  api_key: "<可用 key>"
vpet:
  body_state_injection: true
  touch_escalation: true
  physical_proactive: true
  touch_escalation_daily_limit: 20
  bridge_token: ""
```

先跑自动化测试:

```powershell
uv run ruff check mybuddy tests
uv run pytest -q
```

启动后端:

```powershell
uv run mybuddy web --host 127.0.0.1 --port 8000
```

需要把后端作为单独 Windows 进程留给 VPet 联调时,也可以运行:

```powershell
Start-Process powershell -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", ".\scripts\start_mybuddy_web.ps1",
  "-HostAddress", "127.0.0.1",
  "-Port", "8000"
) -WindowStyle Hidden
```

另开 PowerShell 验证状态:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/vpet/status
```

预期:返回 `ok=true`,且 `configured=true`。

## 2. 构建和打包 VPet MOD

在仓库根目录执行:

```powershell
bash scripts/package_vpet_plugin.sh
```

如果 Windows 没有 `bash` 或 Git Bash,用 PowerShell 原生脚本运行:

```powershell
.\scripts\package_vpet_plugin.ps1
```

成功后应出现:

```text
dist/vpet/1114_MyBuddyBridge/
  info.lps
  plugin/
    MyBuddy.VPetPlugin.dll
    MyBuddy.VPetPlugin.deps.json
    VPet-Simulator.Core.dll
    VPet-Simulator.Windows.Interface.dll
    ...
```

必须检查:

```powershell
Test-Path dist\vpet\1114_MyBuddyBridge\info.lps
Test-Path dist\vpet\1114_MyBuddyBridge\plugin\MyBuddy.VPetPlugin.dll
Test-Path dist\vpet\1114_MyBuddyBridge\plugin\MyBuddy.VPetPlugin.deps.json
```

三个都应为 `True`。

## 3. 安装到 VPet

把整个目录复制到 VPet 本地 MOD 目录。目录名必须保留:

```text
1114_MyBuddyBridge
```

复制后结构应类似:

```text
<VPet MOD 目录>\1114_MyBuddyBridge\info.lps
<VPet MOD 目录>\1114_MyBuddyBridge\plugin\MyBuddy.VPetPlugin.dll
```

如果不确定 MOD 目录位置:

- 优先使用 VPet 设置里的本地 MOD 管理入口。
- 参考已安装的官方 demo MOD 目录形态,应同样包含 `info.lps` 和 `plugin/`。
- 不要只复制 DLL;VPet 代码 MOD 需要整个目录。

然后启动或重启 VPet,在 MOD 设置中启用 `MyBuddy Bridge`。

## 4. 插件设置

打开 VPet 的 MOD 设置或 MyBuddy Bridge 设置:

- Bridge URL: `http://127.0.0.1:8000`
- Token: 与 `config.yaml` 的 `vpet.bridge_token` 一致;空 token 就留空。
- 勾选:
  - `body_state_injection`
  - `touch_escalation`
  - `physical_proactive`
- `今天安静`:默认不勾选,只在对应测试项里勾。

预期:VPet 状态区域显示 `MyBuddy: bridge connected.`。

## 5. 必测流程

逐项执行 `docs/VPET_PHASE2_MANUAL_QA.md` 的 12 个流程。Windows Codex 要把每项结果记录成:

```text
[PASS/FAIL] 编号. 名称
证据:
- 看到的 VPet 行为
- 相关后端响应或 SQL
- 失败时的截图/日志/异常
```

最小证据要求:

- token 错误:可见 `token rejected` 或等价错误状态。
- chat:发送瞬间有 thinking/待机反馈,最终出现 MyBuddy 气泡。
- 摸头:VPet 原生摸头动画立即出现;MyBuddy 短句是额外异步出现。
- 拖拽:拖动窗口不产生触摸升格气泡。
- 30 秒聚合:同一 `client_event_id` 只有一次回复,count 反映聚合次数。
- agent busy:SQL 或响应里出现 `gate_reason='agent_busy'`。
- idle pause:空闲期间不普通 drain。
- user_back digest:先 `user_back`,再 digest;只出一句摘要和 overdue 持久项。
- fullscreen:interrupt 不把 VPet 置前。
- 今天安静:当天不走前台,跨天恢复。
- 断网/超时:VPet UI 不冻结,状态有可见错误。

## 6. SQL 采证

在后端使用的 SQLite 数据库上执行 `docs/VPET_PHASE2_MANUAL_QA.md` 中的 SQL。

如果不确定数据库路径,查看 `config.yaml` 的 `paths.db_file`。常见命令:

```powershell
sqlite3 .\data\mybuddy.db ".tables"
sqlite3 .\data\mybuddy.db "select event, count, escalated, replied, gate_reason, day_index from vpet_events order by id desc limit 20;"
```

必须能证明:

- `vpet_events.server_flags_json` 存在服务端三开关快照。
- `vpet_events.client_flags_json` 存在插件三开关快照。
- `day_index` 非空。
- 存在或能造出 `pending_discarded`、`pending_digested`、`pending_overdue`。
- body_state 明显矛盾时会写 `body_state_conflict`。

## 7. 失败处理

遇到失败不要只记录。Windows Codex 应按下面顺序处理:

1. 保存失败证据:截图、VPet 可见状态、后端日志、`vpet_events` 相关行。
2. 判断失败归属:
   - 后端语义/遥测错:改 Python 和测试。
   - 插件 UI/事件/冻结/动作错:改 `vpet-plugin/`。
   - 部署结构错:改 `scripts/package_vpet_plugin.sh` 或 `vpet-plugin/mod/`。
3. 重跑:

```powershell
uv run ruff check mybuddy tests
uv run pytest -q
.\scripts\package_vpet_plugin.ps1
```

4. 重新复制 MOD,重启 VPet,重测失败项。

## 8. 完成回填

全部通过后,Windows Codex 应更新 `docs/VPET_PHASE2_MANUAL_QA.md`,在文末追加:

```markdown
## Windows QA Result

- Date: YYYY-MM-DD
- Windows version:
- VPet version/source:
- MyBuddy commit:
- Backend command:
- Plugin package: dist/vpet/1114_MyBuddyBridge
- Result: PASS

### Evidence

- `uv run ruff check mybuddy tests`: pass
- `uv run pytest -q`: pass
- `bash scripts/package_vpet_plugin.sh`: pass
- Manual QA 1-12: pass
- SQL evidence: summary pasted here
```

如果有任何失败项未修复,不要写 `Result: PASS`,也不要把 Phase 2 spec 标为完成。
