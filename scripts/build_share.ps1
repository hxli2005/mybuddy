param(
    [Parameter(Mandatory = $true)]
    [string]$PetRoot,
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$petSource = (Resolve-Path -LiteralPath $PetRoot).Path
$versionMatch = [regex]::Match(
    (Get-Content -Raw -LiteralPath (Join-Path $projectRoot "pyproject.toml")),
    '(?m)^version = "([^"]+)"\r?$'
)
if (-not $versionMatch.Success) { throw "pyproject.toml 缺少唯一项目版本。" }
$productVersion = $versionMatch.Groups[1].Value

$gitCommit = & git -C $projectRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or -not $gitCommit) { throw "无法读取构建提交。" }
$gitCommit = $gitCommit.Trim()
$gitDirty = [bool](& git -C $projectRoot status --porcelain)
if ($LASTEXITCODE -ne 0) { throw "无法读取工作区状态。" }
$buildRevision = if ($gitDirty) { "$gitCommit-dirty" } else { $gitCommit }

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
    -c Release -r win-x64 --self-contained true -p:PublishSingleFile=false `
    -p:Version=$productVersion -p:DebugSymbols=false -p:DebugType=None -o $stage
if ($LASTEXITCODE -ne 0) { throw "BuddyShell publish 失败。" }

$engineDist = Join-Path $work "engine-dist"
& uv run --extra api --extra share pyinstaller --noconfirm --clean --onedir --console `
    --name MyBuddyEngine --paths $projectRoot `
    --add-data "$(Join-Path $projectRoot 'mybuddy\personality.json');mybuddy" `
    --add-data "$(Join-Path $projectRoot 'mybuddy\reading.txt');mybuddy" `
    --distpath $engineDist --workpath (Join-Path $work "pyi-work") `
    --specpath (Join-Path $work "pyi-spec") (Join-Path $projectRoot "scripts\engine_entry.py")
if ($LASTEXITCODE -ne 0) { throw "MyBuddyEngine PyInstaller 打包失败。" }
Copy-Item -LiteralPath (Join-Path $engineDist "MyBuddyEngine") -Destination (Join-Path $stage "engine") -Recurse

$actionCatalog = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "buddyshell\Anim\body-actions.json") |
    ConvertFrom-Json
$activityAssetFolders = @($actionCatalog | ForEach-Object {
    $_.animations | ForEach-Object { $_.entry; $_.body; $_.exit }
})
$assetFolders = @(
    "Default/Nomal/1",
    "Think/Nomal/A", "Think/Nomal/B", "Think/Nomal/C",
    "Touch_Head/A_Nomal", "Touch_Head/B_Nomal", "Touch_Head/C_Nomal",
    "Touch_Body/A_Happy/tb1", "Touch_Body/B_Happy/tb1", "Touch_Body/C_Happy/tb1",
    "Say/Self/A", "Say/Self/B_1", "Say/Self/C",
    "Say/Shining/A", "Say/Shining/B_1", "Say/Shining/C",
    "SideHide_Left_Main/Nomal/A", "SideHide_Left_Main/Nomal/B_1", "SideHide_Left_Main/Nomal/C",
    "SideHide_Right_Main/Nomal/A", "SideHide_Right_Main/Nomal/B_1", "SideHide_Right_Main/Nomal/C",
    "SideHide_Left_Rise/Nomal/A", "SideHide_Left_Rise/Nomal/B", "SideHide_Left_Rise/Nomal/C",
    "SideHide_Right_Rise/Nomal/A", "SideHide_Right_Rise/Nomal/B", "SideHide_Right_Rise/Nomal/C"
) + $activityAssetFolders
$assetFolders = @($assetFolders | Sort-Object -Unique)
$assetRoot = Join-Path $stage "assets\pet"
$expectedPngCount = 0
foreach ($relative in $assetFolders) {
    $source = Join-Path $petSource $relative
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "缺少动画目录：$relative" }
    $expectedPngCount += @(Get-ChildItem -LiteralPath $source -File -Filter *.png).Count
    $destination = Join-Path $assetRoot $relative
    New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Recurse
}
$pngCount = @(Get-ChildItem -LiteralPath $assetRoot -Recurse -File -Filter *.png).Count
if ($pngCount -ne $expectedPngCount) {
    throw "内置动画应为 $expectedPngCount 帧，实际为 $pngCount。"
}

