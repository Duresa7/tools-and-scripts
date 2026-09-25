#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

readonly stock_text="res.data.status.toLowerCase() !== 'active'"
readonly patched_text="res.data.status.toLowerCase() == 'NoMoreNagging'"
# The supported file has exactly this many subscription checks. Any other count
# means upstream changed the layout, so every mode refuses to touch the file.
readonly expected_count=2

toolkit_file=/usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js
toolkit_package=proxmox-widget-toolkit
service_name=pveproxy
restart_service=yes
config_file=''
mode=''
staged_file=''

usage() {
  cat <<'EOF'
Usage:
  proxmox-subscription-notice.sh [--config PATH] [OPTIONS] [--check]
  sudo proxmox-subscription-notice.sh [--config PATH] [OPTIONS] --apply
  sudo proxmox-subscription-notice.sh [--config PATH] [OPTIONS] --restore

Configuration precedence: command line, config file, documented default.

Modes:
  --check             Report the current layout without changing it (default)
  --apply             Replace both stock subscription checks with the patched check
  --restore           Put both stock subscription checks back

Options:
  --config PATH       Strict KEY=value configuration file
  --file PATH         proxmoxlib.js to inspect or change
                      (/usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js)
  --package NAME      Package whose version is reported (proxmox-widget-toolkit)
  --service NAME      Web proxy service restarted after a change (pveproxy)
  --restart           Restart the service after a change (default)
  --no-restart        Leave the service running with the old file cached

Every mode refuses a file that doesn't contain exactly two stock checks or
exactly two patched checks.

Exit status:
  0   check found the patch, or apply/restore finished or had nothing to do
  1   check found the stock layout; the patch is required
  2   the file is missing or unreadable
  3   unsupported layout; nothing changed
  4   the replacement could not be written or failed verification
  5   the file changed and verified, but the service restart failed
  64  invalid argument, invalid configuration, or missing command
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 64
}

fail() {
  local code=$1
  shift
  printf 'error: %s\n' "$*" >&2
  exit "$code"
}

require_value() {
  local option=$1
  local value=${2-}
  [[ -n $value ]] || die "$option requires a value"
}

set_mode() {
  [[ -z $mode ]] || die 'select one of --check, --apply, or --restore'
  mode=$1
}

set_config_value() {
  local key=$1
  local value=$2
  case $key in
    TOOLKIT_FILE) toolkit_file=$value ;;
    TOOLKIT_PACKAGE) toolkit_package=$value ;;
    SERVICE_NAME) service_name=$value ;;
    RESTART_SERVICE) restart_service=$value ;;
    *) die "unknown configuration key: $key" ;;
  esac
}

