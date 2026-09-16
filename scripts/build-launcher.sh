#!/bin/zsh
set -euo pipefail

if (( $# != 1 )); then
  print -u2 "usage: $0 /absolute/path/LocalFlow.app"
  exit 64
fi

script_dir=${0:A:h}
project_root=${script_dir:h}
output_app=$1
if [[ $output_app != /* || $output_app != *.app || $output_app == / ]]; then
  print -u2 "output must be a safe absolute .app path"
  exit 64
fi
if [[ -e $output_app ]]; then
  print -u2 "refusing to overwrite existing output: $output_app"
  exit 73
fi
output_parent=${output_app:h}
if [[ ! -d $output_parent ]]; then
  print -u2 "output parent does not exist: $output_parent"
  exit 72
fi

build_temp=$(mktemp -d "${TMPDIR:-/tmp}/localflow-launcher-build.XXXXXX")
cleanup() {
  rm -rf "$build_temp"
}
trap cleanup EXIT

bundle=$build_temp/LocalFlow.app
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"
cp "$project_root/launcher/Info.plist" "$bundle/Contents/Info.plist"

swift_common=(-parse-as-library)
swiftc "${swift_common[@]}" \
  -target x86_64-apple-macos13.0 \
  "$project_root/launcher/LocalFlowLauncher.swift" \
  -o "$build_temp/LocalFlow-x86_64"

if swiftc "${swift_common[@]}" \
  -target arm64-apple-macos13.0 \
  "$project_root/launcher/LocalFlowLauncher.swift" \
  -o "$build_temp/LocalFlow-arm64" 2>"$build_temp/arm64-build.log"; then
  lipo -create \
    "$build_temp/LocalFlow-x86_64" \
    "$build_temp/LocalFlow-arm64" \
    -output "$bundle/Contents/MacOS/LocalFlow"
else
  cp "$build_temp/LocalFlow-x86_64" "$bundle/Contents/MacOS/LocalFlow"
fi
chmod 755 "$bundle/Contents/MacOS/LocalFlow"

swiftc -parse-as-library \
  "$project_root/launcher/IconGenerator.swift" \
  -o "$build_temp/IconGenerator"
"$build_temp/IconGenerator" "$build_temp/LocalFlow.iconset"
iconutil -c icns "$build_temp/LocalFlow.iconset" \
  -o "$bundle/Contents/Resources/LocalFlow.icns"

plutil -lint "$bundle/Contents/Info.plist" >/dev/null
codesign --force --deep --sign - --timestamp=none "$bundle"
codesign --verify --deep --strict --verbose=4 "$bundle"
mv "$bundle" "$output_app"
print "$output_app"
