param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path $Root ".uv-cache"
}

uv run --extra api mybuddy web --host $HostAddress --port $Port
