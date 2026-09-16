#!/bin/zsh
# local-flow installer — offline, multilingual dictation for macOS.
#
#   git clone https://github.com/TrenchStrAvy/local-flow.git
#   cd local-flow && ./install.sh
#
# What it does (all local, nothing is sent anywhere):
#   1. checks macOS + Python 3.10+
#   2. creates .venv and installs the Python dependencies
#   3. pre-downloads the speech model(s) so the first dictation is instant
#   4. optionally installs Ollama + gemma3:4b for the cleanup pass
#   5. installs a LaunchAgent so local-flow starts at login (menu-bar mic)
#
# Flags:
#   --multilingual     also pre-download the multilingual model (German,
#                      French, … 99 languages; ~460 MB). Otherwise it is
#                      fetched automatically the first time you switch.
#   --with-cleanup     install Ollama + gemma3:4b (via Homebrew) and enable
#                      the punctuation/filler cleanup pass
#   --no-service       set up the venv and models only; don't install the
#                      login service (run .venv/bin/python flow.py yourself)
#   --model NAME       whisper model to pre-download (default: small.en)

set -euo pipefail

multilingual=false
with_cleanup=false
no_service=false
model=small.en
label=com.localflow.dictation

while (( $# > 0 )); do
  case $1 in
    --multilingual) multilingual=true; shift ;;
    --with-cleanup) with_cleanup=true; shift ;;
    --no-service)   no_service=true; shift ;;
    --model)        model=$2; shift 2 ;;
    -h|--help)      sed -n '2,25p' "$0"; exit 0 ;;
    *) print -u2 "unknown flag: $1 (see --help)"; exit 64 ;;
  esac
done

say() { print -P "%F{cyan}▸%f $*"; }
ok()  { print -P "%F{green}✓%f $*"; }
die() { print -u2 -P "%F{red}✗%f $*"; exit 1; }

root=${0:A:h}
cd "$root"

# 1. platform ----------------------------------------------------------------
[[ $(uname -s) == Darwin ]] || die "local-flow currently supports macOS only"

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

# 5. login service -----------------------------------------------------------
if $no_service; then
  print
  ok "done. Run it with:  .venv/bin/python flow.py ${service_args[*]}"
  exit 0
fi

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
    <string>$root/.venv/bin/python</string>
    <string>$root/flow.py</string>
EOF
  for a in "${service_args[@]}"; do print "    <string>$a</string>"; done
  cat <<EOF
  </array>
  <key>WorkingDirectory</key><string>$root</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
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
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl kickstart -k "$target"
ok "service installed and started (log: ~/Library/Logs/local-flow.log)"

cat <<'EOF'

Next: grant permissions once, then restart the service.
  System Settings → Privacy & Security →
    • Microphone        → allow "Python"
    • Accessibility     → add and enable "Python" (.venv/bin/python)
    • Input Monitoring  → add and enable "Python"
  Then:  launchctl kickstart -k gui/$(id -u)/com.localflow.dictation

Hold Right-Option, speak, release. Look for the mic icon in the menu bar.
EOF
