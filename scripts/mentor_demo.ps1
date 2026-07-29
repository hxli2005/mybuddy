# 一键导师演示：三格既有画像 → 聊天当轮填格 → 新偏好激活 → shown → 十点报告。
# 每次运行复制新的临时目录；不读取或改写日常 mind 数据。
param(
    [int]$Port = 8876,
    [int]$MaxSeconds = 180,
    [switch]$ValidateOnly,
    [switch]$HeadlessDemo
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Net.Http

# 某些受限启动器会同时注入 Path 与 PATH；Windows PowerShell 的 Start-Process
# 会把它们视为重复键。只在确有重复时保留规范的 Path。
$pathKeys = @(
    [Environment]::GetEnvironmentVariables().Keys |
        Where-Object { [string]$_ -ieq "Path" }
)
if ($pathKeys.Count -gt 1) {
    $processPath = [Environment]::GetEnvironmentVariable("Path", "Process")
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $processPath, "Process")
}

$Root = Split-Path -Parent $PSScriptRoot
$Fixture = Join-Path $PSScriptRoot "mentor_demo_fixture"
$MainWorktree = [IO.Path]::GetFullPath((Join-Path $Root "..\.."))
$RunRoot = Join-Path $Root (
    "data\mentor-demo-runs\" + (Get-Date -Format "yyyyMMdd-HHmmss")
)
$MindDir = Join-Path $RunRoot "mind"
$ServerOut = Join-Path $RunRoot "server.stdout.log"
$ServerErr = Join-Path $RunRoot "server.stderr.log"
$Url = "http://127.0.0.1:$Port/api/body/step"
$DemoUrl = "http://127.0.0.1:$Port/mentor-demo"
$LivePreference = "有个长期偏好想告诉你：散步时我喜欢看老建筑的窗户和门牌，尤其会留意后来被换过、补过的细节。"
$engine = $null

function Resolve-FirstFile([string[]]$Candidates, [string]$Label) {
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "找不到$Label。检查独立 worktree 与主工作区的本地工具链。"
}

function Test-PortAvailable([int]$CandidatePort) {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $CandidatePort)
    try {
        $listener.Start()
        return $true
    }
    catch [Net.Sockets.SocketException] {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

function Get-AuthorityHashes([string]$Directory) {
    $result = @{}
    foreach ($name in @("state.json", "history.jsonl", "memories.json", "failures.jsonl")) {
        $result[$name] = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Directory $name)).Hash
    }
    return $result
}

function Test-HashesEqual($Left, $Right) {
    foreach ($name in $Left.Keys) {
        if ($Left[$name] -ne $Right[$name]) { return $false }
    }
    return $true
}

function Invoke-BodyStep([string]$Json) {
    $client = [Net.Http.HttpClient]::new()
    try {
        $content = [Net.Http.StringContent]::new(
            $Json,
            [Text.Encoding]::UTF8,
            "application/json"
        )
        $response = $client.PostAsync($Url, $content).Result
        $bytes = $response.Content.ReadAsByteArrayAsync().Result
        $text = [Text.Encoding]::UTF8.GetString($bytes)
        if (-not $response.IsSuccessStatusCode) {
            throw "身体桥返回 $([int]$response.StatusCode)：$text"
        }
        return $text | ConvertFrom-Json
    }
    finally {
        $client.Dispose()
    }
}

function Wait-Bridge {
    foreach ($attempt in 1..120) {
        Start-Sleep -Milliseconds 250
        if ($engine.HasExited) { throw "演示心智启动失败，见 $ServerErr" }
        try {
            return Invoke-BodyStep '{"include_user_profile":true}'
        }
        catch { }
    }
    throw "演示心智未在 30 秒内监听 $Port"
}

function Invoke-ChatAndShow([string]$Content, [string]$Prefix) {
    $eventBody = [ordered]@{
        presence = [ordered]@{ present = $true; fullscreen = $false; surface = "full" }
        event = [ordered]@{
            event_id = "$Prefix-" + [Guid]::NewGuid().ToString("N")
            type = "chat"
            content = $Content
        }
    } | ConvertTo-Json -Depth 10 -Compress
    $response = Invoke-BodyStep $eventBody
    if ($response.event_status -ne "processed" -or -not $response.expression) {
        throw "真实聊天没有生成可显示回答：$($response | ConvertTo-Json -Depth 10)"
    }
    $shownBody = [ordered]@{
        shown_id = [string]$response.expression.id
        presence = [ordered]@{ present = $true; fullscreen = $false; surface = "full" }
    } | ConvertTo-Json -Depth 10 -Compress
    $shown = Invoke-BodyStep $shownBody
    if (-not $shown.shown_confirmed) { throw "回答未完成 shown 确认" }
    return $response
}

