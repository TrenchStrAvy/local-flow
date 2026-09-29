#!/bin/zsh
# Build an .icns from launcher/icon.png (1024×1024, the sphere on a white
# rounded square). Usage: scripts/make-icns.sh OUT.icns
set -euo pipefail
root=${0:A:h:h}
out=${1:?usage: make-icns.sh OUT.icns}
tmp=$(mktemp -d "${TMPDIR:-/tmp}/sorph-icon.XXXXXX")
set=$tmp/icon.iconset
mkdir -p "$set"
for spec in 16:icon_16x16 32:icon_16x16@2x 32:icon_32x32 64:icon_32x32@2x 128:icon_128x128 \
            256:icon_128x128@2x 256:icon_256x256 512:icon_256x256@2x 512:icon_512x512 1024:icon_512x512@2x; do
  px=${spec%%:*}; name=${spec#*:}
  sips -z "$px" "$px" "$root/launcher/icon.png" --out "$set/$name.png" >/dev/null
done
iconutil -c icns "$set" -o "$out"
rm -rf "$tmp"
