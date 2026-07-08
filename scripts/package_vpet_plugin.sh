#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOTNET_BIN="${DOTNET:-dotnet}"
CONFIGURATION="${CONFIGURATION:-Release}"
PACKAGE_NAME="${VPET_PACKAGE_NAME:-1114_MyBuddyBridge}"
PROJECT="$ROOT/vpet-plugin/MyBuddy.VPetPlugin.csproj"
MOD_SOURCE="$ROOT/vpet-plugin/mod/$PACKAGE_NAME"
OUT_DIR="$ROOT/dist/vpet/$PACKAGE_NAME"
PLUGIN_DIR="$OUT_DIR/plugin"

if [[ ! -f "$MOD_SOURCE/info.lps" ]]; then
  echo "missing VPet mod metadata: $MOD_SOURCE/info.lps" >&2
  exit 1
fi

WINDOWS_TARGETING_ARGS=()
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    ;;
  *)
    WINDOWS_TARGETING_ARGS+=("-p:EnableWindowsTargeting=true")
    ;;
esac

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
cp -R "$MOD_SOURCE/." "$OUT_DIR/"

"$DOTNET_BIN" publish "$PROJECT" \
  --configuration "$CONFIGURATION" \
  --output "$PLUGIN_DIR" \
  --self-contained false \
  "${WINDOWS_TARGETING_ARGS[@]}"

echo "VPet plugin package: $OUT_DIR"