function Stop-ProcessIfRunning($Process) {
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        $Process.WaitForExit()
    }
}

function Stop-DemoBridge {
    Stop-ProcessIfRunning $engine
    $listener = netstat -ano |
        Select-String ":$Port\s+.*LISTENING\s+(\d+)$" |
        Select-Object -First 1
    if ($listener -and $listener.Matches.Count -gt 0) {
        $listenerPid = [int]$listener.Matches[0].Groups[1].Value
        $process = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
        if ($process -and $process.ProcessName -eq "python") {
            Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not (Test-PortAvailable $Port)) { throw "端口 $Port 已被占用，请用 -Port 指定空闲端口" }
foreach ($name in @(
    "DEMO_DATA.md", "state.json", "history.jsonl", "memories.json", "failures.jsonl"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $Fixture $name) -PathType Leaf)) {
        throw "合成演示基线缺少 $name"
    }
}

$Python = Resolve-FirstFile @(
    (Join-Path $Root ".venv\Scripts\python.exe"),
    (Join-Path $MainWorktree ".venv\Scripts\python.exe")
) "Python"
$Config = Resolve-FirstFile @(
    (Join-Path $Root "config.yaml"),
    (Join-Path $MainWorktree "config.yaml")
) "config.yaml"

New-Item -ItemType Directory -Path $MindDir | Out-Null
foreach ($name in @(
    "DEMO_DATA.md", "state.json", "history.jsonl", "memories.json", "failures.jsonl"
)) {
    Copy-Item -LiteralPath (Join-Path $Fixture $name) -Destination (Join-Path $MindDir $name)
}

$statePath = Join-Path $MindDir "state.json"
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
$state.last_step_at = [DateTimeOffset]::Now.ToString("yyyy-MM-ddTHH:mm:ss.ffffffzzz")
[IO.File]::WriteAllText(
    $statePath,
    ($state | ConvertTo-Json -Depth 20),
    [Text.UTF8Encoding]::new($false)
)

$engineArgs = @(
    "-m", "mybuddy.cli", "web",
    "--config", $Config,
    "--data-dir", $MindDir,
    "--port", "$Port",
    "--reading-file", (Join-Path $Root "mybuddy\reading.txt"),
    "--parent-pid", "$PID"
)

