param(
    [string]$ConfigPath = ".\config.yaml",
    [string]$AcceptanceResult = ".\eval\acceptance\v1\RESULT.json",
    [string]$OutputPath = ".\eval\acceptance\v1\FREEZE_MANIFEST.json"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$SafeRoot = $Root.Replace("\", "/")

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Config not found: $ConfigPath"
}

$commit = (git -c "safe.directory=$SafeRoot" -C $Root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read git commit"
}
$configHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ConfigPath).Hash.ToLowerInvariant()

$releaseLevel = "REDUCED"
$deferredBeats = @()
$knownDeviations = @()
if (-not (Test-Path -LiteralPath $AcceptanceResult)) {
    throw "Acceptance result not found: $AcceptanceResult"
}
$result = Get-Content -Raw -LiteralPath $AcceptanceResult | ConvertFrom-Json
if ($result.release_level -in @("FULL", "REDUCED")) {
    $releaseLevel = $result.release_level
}
if ($null -ne $result.deferred_beats) {
    $deferredBeats = @($result.deferred_beats)
}
if ($null -ne $result.known_deviations) {
    $knownDeviations = @($result.known_deviations)
}
$releaseBlocked = $result.release_blocked -eq $true

$AcceptanceRoot = Split-Path -Parent $AcceptanceResult
uv run python scripts/vpet_acceptance_verify.py --root $AcceptanceRoot
if ($LASTEXITCODE -ne 0) {
    throw "Acceptance evidence verification failed"
}

$manifest = [ordered]@{
    tag = "v1.0"
    commit = $commit
    config_sha256 = $configHash
    release_level = $releaseLevel
    release_blocked = $releaseBlocked
    acceptance_result = "eval/acceptance/v1/RESULT.json"
    experiment_window = "2026-08-02/2026-08-17"
    touch_schedule = "08-04 ON, thereafter alternate daily"
    physical_proactive_schedule = "08-04/10 ON; 08-11/17 OFF"
    deferred_beats = $deferredBeats
    known_deviations = $knownDeviations
    generated_at = [DateTimeOffset]::Now.ToString("o")
}

$parent = Split-Path -Parent $OutputPath
if ($parent) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 -LiteralPath $OutputPath
Write-Output "Wrote $OutputPath"
