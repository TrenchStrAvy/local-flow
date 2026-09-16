#!/bin/zsh
# local-flow installer — offline, multilingual dictation for macOS.
#
#   git clone https://github.com/TrenchStrAvy/local-flow.git
#   cd local-flow && ./install.sh
#
# What it does (all local; nothing is sent anywhere):
#   1. checks macOS + Python 3.10+
#   2. creates .venv and installs the Python dependencies
#   3. pre-downloads the speech model(s) so the first dictation is instant
#   4. optionally installs Ollama + gemma3:4b for the cleanup pass
#   5. builds a named "LocalFlow" runtime app so macOS privacy settings show
#      LocalFlow (not "Python"), puts a LocalFlow launcher in /Applications
#      and Launchpad, and installs a LaunchAgent that starts it at login
#
# Flags:
#   --multilingual     also pre-download the multilingual model (German,
#                      French, … 99 languages; ~460 MB). Otherwise it is
#                      fetched automatically the first time you switch.
#   --with-cleanup     install Ollama + gemma3:4b (via Homebrew) and enable
#                      the punctuation/filler cleanup pass
#   --no-service       set up the venv and models only; don't install the
#                      runtime, launcher, or login service
#   --model NAME       whisper model to pre-download (default: small.en)
#
# Re-running is safe: the venv, models, and an existing LocalFlow runtime
# (with its permission grants) are reused.

set -euo pipefail

multilingual=false
with_cleanup=false
no_service=false
model=small.en
label=com.localflow.dictation
support_root="$HOME/Library/Application Support/LocalFlow"
runtime_app="$support_root/Runtime/LocalFlow.app"
visible_app=/Applications/LocalFlow.app

while (( $# > 0 )); do
  case $1 in
    --multilingual) multilingual=true; shift ;;
    --with-cleanup) with_cleanup=true; shift ;;
    --no-service)   no_service=true; shift ;;
    --model)        model=$2; shift 2 ;;
    -h|--help)      sed -n '2,28p' "$0"; exit 0 ;;
    *) print -u2 "unknown flag: $1 (see --help)"; exit 64 ;;
  esac
done

say()  { print -P "%F{cyan}▸%f $*"; }
ok()   { print -P "%F{green}✓%f $*"; }
warn() { print -P "%F{yellow}!%f $*"; }
die()  { print -u2 -P "%F{red}✗%f $*"; exit 1; }

root=${0:A:h}
cd "$root"

# 1. platform ----------------------------------------------------------------
[[ $(uname -s) == Darwin ]] || die "local-flow supports macOS only"