try {
    $engine = Start-Process -FilePath $Python -ArgumentList $engineArgs `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $ServerOut -RedirectStandardError $ServerErr

    $profile = Wait-Bridge
    $before = Get-AuthorityHashes $MindDir
    $profile = Invoke-BodyStep '{"include_user_profile":true}'
    $after = Get-AuthorityHashes $MindDir

    $history = Get-Content -LiteralPath (Join-Path $MindDir "history.jsonl") -Encoding UTF8 |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        ForEach-Object { $_ | ConvertFrom-Json }
    $sources = @{}
    foreach ($item in $history | Where-Object { $_.type -eq "user_experience" }) {
        $sources[[string]$item.id] = [string]$item.content
    }
    $profileEvidenceMatches = @($profile.user_profile).Count -eq 3
    foreach ($item in @($profile.user_profile)) {
        if (-not $sources.ContainsKey([string]$item.source_id) -or
            $sources[[string]$item.source_id] -ne [string]$item.quote) {
            $profileEvidenceMatches = $false
        }
    }
    $profileJson = $profile | ConvertTo-Json -Depth 10
    $noScores = $profileJson -notmatch '"(score|relationship|warmth|personality)"'
    $initialDimensions = @($profile.user_profile | ForEach-Object { $_.profile_dimension })
    $fixedInitialTable = (
        $initialDimensions -contains "communication_preference" -and
        $initialDimensions -contains "decision_preference" -and
        $initialDimensions -contains "content_interest" -and
        $initialDimensions -notcontains "life_interest"
    )

    if ($ValidateOnly) {
        $validationChecks = [ordered]@{
            "合成数据标记" = (Test-Path -LiteralPath (Join-Path $MindDir "DEMO_DATA.md"))
            "四个权威文件" = @(
                "state.json", "history.jsonl", "memories.json", "failures.jsonl" |
                    Where-Object { Test-Path -LiteralPath (Join-Path $MindDir $_) }
            ).Count -eq 4
            "三格历史画像" = @($profile.user_profile).Count -eq 3
            "生活兴趣初始为空" = $fixedInitialTable
            "每条画像匹配原话" = $profileEvidenceMatches
            "画像查看不修改四文件" = (Test-HashesEqual $before $after)
            "无性格标签或关系总分" = $noScores
        }
        $validationFailed = 0
        foreach ($entry in $validationChecks.GetEnumerator()) {
            if ($entry.Value) { "PASS $($entry.Key)" }
            else { "FAIL $($entry.Key)"; $validationFailed += 1 }
        }
        "验证目录：$RunRoot"
        if ($validationFailed -gt 0) {
            throw "合成演示基线验证失败"
        }
        exit 0
    }

    if ($HeadlessDemo) {
        $learnedResponse = Invoke-ChatAndShow $LivePreference "mentor-learn"
        $learnedProfile = Invoke-BodyStep '{"include_user_profile":true}'
        $learned = @(
            $learnedProfile.user_profile |
                Where-Object {
                    $_.profile_dimension -eq "life_interest" -and
                    $_.quote -eq $LivePreference
                }
        ) | Select-Object -Last 1
        if (-not $learned) { throw "真实聊天后生活兴趣没有当轮形成" }
        $greetingResponse = Invoke-ChatAndShow "你好" "mentor-recall"
        $headlessHistory = Get-Content -LiteralPath (Join-Path $MindDir "history.jsonl") -Encoding UTF8 |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
        $grounded = @(
            $headlessHistory |
                Where-Object {
                    $_.type -eq "shared_expression" -and
                    $_.expression_act -eq "grounded_recall" -and
                    @($_.expression_evidence_ids) -contains [string]$learned.source_id
                }
        ) | Select-Object -Last 1
        if (-not $grounded) { throw "无界面演示回答没有绑定老建筑偏好的原话证据" }
        "PASS 真实聊天当轮形成生活兴趣"
        "PASS 人格表从 3 条增长到 $(@($learnedProfile.user_profile).Count) 条"
        "PASS 下一句问候生成 grounded_recall"
        "PASS 回答绑定 $($learned.source_id)"
        "PASS shown 后进入共同历史"
        "形成时她实际显示的话：$($learnedResponse.expression.text)"
        "她实际显示的话：$($grounded.content)"
        "证据目录：$RunRoot"
        exit 0
    }

    $page = Invoke-WebRequest -UseBasicParsing -Uri $DemoUrl
    if ($page.StatusCode -ne 200 -or $page.Content -notmatch "实时个性化建模") {
        throw "实时人格表页面未正确提供"
    }
    Start-Process -FilePath "$DemoUrl`?auto=1"

    $learned = $null
    $greeting = $null
    foreach ($attempt in 1..$MaxSeconds) {
        Start-Sleep -Seconds 1
        $currentProfile = Invoke-BodyStep '{"include_user_profile":true}'
        $learned = @(
            $currentProfile.user_profile |
                Where-Object {
                    $_.profile_dimension -eq "life_interest" -and
                    $_.quote -eq $LivePreference
                }
        ) | Select-Object -Last 1
        if (-not $learned) { continue }
        $resultHistory = Get-Content -LiteralPath (Join-Path $MindDir "history.jsonl") -Encoding UTF8 |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
        $greeting = @(
            $resultHistory |
                Where-Object {
                    $_.type -eq "shared_expression" -and
                    $_.expression_act -eq "grounded_recall" -and
                    @($_.expression_evidence_ids) -contains [string]$learned.source_id
                }
        ) | Select-Object -Last 1
        if ($greeting) { break }
    }

    $checks = [ordered]@{
        "1 合成数据已披露" = (Test-Path -LiteralPath (Join-Path $MindDir "DEMO_DATA.md"))
        "2 四个权威文件齐全" = @(
            "state.json", "history.jsonl", "memories.json", "failures.jsonl" |
                Where-Object { Test-Path -LiteralPath (Join-Path $MindDir $_) }
        ).Count -eq 4
        "3 固定人格表页面已打开" = $page.Content -match "尚未形成"
        "4 初始三格画像逐条匹配原话" = $profileEvidenceMatches
        "5 生活兴趣初始为空" = $fixedInitialTable
        "6 查看人格表不改四文件" = (Test-HashesEqual $before $after)
        "7 真实聊天当轮形成生活兴趣" = $null -ne $learned
        "8 新人格内容逐字来自本轮原话" = ($learned -and $learned.quote -eq $LivePreference)
        "9 下一句问候激活刚形成的偏好" = $null -ne $greeting
        "10 激活回答 shown 后进入共同历史" = ($greeting -and $greeting.expression_id)
    }

    ""
    "=== 导师自动演示验收 ==="
    $failed = 0
    foreach ($entry in $checks.GetEnumerator()) {
        if ($entry.Value) {
            "PASS $($entry.Key)"
        }
        else {
            "FAIL $($entry.Key)"
            $failed += 1
        }
    }
    ""
    if ($greeting) {
        "她实际显示的话：$($greeting.content)"
        "采用证据：$($greeting.expression_evidence_ids -join ', ')"
    }
    "完整证据目录：$RunRoot"
    if ($failed -gt 0) { throw "自动演示有 $failed 项未通过" }
}
finally {
    Stop-DemoBridge
}
