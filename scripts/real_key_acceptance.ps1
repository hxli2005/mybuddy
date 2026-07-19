# 一键真实-key 验收:chat/touch/raise 事件腿 + read/walk/ambient 时间腿。
# 脚本以身体层身份与引擎对话;key 始终只被引擎进程读取,本脚本不读取、不打印。
# quiet/ambient 依赖时间流逝,采用与 S13 验收相同的"停机→回拨 last_step_at→重启"。
param(
    [int]$Port = 8123,
    [string]$DataDir = "data\real-key-acceptance",
    [string]$Config = "config.yaml",
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Split-Path -Parent $PSScriptRoot)

if (Test-Path $DataDir) { throw "证据目录已存在,不覆盖:$DataDir" }
if (-not (Select-String -Path $Config -Pattern '^\s*api_key:\s*\S+' -Quiet)) {
    throw "$Config 缺少 api_key;key 由所有者填入,本脚本不经手"
}
if (Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue) { throw "端口 $Port 已被占用" }
New-Item -ItemType Directory -Force $DataDir | Out-Null

$url = "http://127.0.0.1:$Port/api/body/step"
$script:run = 0
$script:engine = $null

function Start-Engine {
    $script:run += 1
    $out = Join-Path $DataDir "server-$($script:run).stdout.log"
    $err = Join-Path $DataDir "server-$($script:run).stderr.log"
    $script:engine = Start-Process -FilePath $Python `
        -ArgumentList @("-m", "mybuddy.cli", "web", "--config", $Config,
            "--data-dir", $DataDir, "--port", $Port, "--parent-pid", $PID) `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -NoNewWindow
    foreach ($attempt in 1..120) {
        Start-Sleep -Milliseconds 500
        if ($script:engine.HasExited) { throw "引擎启动失败,见 $err" }
        try {
            Invoke-RestMethod -Method Post -Uri $url -ContentType "application/json; charset=utf-8" -Body "{}" | Out-Null
            return
        } catch { }
    }
    throw "引擎 $Port 健康检查超时"
}

function Stop-Engine {
    if ($script:engine -and -not $script:engine.HasExited) {
        Stop-Process -Id $script:engine.Id -Force
        $script:engine.WaitForExit()
    }
    $script:engine = $null
}

function Invoke-Leg([string]$Scenario, [string]$EventId) {
    $legArgs = @("scripts\accept_real_key.py", "--url", $url, "--scenario", $Scenario)
    if ($EventId) { $legArgs += @("--event-id", $EventId) }
    $output = & $Python @legArgs
    if ($LASTEXITCODE -ne 0) { throw "$Scenario 腿失败:$output" }
    $legFile = Join-Path $DataDir "leg-$($script:run)-$Scenario.json"
    [IO.File]::WriteAllText($legFile, ($output -join "`n"))
}

function Set-ClockBack([int]$Minutes = 31) {
    $statePath = Join-Path $DataDir "state.json"
    $stateText = [IO.File]::ReadAllText($statePath)
    $state = $stateText | ConvertFrom-Json
    $rewound = [DateTimeOffset]::Parse($state.last_step_at).AddMinutes(-$Minutes)
    $stamp = $rewound.ToString("yyyy-MM-ddTHH:mm:ss.ffffffzzz")
    $newText = $stateText -replace '("last_step_at":\s*")[^"]+(")', "`${1}$stamp`${2}"
    [IO.File]::WriteAllText($statePath, $newText)
}

try {
    Start-Engine                                    # 运行 1:三条事件腿
    Invoke-Leg "chat" "real-acceptance-chat-001"
    Invoke-Leg "touch-head" "real-acceptance-touch-001"
    Invoke-Leg "raise" "real-acceptance-raise-001"
    Stop-Engine

    Set-ClockBack; Start-Engine                     # 运行 2:安静阅读第 0 段
    Invoke-Leg "quiet"
    Stop-Engine

    Set-ClockBack; Start-Engine                     # 运行 3:walk 轮转(脚本代身体回位移收据)
    Invoke-Leg "quiet"
    Stop-Engine

    Set-ClockBack; Start-Engine                     # 运行 4:在场阅读第 1 段,ambient 机会
    Invoke-Leg "ambient"
    Stop-Engine
}
finally {
    Stop-Engine
}

$history = Get-Content (Join-Path $DataDir "history.jsonl") -Encoding UTF8 |
    ForEach-Object { $_ | ConvertFrom-Json }
$failuresPath = Join-Path $DataDir "failures.jsonl"
$failureCount = 0
if ((Test-Path $failuresPath) -and (Get-Item $failuresPath).Length -gt 0) {
    $failureCount = @(Get-Content $failuresPath).Count
}
""
"=== 她实际显示的话(真实模型)==="
$history | Where-Object { $_.type -eq "shared_expression" } |
    ForEach-Object { "[$($_.expression_kind)] $($_.content)" }
""
"=== history 类型序列 ==="
($history | ForEach-Object { $_.type }) -join " -> "
""
"失败候选:$failureCount 条(原文与拒因已留在 failures.jsonl)"
"证据目录:$DataDir"