Copy-Item -LiteralPath (Join-Path $projectRoot "distribution\config.default.yaml") -Destination $stage
Copy-Item -LiteralPath (Join-Path $projectRoot "mybuddy\reading.txt") -Destination (Join-Path $stage "小布读本.txt")
Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination $stage
Copy-Item -LiteralPath (Join-Path $projectRoot "distribution\THIRD_PARTY_NOTICES.txt") -Destination $stage
Copy-Item -LiteralPath (Join-Path $projectRoot "distribution\使用说明.html") -Destination $stage
$newLine = [Environment]::NewLine
$buildInfo = "version=$productVersion$($newLine)revision=$buildRevision$($newLine)platform=win-x64$newLine"
[IO.File]::WriteAllText(
    (Join-Path $stage "BUILD.txt"),
    $buildInfo,
    [Text.UTF8Encoding]::new($false)
)

$engineVersion = (& (Join-Path $stage "engine\MyBuddyEngine.exe") version | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "打包后心智引擎无法启动。" }
if ($engineVersion -ne "mybuddy $productVersion") {
    throw "心智版本应为 mybuddy $productVersion，实际为 $engineVersion。"
}
$shellVersion = (Get-Item -LiteralPath (Join-Path $stage "BuddyShell.exe")).VersionInfo.ProductVersion
if (-not $shellVersion.StartsWith($productVersion, [StringComparison]::Ordinal)) {
    throw "身体版本应以 $productVersion 开头，实际为 $shellVersion。"
}
$pdbs = @(Get-ChildItem -LiteralPath $stage -Recurse -File -Filter *.pdb)
if ($pdbs) { throw "分发目录不得包含调试符号：$($pdbs.FullName -join ', ')" }

$leaks = Get-ChildItem -LiteralPath $stage -Recurse -File | Where-Object {
    $_.Extension -in ".yaml", ".json", ".txt", ".html", ".config"
} | Select-String -Pattern "sk-or-v1-|sk-ant-|api_key:\s+(?!\$\{MYBUDDY_API_KEY\})\S+"
if ($leaks) { throw "分发目录疑似含有真实 key：$($leaks.Path -join ', ')" }

$previousArtifacts = @(Get-ChildItem -LiteralPath $outputRoot -File | Where-Object {
    $_.Name -like "MyBuddy*-win-x64.zip" -or $_.Name -like "MyBuddy*-win-x64.zip.sha256"
})
if ($previousArtifacts) {
    $previousRoot = Join-Path $outputRoot "previous"
    $previousDirectory = Join-Path $previousRoot (Get-Date -Format "yyyyMMdd-HHmmssfff")
    New-Item -ItemType Directory -Path $previousDirectory -Force | Out-Null
    $previousArtifacts | Move-Item -Destination $previousDirectory
}

$archiveName = "MyBuddy-$productVersion-win-x64.zip"
$archive = Join-Path $outputRoot $archiveName
Compress-Archive -LiteralPath $stage -DestinationPath $archive -CompressionLevel Optimal
$checksum = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = "$archive.sha256"
[IO.File]::WriteAllText(
    $checksumPath,
    "$checksum  $archiveName$([Environment]::NewLine)",
    [Text.UTF8Encoding]::new($false)
)
Write-Host "SHARE_BUILD_OK archive=$archive png=$pngCount version=$productVersion revision=$buildRevision sha256=$checksum"
