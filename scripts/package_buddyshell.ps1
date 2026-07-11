param(
    [string]$Configuration = "Release",
    [string]$Runtime = "win-x64",
    [string]$Output = "dist/BuddyShell",
    [string]$PetAssetRoot = "",
    [switch]$IncludePetAssets
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Project = Join-Path $Root "buddyshell/BuddyShell.csproj"
$OutputPath = [IO.Path]::GetFullPath((Join-Path $Root $Output))
$Dotnet = Join-Path $Root ".dotnet-sdk/dotnet.exe"
if (-not (Test-Path -LiteralPath $Dotnet)) { $Dotnet = "dotnet" }

& $Dotnet publish $Project -c $Configuration -r $Runtime --self-contained false -o $OutputPath
if ($LASTEXITCODE -ne 0) { throw "BuddyShell publish failed with exit code $LASTEXITCODE" }

if ($IncludePetAssets) {
    if ([string]::IsNullOrWhiteSpace($PetAssetRoot)) {
        $PetAssetRoot = $env:BUDDYSHELL_PET_ROOT
    }
    if ([string]::IsNullOrWhiteSpace($PetAssetRoot)) {
        $SteamPath = (Get-ItemProperty -LiteralPath "HKCU:\Software\Valve\Steam" -ErrorAction SilentlyContinue).SteamPath
        if (-not [string]::IsNullOrWhiteSpace($SteamPath)) {
            $PetAssetRoot = Join-Path $SteamPath "steamapps/common/VPet/mod/0000_core/pet/vup"
        }
    }
    if ([string]::IsNullOrWhiteSpace($PetAssetRoot)) {
        throw "Pet assets not found. Set BUDDYSHELL_PET_ROOT or pass -PetAssetRoot."
    }
    $ResolvedPetRoot = (Resolve-Path -LiteralPath $PetAssetRoot).Path
    $AssetTarget = Join-Path $OutputPath "assets/pet"
    New-Item -ItemType Directory -Force -Path $AssetTarget | Out-Null
    Copy-Item -Path (Join-Path $ResolvedPetRoot "*") -Destination $AssetTarget -Recurse -Force
}

$Notice = @"
BuddyShell animation asset notice

The VPet default pet animation assets are copyright of the VPet (Virtual Pet)
production team: https://github.com/LorisYounger/VPet
They are included/used for non-commercial purposes only. Separate permission is
required before commercial distribution.
"@
Set-Content -LiteralPath (Join-Path $OutputPath "ASSET-NOTICE.txt") -Value $Notice -Encoding UTF8
Write-Host "BuddyShell package: $OutputPath"
