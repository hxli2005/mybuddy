param(
    [int]$Port = 8000,
    [string]$ReadingFile = "data\reading.local.txt"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path $Root ".uv-cache"
}

$engineArgs = @("run", "--extra", "api", "mybuddy", "web", "--port", $Port)
$readingPath = Join-Path $Root $ReadingFile
if (Test-Path -LiteralPath $readingPath -PathType Leaf) {
    $engineArgs += @("--reading-file", (Resolve-Path -LiteralPath $readingPath).Path)
}
& uv @engineArgs
