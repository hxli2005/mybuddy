param(
    [Parameter(Mandatory = $true)]
    [string]$PetRoot,
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$petSource = (Resolve-Path -LiteralPath $PetRoot).Path
$outputRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
if (-not $outputRoot.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory 必须位于仓库内。"
}

$stage = Join-Path $outputRoot "MyBuddy"
$work = Join-Path $projectRoot ".share-build"
foreach ($target in @($stage, $work)) {
    if (Test-Path -LiteralPath $target) {
        $resolved = (Resolve-Path -LiteralPath $target).Path
        if (-not $resolved.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "拒绝清理仓库外路径：$resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $stage, $work -Force | Out-Null

$dotnet = Join-Path $projectRoot ".dotnet-sdk\dotnet.exe"
if (-not (Test-Path -LiteralPath $dotnet)) { $dotnet = "dotnet" }
& $dotnet publish (Join-Path $projectRoot "buddyshell\BuddyShell.csproj") `
    -c Release -r win-x64 --self-contained true -p:PublishSingleFile=false -o $stage
if ($LASTEXITCODE -ne 0) { throw "BuddyShell publish 失败。" }

$engineDist = Join-Path $work "engine-dist"
& uv run --extra api --extra share pyinstaller --noconfirm --clean --onedir --console `
    --name MyBuddyEngine --paths $projectRoot `
    --distpath $engineDist --workpath (Join-Path $work "pyi-work") `
    --specpath (Join-Path $work "pyi-spec") (Join-Path $projectRoot "scripts\engine_entry.py")
if ($LASTEXITCODE -ne 0) { throw "MyBuddyEngine PyInstaller 打包失败。" }
Copy-Item -LiteralPath (Join-Path $engineDist "MyBuddyEngine") -Destination (Join-Path $stage "engine") -Recurse

$assetFolders = @(
    "Default/Nomal/1",
    "Sleep/A_Nomal", "Sleep/B_Nomal", "Sleep/C_Nomal",
    "WORK/Study/A_Nomal", "WORK/Study/B_1_Nomal", "WORK/Study/C_Nomal",
    "WORK/Calligraphy/Nomal/A", "WORK/Calligraphy/Nomal/B", "WORK/Calligraphy/Nomal/C",
    "IDEL/aside/Nomal/A", "IDEL/aside/Nomal/B", "IDEL/aside/Nomal/C",
    "Think/Nomal/A", "Think/Nomal/B", "Think/Nomal/C",
    "Touch_Head/A_Nomal", "Touch_Head/B_Nomal", "Touch_Head/C_Nomal",
    "Touch_Body/A_Happy/tb1", "Touch_Body/B_Happy/tb1", "Touch_Body/C_Happy/tb1",
    "Say/Self/A", "Say/Self/B_1", "Say/Self/C",
    "Say/Shining/A", "Say/Shining/B_1", "Say/Shining/C"
)
$assetRoot = Join-Path $stage "assets\pet"
foreach ($relative in $assetFolders) {
    $source = Join-Path $petSource $relative
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "缺少动画目录：$relative" }
    $destination = Join-Path $assetRoot $relative
    New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Recurse
}
$pngCount = @(Get-ChildItem -LiteralPath $assetRoot -Recurse -File -Filter *.png).Count
if ($pngCount -ne 248) { throw "内置动画应为 248 帧，实际为 $pngCount。" }

Copy-Item -LiteralPath (Join-Path $projectRoot "distribution\config.default.yaml") -Destination $stage
Copy-Item -LiteralPath (Join-Path $projectRoot "distribution\THIRD_PARTY_NOTICES.txt") -Destination $stage
Copy-Item -LiteralPath (Join-Path $projectRoot "distribution\使用说明.html") -Destination $stage

& (Join-Path $stage "engine\MyBuddyEngine.exe") version
if ($LASTEXITCODE -ne 0) { throw "打包后心智引擎无法启动。" }

$leaks = Get-ChildItem -LiteralPath $stage -Recurse -File | Where-Object {
    $_.Extension -in ".yaml", ".json", ".txt", ".html", ".config"
} | Select-String -Pattern "sk-or-v1-|sk-ant-|api_key:\s+(?!\$\{MYBUDDY_API_KEY\})\S+"
if ($leaks) { throw "分发目录疑似含有真实 key：$($leaks.Path -join ', ')" }

$archive = Join-Path $outputRoot "MyBuddy-S11-win-x64.zip"
if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
Compress-Archive -LiteralPath $stage -DestinationPath $archive -CompressionLevel Optimal
Write-Host "SHARE_BUILD_OK archive=$archive png=$pngCount"
