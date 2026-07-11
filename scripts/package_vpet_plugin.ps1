param(
    [string]$Dotnet = $(if ($env:DOTNET) { $env:DOTNET } else { "dotnet" }),
    [string]$Configuration = $(if ($env:CONFIGURATION) { $env:CONFIGURATION } else { "Release" }),
    [string]$PackageName = $(if ($env:VPET_PACKAGE_NAME) { $env:VPET_PACKAGE_NAME } else { "1114_MyBuddyBridge" })
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Project = Join-Path $Root "vpet-plugin\MyBuddy.VPetPlugin.csproj"
$ModSource = Join-Path $Root "vpet-plugin\mod\$PackageName"
$OutDir = Join-Path $Root "dist\vpet\$PackageName"
$PluginDir = Join-Path $OutDir "plugin"

if (-not (Test-Path -LiteralPath (Join-Path $ModSource "info.lps"))) {
    throw "missing VPet mod metadata: $ModSource\info.lps"
}

if (Test-Path -LiteralPath $OutDir) {
    Remove-Item -LiteralPath $OutDir -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Get-ChildItem -LiteralPath $ModSource -Force | Copy-Item -Destination $OutDir -Recurse -Force

& $Dotnet publish $Project `
    --configuration $Configuration `
    --output $PluginDir `
    --self-contained false

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "VPet plugin package: $OutDir"
