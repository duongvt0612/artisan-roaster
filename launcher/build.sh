#!/bin/sh
# Builds the Windows launcher Artisan.exe (GUI subsystem: no console window) with the Artisan icon and version info.
# Usage: launcher/build.sh <output dir>   (any OS: Go cross compiles). NO_ICON=1 skips the icon (no download of goversioninfo).
set -eu
cd "$(dirname "$0")"
OUT="${1:-dist}"
mkdir -p "$OUT"
if [ "${NO_ICON:-0}" != "1" ]; then
  go run github.com/josephspurrier/goversioninfo/cmd/goversioninfo@v1.4.1 -64 -o resource_windows_amd64.syso versioninfo.json
fi
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -H=windowsgui" -o "$OUT/Artisan.exe" .
rm -f resource_windows_amd64.syso
echo "launcher: $OUT/Artisan.exe"