python=$(command -v python3 || true)
[[ -n $python ]] || die "python3 not found — install it from python.org or 'brew install python'"
pyver=$("$python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
"$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10+ required (found $pyver)"
ok "macOS, Python $pyver"

# 2. virtualenv + deps -------------------------------------------------------
if [[ ! -x .venv/bin/python ]]; then
  say "creating virtual environment"
  "$python" -m venv .venv
fi
say "installing Python dependencies"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
ok "dependencies installed"

# 3. models ------------------------------------------------------------------
fetch_model() {
  say "downloading speech model '$1' (once; cached in ~/.cache/huggingface)"
  .venv/bin/python - "$1" <<'EOF'
import sys
from faster_whisper import WhisperModel
WhisperModel(sys.argv[1], device="cpu", compute_type="int8")
EOF
  ok "model '$1' ready"
}
fetch_model "$model"
if $multilingual && [[ $model == *.en ]]; then
  fetch_model "${model%.en}"
fi

# 4. optional cleanup pass ---------------------------------------------------
service_args=()
if $with_cleanup; then
  if ! command -v ollama >/dev/null; then
    command -v brew >/dev/null || die "--with-cleanup needs Homebrew (https://brew.sh) to install Ollama"
    say "installing Ollama"
    brew install ollama
  fi
  brew services start ollama >/dev/null 2>&1 || true
  say "pulling gemma3:4b (~3 GB, once)"
  ollama pull gemma3:4b
  service_args=(--ollama)
  ok "cleanup pass enabled"
fi

if $no_service; then
  print
  ok "done. Run it with:  .venv/bin/python flow.py ${service_args[*]}"
  exit 0
fi

# 5a. named runtime app ------------------------------------------------------
# A copy of the Python framework's Python.app, renamed to LocalFlow and
# re-signed. macOS then lists "LocalFlow" in Microphone / Accessibility /
# Input Monitoring instead of "Python", and grants survive reinstalls
# because the bundle is kept once created.
bundle_id() { /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$1/Contents/Info.plist" 2>/dev/null; }

build_runtime() {
  local base src tmp
  base=$(.venv/bin/python -c 'import sys; print(sys.base_prefix)')
  src="$base/Resources/Python.app"
  [[ -d $src ]] || { warn "no Python.app at $src; using plain python for the service"; return 1; }
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/localflow-runtime.XXXXXX")
  /usr/bin/ditto "$src" "$tmp/LocalFlow.app"
  mv "$tmp/LocalFlow.app/Contents/MacOS/Python" "$tmp/LocalFlow.app/Contents/MacOS/LocalFlow"
  local pb=/usr/libexec/PlistBuddy plist="$tmp/LocalFlow.app/Contents/Info.plist"
  $pb -c 'Set :CFBundleExecutable LocalFlow' "$plist"
  $pb -c 'Set :CFBundleIdentifier com.localflow.app' "$plist"
  $pb -c 'Set :CFBundleName LocalFlow' "$plist"
  $pb -c 'Set :CFBundleDisplayName LocalFlow' "$plist" 2>/dev/null \
    || $pb -c 'Add :CFBundleDisplayName string LocalFlow' "$plist"
  $pb -c 'Set :LSUIElement true' "$plist" 2>/dev/null \
    || $pb -c 'Add :LSUIElement bool true' "$plist"
  $pb -c 'Add :NSMicrophoneUsageDescription string "LocalFlow transcribes your dictation locally using the microphone."' "$plist" 2>/dev/null \
    || $pb -c 'Set :NSMicrophoneUsageDescription "LocalFlow transcribes your dictation locally using the microphone."' "$plist"
  /usr/bin/codesign --force --deep --sign - --timestamp=none "$tmp/LocalFlow.app" >/dev/null 2>&1
  # the stub must still find its framework from the new location
  if ! "$tmp/LocalFlow.app/Contents/MacOS/LocalFlow" -c 'import sys' >/dev/null 2>&1; then
    warn "relocated Python.app can't start; using plain python for the service"
    rm -rf "$tmp"; return 1
  fi
  mkdir -p "$support_root/Runtime"
  rm -rf "$runtime_app"
  mv "$tmp/LocalFlow.app" "$runtime_app"
  rm -rf "$tmp"
}

if [[ -d $runtime_app && $(bundle_id "$runtime_app") == com.localflow.app ]] \
   && "$runtime_app/Contents/MacOS/LocalFlow" -c 'import sys' >/dev/null 2>&1; then
  ok "reusing existing LocalFlow runtime (permissions preserved)"
  runtime_ok=true
elif build_runtime; then
  ok "built LocalFlow runtime at $runtime_app"
  runtime_ok=true
else
  runtime_ok=false
fi

if $runtime_ok; then
  program="$runtime_app/Contents/MacOS/LocalFlow"
  site=$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
  perm_name=LocalFlow
else
  program="$root/.venv/bin/python"
  site=""
  perm_name="Python"
fi

# 5b. Launchpad launcher -----------------------------------------------------
# A tiny signed Swift app: clicking it starts the service (or leaves it
# running) and exits. Needs the Xcode Command Line Tools for swiftc.
if command -v swiftc >/dev/null; then
  tmp_launcher=$(mktemp -d "${TMPDIR:-/tmp}/localflow-launcher.XXXXXX")/LocalFlow.app
  if scripts/build-launcher.sh "$tmp_launcher" >/dev/null 2>&1; then
    if [[ -d $visible_app && $(bundle_id "$visible_app") != com.localflow.launcher ]]; then
      warn "$visible_app exists but isn't the local-flow launcher; leaving it alone"
    elif rm -rf "$visible_app" 2>/dev/null && mv "$tmp_launcher" "$visible_app" 2>/dev/null; then
      ok "installed launcher: $visible_app (also in Launchpad)"
    else
      warn "couldn't write $visible_app; skipping the Launchpad launcher"
    fi
  else
    warn "launcher build failed; skipping the Launchpad launcher"
  fi
  rm -rf "${tmp_launcher:h}"
else
  warn "swiftc not found (xcode-select --install); skipping the Launchpad launcher"
fi

# 5c. login service ----------------------------------------------------------
plist=$HOME/Library/LaunchAgents/$label.plist
if [[ -f $plist ]] && ! grep -q "$root/flow.py" "$plist"; then
  print
  print "A LaunchAgent for $label already exists and points elsewhere:"
  print "  $plist"
  print "Leaving it untouched. Remove it and re-run, or use --no-service."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
say "writing $plist"
{
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$program</string>
    <string>$root/flow.py</string>
EOF
  for a in "${service_args[@]}"; do print "    <string>$a</string>"; done
  cat <<EOF
  </array>
  <key>WorkingDirectory</key><string>$root</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
EOF
  [[ -n $site ]] && print "    <key>PYTHONPATH</key><string>$site</string>"
  cat <<EOF
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/local-flow.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/local-flow.log</string>
</dict>
</plist>
EOF
} >"$plist"
plutil -lint "$plist" >/dev/null

target=gui/$(id -u)/$label
launchctl bootout "$target" >/dev/null 2>&1 || true
for _ in {1..50}; do   # bootout is asynchronous; wait until it's gone
  launchctl print "$target" >/dev/null 2>&1 || break
  sleep 0.1
done
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl kickstart -k "$target"
ok "service installed and started (log: ~/Library/Logs/local-flow.log)"

cat <<EOF

Next: grant permissions once, then restart local-flow.
  System Settings → Privacy & Security →
    • Microphone        → allow "$perm_name"
    • Accessibility     → add and enable "$perm_name"
    • Input Monitoring  → add and enable "$perm_name"
  Then click LocalFlow in Launchpad, or run:
    launchctl kickstart -k gui/\$(id -u)/$label

Hold Right-Option, speak, release. Look for the mic icon in the menu bar.
EOF