load_config() {
  local path=$1
  local line=''
  local key=''
  local value=''
  local seen='|'
  local line_number=0

  [[ -f $path ]] || die "configuration file not found: $path"
  while IFS= read -r line || [[ -n $line ]]; do
    line_number=$((line_number + 1))
    line=${line%$'\r'}
    [[ $line =~ ^[[:space:]]*$ || $line =~ ^[[:space:]]*# ]] && continue
    [[ $line == *=* ]] || die "$path:$line_number: expected KEY=value"
    key=${line%%=*}
    value=${line#*=}
    [[ $key =~ ^[A-Z][A-Z0-9_]*$ ]] ||
      die "$path:$line_number: invalid key: $key"
    [[ $seen != *"|$key|"* ]] || die "$path:$line_number: duplicate key: $key"
    set_config_value "$key" "$value"
    seen+="$key|"
  done <"$path"
}

arguments=("$@")
for ((index = 0; index < ${#arguments[@]}; index++)); do
  if [[ ${arguments[index]} == --config ]]; then
    ((index + 1 < ${#arguments[@]})) || die '--config requires a value'
    [[ -z $config_file ]] || die '--config may be specified only once'
    config_file=${arguments[index + 1]}
    index=$((index + 1))
  fi
done
[[ -z $config_file ]] || load_config "$config_file"

while (($# > 0)); do
  case $1 in
    --config)
      shift 2
      ;;
    --file)
      require_value "$1" "${2-}"
      toolkit_file=$2
      shift 2
      ;;
    --package)
      require_value "$1" "${2-}"
      toolkit_package=$2
      shift 2
      ;;
    --service)
      require_value "$1" "${2-}"
      service_name=$2
      shift 2
      ;;
    --restart)
      restart_service=yes
      shift
      ;;
    --no-restart)
      restart_service=no
      shift
      ;;
    --check)
      set_mode check
      shift
      ;;
    --apply)
      set_mode apply
      shift
      ;;
    --restore)
      set_mode restore
      shift
      ;;
    --help | -h)
      usage
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

mode=${mode:-check}
[[ $toolkit_file == /* && $toolkit_file != *$'\n'* ]] ||
  die 'the toolkit file must be an absolute path'
[[ $toolkit_package =~ ^[a-z0-9][a-z0-9+.-]*$ ]] || die 'invalid package name'
[[ $service_name =~ ^[A-Za-z0-9][A-Za-z0-9@._:-]*$ ]] || die 'invalid service name'
[[ $restart_service =~ ^(yes|no)$ ]] || die 'RESTART_SERVICE must be yes or no'

required_commands=(awk)
if [[ $mode != check ]]; then
  required_commands+=(chmod chown mktemp mv rm sed)
  [[ $restart_service == no ]] || required_commands+=(systemctl)
fi
for command in "${required_commands[@]}"; do
  command -v "$command" >/dev/null 2>&1 || die "required command not found: $command"
done

count_text() {
  NEEDLE=$1 awk '
    BEGIN { needle = ENVIRON["NEEDLE"] }
    {
      line = $0
      while ((position = index(line, needle)) > 0) {
        count++
        line = substr(line, position + length(needle))
      }
    }
    END { print count + 0 }
  ' "$2"
}

# Prints stock, patched, or unsupported plus both counts.
layout_state() {
  local path=$1
  local stock_count=''
  local patched_count=''
  stock_count=$(count_text "$stock_text" "$path")
  patched_count=$(count_text "$patched_text" "$path")
  if ((stock_count == expected_count && patched_count == 0)); then
    printf 'stock stock=%s patched=%s\n' "$stock_count" "$patched_count"
  elif ((stock_count == 0 && patched_count == expected_count)); then
    printf 'patched stock=%s patched=%s\n' "$stock_count" "$patched_count"
  else
    printf 'unsupported stock=%s patched=%s\n' "$stock_count" "$patched_count"
  fi
}

sed_pattern() {
  printf '%s' "$1" | sed 's/[][\.*^$/]/\\&/g'
}

sed_replacement() {
  printf '%s' "$1" | sed 's/[&/\]/\\&/g'
}

cleanup() {
  if [[ -n $staged_file && -e $staged_file ]]; then
    rm -f -- "$staged_file"
  fi
}

replace_checks() {
  local from=$1
  local to=$2
  local wanted=$3
  local directory=${toolkit_file%/*}
  local staged_state=''
  local live_state=''

  directory=${directory:-/}
  [[ -w $directory && -w $toolkit_file ]] ||
    fail 4 "no write access to $toolkit_file or its directory; run with elevation"

  trap cleanup EXIT
  # The candidate is built beside the original and checked before it replaces
  # anything, so a failed edit never leaves a half-written file in place.
  staged_file=$(mktemp "$toolkit_file.XXXXXX") ||
    fail 4 "could not create a temporary file beside $toolkit_file"
  sed "s/$(sed_pattern "$from")/$(sed_replacement "$to")/g" "$toolkit_file" >"$staged_file" ||
    fail 4 "could not write the candidate file; $toolkit_file was not changed"
  staged_state=$(layout_state "$staged_file")
  [[ ${staged_state%% *} == "$wanted" ]] ||
    fail 4 "candidate failed verification ($staged_state); $toolkit_file was not changed"
  chmod --reference="$toolkit_file" -- "$staged_file" ||
    fail 4 "could not copy the file mode; $toolkit_file was not changed"
  chown --reference="$toolkit_file" -- "$staged_file" ||
    fail 4 "could not copy the file owner; $toolkit_file was not changed"
  mv -f -- "$staged_file" "$toolkit_file" ||
    fail 4 "could not replace $toolkit_file"
  staged_file=''

  live_state=$(layout_state "$toolkit_file")
  [[ ${live_state%% *} == "$wanted" ]] ||
    fail 4 "$label: post-change verification failed ($live_state)"
}

restart_web_service() {
  if [[ $restart_service == no ]]; then
    printf 'service-restart-skipped: restart %s before browsers load the new file\n' \
      "$service_name"
    return
  fi
  systemctl restart "$service_name" ||
    fail 5 "file changed and verified, but $service_name did not restart"
  systemctl is-active --quiet "$service_name" ||
    fail 5 "file changed and verified, but $service_name is not active"
  printf 'service-restarted: %s\n' "$service_name"
}

[[ -e $toolkit_file ]] || fail 2 "toolkit file not found: $toolkit_file"
[[ -f $toolkit_file && -r $toolkit_file ]] ||
  fail 2 "toolkit file is not a readable regular file: $toolkit_file"

version=''
if command -v dpkg-query >/dev/null 2>&1; then
  # shellcheck disable=SC2016
  version=$(dpkg-query -W -f='${Version}' "$toolkit_package" 2>/dev/null) || version=''
fi
label="$toolkit_package ${version:-unknown}"

state=$(layout_state "$toolkit_file")
case ${state%% *} in
  unsupported)
    fail 3 "$label: unsupported subscription-check layout (${state#* }); nothing changed"
    ;;
  patched)
    case $mode in
      check | apply)
        printf '%s: patch already present (%s)\n' "$label" "${state#* }"
        exit 0
        ;;
      restore)
        replace_checks "$patched_text" "$stock_text" stock
        printf '%s: stock subscription checks restored\n' "$label"
        restart_web_service
        ;;
    esac
    ;;
  stock)
    case $mode in
      check)
        printf '%s: patch required (%s)\n' "$label" "${state#* }"
        exit 1
        ;;
      apply)
        replace_checks "$stock_text" "$patched_text" patched
        printf '%s: patch applied\n' "$label"
        restart_web_service
        ;;
      restore)
        printf '%s: stock subscription checks already present (%s)\n' \
          "$label" "${state#* }"
        exit 0
        ;;
    esac
    ;;
esac
