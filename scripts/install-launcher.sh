#!/bin/zsh
set -euo pipefail

service_label=com.localflow.dictation
dry_run=false
fault_after=

usage() {
  print -u2 "usage: $0 [--dry-run] [--fault-after CHECKPOINT]"
}

while (( $# > 0 )); do
  case $1 in
    --dry-run)
      dry_run=true
      shift
      ;;
    --fault-after)
      if (( $# < 2 )); then
        usage
        exit 64
      fi
      fault_after=$2
      shift 2
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

if [[ -n $fault_after && ${LOCALFLOW_TEST_MODE:-0} != 1 ]]; then
  print -u2 "--fault-after is available only with LOCALFLOW_TEST_MODE=1"
  exit 64
fi

script_dir=${0:A:h}
project_root=${script_dir:h}

if [[ ${LOCALFLOW_TEST_MODE:-0} == 1 ]]; then
  visible_app=${LOCALFLOW_VISIBLE_APP:?}
  support_root=${LOCALFLOW_SUPPORT_ROOT:?}
  launch_agent=${LOCALFLOW_PLIST:?}
  launchctl_bin=${LOCALFLOW_LAUNCHCTL:?}
  builder=${LOCALFLOW_BUILDER:-$project_root/scripts/build-launcher.sh}
  user_id=${LOCALFLOW_UID:?}
  user_home=${LOCALFLOW_HOME:?}
else
  visible_app=/Applications/LocalFlow.app
  account_name=$(id -un)
  user_home=$(dscl . -read "/Users/$account_name" NFSHomeDirectory |
    awk '{print $2}')
  user_id=$(id -u)
  support_root="$user_home/Library/Application Support/LocalFlow"
  launch_agent="$user_home/Library/LaunchAgents/$service_label.plist"
  launchctl_bin=/bin/launchctl
  builder=$project_root/scripts/build-launcher.sh
fi

runtime_destination="$support_root/Runtime/LocalFlow.app"
target="gui/$user_id/$service_label"

require_safe_absolute_path() {
  local name=$1
  local value=$2
  if [[ -z $value || $value != /* || $value == / ]]; then
    print -u2 "unsafe $name path: $value"
    exit 64
  fi
}

require_safe_absolute_path visible_app "$visible_app"
require_safe_absolute_path support_root "$support_root"
require_safe_absolute_path launch_agent "$launch_agent"
require_safe_absolute_path launchctl "$launchctl_bin"
require_safe_absolute_path builder "$builder"
require_safe_absolute_path user_home "$user_home"

for required_tool in \
  /usr/bin/codesign \
  /usr/bin/ditto \
  /usr/bin/plutil \
  /usr/bin/shasum \
  /usr/bin/xattr \
  /usr/libexec/PlistBuddy; do
  if [[ ! -x $required_tool ]]; then
    print -u2 "required tool is unavailable: $required_tool"
    exit 69
  fi
done
if [[ ! -x $launchctl_bin || ! -x $builder ]]; then
  print -u2 "launchctl or launcher builder is not executable"
  exit 69
fi
if [[ ! -f $launch_agent ]]; then
  print -u2 "LaunchAgent is missing: $launch_agent"
  exit 66
fi
/usr/bin/plutil -lint "$launch_agent" >/dev/null

bundle_identifier() {
  /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
    "$1/Contents/Info.plist" 2>/dev/null
}

if [[ -d $runtime_destination ]] &&
  [[ $(bundle_identifier "$runtime_destination") == com.localflow.app ]]; then
  source_runtime=$runtime_destination
  runtime_reused=true
elif [[ -d $visible_app ]] &&
  [[ $(bundle_identifier "$visible_app") == com.localflow.app ]]; then
  source_runtime=$visible_app
  runtime_reused=false
else
  print -u2 "no valid LocalFlow runtime bundle was found"
  exit 66
fi

fingerprint_root=$(mktemp -d "${TMPDIR:-/tmp}/localflow-fingerprint.XXXXXX")
cleanup_fingerprints() {
  if [[ -d $fingerprint_root ]]; then
    find "$fingerprint_root" -depth -delete
  fi
}
trap cleanup_fingerprints EXIT

fingerprint_bundle() {
  local bundle=$1
  local output=$2
  mkdir -p "$output" || return 1
  /usr/bin/codesign --verify --deep --strict --verbose=4 "$bundle" \
    2>"$output/verify.txt" || return 1
  /usr/bin/codesign -dvvv --requirements - "$bundle" \
    >"$output/codesign.txt" 2>&1 || return 1
  awk '/^(Identifier|Format|CodeDirectory|Signature|TeamIdentifier|CDHash)=/ ||
       /^# designated => / {print}' \
    "$output/codesign.txt" >"$output/identity.txt" || return 1
  (
    cd "$bundle"
    find . -type f -print | LC_ALL=C sort | while IFS= read -r relative; do
      digest=$(/usr/bin/shasum -a 256 "$relative" | awk '{print $1}')
      print "$digest  $relative"
    done
  ) >"$output/files.sha256" || return 1
  /usr/bin/xattr -lr "$bundle" 2>/dev/null |
    sed "s#${bundle//\\#/\\\\#}#.#g" |
    LC_ALL=C sort >"$output/xattrs.txt" || return 1
}

if ! fingerprint_bundle "$source_runtime" "$fingerprint_root/source"; then
  print -u2 "could not fingerprint the source runtime"
  cleanup_fingerprints
  exit 65
fi
if ! fingerprint_bundle "$visible_app" "$fingerprint_root/visible-original"; then
  print -u2 "could not fingerprint the visible application"
  cleanup_fingerprints
  exit 65
fi
runtime_cdhash=$(awk -F= '/^CDHash=/{print $2; exit}' \
  "$fingerprint_root/source/identity.txt")
if [[ -z $runtime_cdhash ]]; then
  print -u2 "could not determine runtime CDHash"
  exit 65
fi

if inspection=$("$launchctl_bin" print "$target" 2>&1); then
  service_loaded=true
  service_state=$(print -r -- "$inspection" |
    awk -F' = ' '/^[[:space:]]*state = / {print $2; exit}')
  service_pid=$(print -r -- "$inspection" |
    awk -F' = ' '/^[[:space:]]*pid = / {print $2; exit}')
  if [[ $service_state == running && $service_pid == <-> ]]; then
    service_running=true
  else
    service_running=false
  fi
else
  inspection_status=$?
  if (( inspection_status == 113 )) &&
    [[ $inspection == *"Could not find service"* ]]; then
    service_loaded=false
    service_running=false
  else
    print -u2 "could not inspect $service_label: $inspection"
    exit 69
  fi
fi

print "source_runtime=$source_runtime"
print "runtime_destination=$runtime_destination"
print "visible_app=$visible_app"
print "launch_agent=$launch_agent"
print "service_loaded=$service_loaded"
print "service_running=$service_running"
print "runtime_cdhash=$runtime_cdhash"
print "runtime_reused=$runtime_reused"

if $dry_run; then
  print "dry_run=passed"
  exit 0
fi

safe_delete_tree() {
  local target_path=$1
  if [[ -n $target_path && $target_path == /* &&
    $target_path != / && -e $target_path ]]; then
    find "$target_path" -depth -delete
  fi
}

compare_fingerprints() {
  local expected=$1
  local actual=$2
  for component in identity.txt files.sha256 xattrs.txt; do
    if ! cmp -s "$expected/$component" "$actual/$component"; then
      print -u2 "runtime fingerprint mismatch: $component"
      return 1
    fi
  done
}

poll_service_running() {
  local attempt state pid output
  for attempt in {1..50}; do
    if output=$("$launchctl_bin" print "$target" 2>&1); then
      state=$(print -r -- "$output" |
        awk -F' = ' '/^[[:space:]]*state = / {print $2; exit}')
      pid=$(print -r -- "$output" |
        awk -F' = ' '/^[[:space:]]*pid = / {print $2; exit}')
      if [[ $state == running && $pid == <-> ]]; then
        installed_pid=$pid
        return 0
      fi
    fi
    sleep 0.1
  done
  print -u2 "LocalFlow did not reach a stable running state"
  return 1
}

wait_for_service_unloaded() {
  local attempt output command_code
  for attempt in {1..50}; do
    if output=$("$launchctl_bin" print "$target" 2>&1); then
      sleep 0.1
      continue
    else
      command_code=$?
    fi
    if (( command_code == 113 )) &&
      [[ $output == *"Could not find service"* ]]; then
      return 0
    fi
    print -u2 "could not verify service unload: $output"
    return 1
  done
  print -u2 "timed out waiting for launchd to unload $service_label"
  return 1
}

bootstrap_with_retry() {
  local plist=$1
  local attempt output command_code
  for attempt in {1..25}; do
    if output=$(
      "$launchctl_bin" bootstrap "gui/$user_id" "$plist" 2>&1
    ); then
      return 0
    else
      command_code=$?
    fi
    if (( command_code != 5 )); then
      print -u2 "$output"
      return $command_code
    fi
    sleep 0.2
  done
  print -u2 "${output:-launchctl bootstrap repeatedly returned error 5}"
  return 5
}

maybe_inject_fault() {
  local checkpoint=$1
  if [[ -n $fault_after && $fault_after == $checkpoint ]]; then
    print -u2 "injected installer fault at $checkpoint"
    return 1
  fi
}

mutation_started=false
installation_complete=false
runtime_created=false
visible_replaced=false
runtime_stage=
launcher_stage_root=
plist_stage=
old_visible_swap=
backup_path=
installed_pid=

rollback_installation() {
  local rollback_failed=false
  print -u2 "installation failed; restoring the previous LocalFlow installation"

  "$launchctl_bin" bootout "$target" >/dev/null 2>&1 || true
  wait_for_service_unloaded || rollback_failed=true

  if $visible_replaced; then
    if [[ -n $old_visible_swap && -d $old_visible_swap ]]; then
      safe_delete_tree "$visible_app"
      if ! /bin/mv "$old_visible_swap" "$visible_app"; then
        rollback_failed=true
      fi
    elif [[ -n $backup_path && -d $backup_path/LocalFlow.app ]]; then
      safe_delete_tree "$visible_app"
      if ! /usr/bin/ditto "$backup_path/LocalFlow.app" "$visible_app"; then
        rollback_failed=true
      fi
    fi
  fi

  if [[ -n $backup_path &&
    -f $backup_path/com.localflow.dictation.plist ]]; then
    if ! /bin/cp "$backup_path/com.localflow.dictation.plist" \
      "$launch_agent"; then
      rollback_failed=true
    else
      chmod 644 "$launch_agent"
    fi
  fi

  if $runtime_created; then
    safe_delete_tree "$runtime_destination"
  fi

  if [[ $service_loaded == true ]]; then
    if ! bootstrap_with_retry "$launch_agent"; then
      rollback_failed=true
    elif [[ $service_running != true ]]; then
      if [[ ${LOCALFLOW_TEST_MODE:-0} == 1 ]]; then
        "$launchctl_bin" kill SIGTERM "$target" >/dev/null 2>&1 ||
          rollback_failed=true
      else
        /usr/bin/osascript \
          -e 'tell application id "com.localflow.app" to quit' \
          >/dev/null 2>&1 || rollback_failed=true
      fi
    fi
  fi

  if [[ -n $backup_path && -d $visible_app ]]; then
    if fingerprint_bundle \
      "$visible_app" "$fingerprint_root/rollback-visible"; then
      compare_fingerprints \
        "$fingerprint_root/backup-visible" \
        "$fingerprint_root/rollback-visible" || rollback_failed=true
    else
      rollback_failed=true
    fi
  fi
  if [[ -n $backup_path ]] &&
    ! cmp -s \
      "$backup_path/com.localflow.dictation.plist" \
      "$launch_agent"; then
    rollback_failed=true
  fi
  if $runtime_created && [[ -e $runtime_destination ]]; then
    rollback_failed=true
  fi
  if [[ $service_loaded == true ]]; then
    if restored_state=$("$launchctl_bin" print "$target" 2>&1); then
      restored_running=$(print -r -- "$restored_state" |
        awk -F' = ' '/^[[:space:]]*state = / {print $2; exit}')
      restored_pid=$(print -r -- "$restored_state" |
        awk -F' = ' '/^[[:space:]]*pid = / {print $2; exit}')
      if [[ $service_running == true ]]; then
        [[ $restored_running == running && $restored_pid == <-> ]] ||
          rollback_failed=true
      else
        [[ $restored_running != running ]] || rollback_failed=true
      fi
    else
      rollback_failed=true
    fi
  elif "$launchctl_bin" print "$target" >/dev/null 2>&1; then
    rollback_failed=true
  fi

  if $rollback_failed; then
    print -u2 "automatic rollback was incomplete"
    print -u2 "recovery backup: $backup_path"
    print -u2 "restore app: ditto \"$backup_path/LocalFlow.app\" \"$visible_app\""
    print -u2 "restore plist: cp \"$backup_path/com.localflow.dictation.plist\" \"$launch_agent\""
  else
    print -u2 "rollback=passed"
  fi
}

on_exit() {
  local exit_code=$?
  trap - EXIT
  set +e
  if (( exit_code != 0 )) &&
    [[ $mutation_started == true && $installation_complete != true ]]; then
    rollback_installation
  fi
  [[ -n $runtime_stage ]] && safe_delete_tree "$runtime_stage"
  [[ -n $launcher_stage_root ]] && safe_delete_tree "$launcher_stage_root"
  [[ -n $plist_stage && -e $plist_stage ]] && /bin/rm "$plist_stage"
  cleanup_fingerprints
  exit $exit_code
}
trap on_exit EXIT

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$support_root/Runtime" "$support_root/Backups"
backup_path="$support_root/Backups/$timestamp-$$"
mkdir "$backup_path"
/usr/bin/ditto "$visible_app" "$backup_path/LocalFlow.app"
/bin/cp "$launch_agent" "$backup_path/com.localflow.dictation.plist"
chmod 644 "$backup_path/com.localflow.dictation.plist"
if ! fingerprint_bundle \
  "$backup_path/LocalFlow.app" "$fingerprint_root/backup-visible"; then
  print -u2 "could not fingerprint the visible-app backup"
  exit 65
fi
if ! compare_fingerprints \
  "$fingerprint_root/visible-original" \
  "$fingerprint_root/backup-visible"; then
  print -u2 "visible-app backup does not match the original"
  exit 65
fi

if [[ $source_runtime != $runtime_destination ]]; then
  runtime_stage="$support_root/.Runtime.stage.$$"
  if [[ -e $runtime_stage ]]; then
    print -u2 "runtime staging path already exists: $runtime_stage"
    exit 73
  fi
  /usr/bin/ditto --rsrc --extattr --acl "$source_runtime" "$runtime_stage"
  if ! fingerprint_bundle \
    "$runtime_stage" "$fingerprint_root/staged-runtime"; then
    print -u2 "could not fingerprint the staged runtime"
    exit 65
  fi
  if ! compare_fingerprints \
    "$fingerprint_root/source" \
    "$fingerprint_root/staged-runtime"; then
    exit 65
  fi
fi

launcher_stage_root=$(mktemp -d \
  "${visible_app:h}/.localflow-launcher-stage.XXXXXX")
launcher_stage="$launcher_stage_root/LocalFlow.app"
"$builder" "$launcher_stage" >/dev/null

plist_stage=$(mktemp "${launch_agent:h}/.localflow-plist.XXXXXX")
/bin/cp "$launch_agent" "$plist_stage"
runtime_executable="$runtime_destination/Contents/MacOS/LocalFlow"
/usr/libexec/PlistBuddy \
  -c "Set :ProgramArguments:0 $runtime_executable" \
  "$plist_stage"
/usr/bin/plutil -lint "$plist_stage" >/dev/null
chmod 644 "$plist_stage"

mutation_started=true
if [[ $service_loaded == true ]]; then
  "$launchctl_bin" bootout "$target"
  if ! wait_for_service_unloaded; then
    exit 69
  fi
fi

if [[ -n $runtime_stage ]]; then
  if [[ -e $runtime_destination ]]; then
    print -u2 "refusing to overwrite existing runtime destination"
    exit 73
  fi
  /bin/mv "$runtime_stage" "$runtime_destination"
  runtime_stage=
  runtime_created=true
fi

/bin/mv "$plist_stage" "$launch_agent"
plist_stage=
if ! maybe_inject_fault after-plist; then
  exit 1
fi

old_visible_swap="${visible_app:h}/.LocalFlow.previous.$$"
if [[ -e $old_visible_swap ]]; then
  print -u2 "visible-app swap path already exists: $old_visible_swap"
  exit 73
fi
/bin/mv "$visible_app" "$old_visible_swap"
/bin/mv "$launcher_stage" "$visible_app"
visible_replaced=true
safe_delete_tree "$launcher_stage_root"
launcher_stage_root=
if ! maybe_inject_fault after-visible-app; then
  exit 1
fi

if ! bootstrap_with_retry "$launch_agent"; then
  exit 69
fi
if ! maybe_inject_fault after-bootstrap; then
  exit 1
fi
if ! poll_service_running; then
  exit 1
fi

if ! fingerprint_bundle \
  "$runtime_destination" "$fingerprint_root/installed-runtime"; then
  print -u2 "could not fingerprint the installed runtime"
  exit 65
fi
if ! compare_fingerprints \
  "$fingerprint_root/source" \
  "$fingerprint_root/installed-runtime"; then
  exit 65
fi
/usr/bin/codesign --verify --deep --strict --verbose=4 "$visible_app"
installed_identifier=$(bundle_identifier "$visible_app")
if [[ $installed_identifier != com.localflow.launcher ]]; then
  print -u2 "visible launcher has the wrong bundle identifier"
  exit 65
fi
installed_target=$(/usr/libexec/PlistBuddy \
  -c 'Print :ProgramArguments:0' "$launch_agent")
if [[ $installed_target != $runtime_executable ]]; then
  print -u2 "LaunchAgent does not target the preserved runtime"
  exit 65
fi

safe_delete_tree "$old_visible_swap"
old_visible_swap=
installation_complete=true
print "backup_path=$backup_path"
print "installed_pid=$installed_pid"
print "installation=passed"
