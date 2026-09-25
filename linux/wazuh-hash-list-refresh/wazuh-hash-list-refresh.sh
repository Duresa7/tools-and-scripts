#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

readonly eicar_hash="275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"
readonly eicar_entry="${eicar_hash}:eicar-test-file"

feed_url='https://bazaar.abuse.ch/export/txt/sha256/recent/'
list_path='/var/ossec/etc/lists/known-bad-hashes'
owner='wazuh'
group='wazuh'
mode='660'
min_entries=2
curl_timeout=120
restart_unit='wazuh-manager.service'
restart_command=''
verify_command=''
config_file=''
dry_run=false
output_path=''
tmp_file=''
staged_file=''
raw_feed=''

usage() {
  cat <<'EOF'
Usage:
  sudo wazuh-hash-list-refresh.sh [--config PATH] [OPTIONS]
  wazuh-hash-list-refresh.sh [--config PATH] [OPTIONS] --dry-run
  wazuh-hash-list-refresh.sh [--config PATH] [OPTIONS] --output PATH

Rebuild the Wazuh manager known-bad hash CDB list from the abuse.ch
MalwareBazaar feed, pin the EICAR test hash, and restart the manager.

Configuration precedence: command line, config file, documented default.

Options:
  --feed-url URL          Feed URL to fetch (https://bazaar.abuse.ch/export/txt/sha256/recent/)
  --list-path PATH        CDB list destination path (/var/ossec/etc/lists/known-bad-hashes)
  --owner USER            Installed list owner (wazuh)
  --group GROUP           Installed list group (wazuh)
  --mode MODE             Installed list file mode (660)
  --min-entries COUNT     Minimum entries required to accept the list (2)
  --curl-timeout SECONDS  Download timeout in seconds (120)
  --restart-unit UNIT     systemd service restarted after change (wazuh-manager.service)
  --restart-command CMD   Custom command to execute for restart instead of systemctl
  --verify-command CMD    Optional command to verify service status after custom restart
  --output PATH, -o PATH  Write rebuilt list to PATH without restarting the manager
  --dry-run               Download and build list in temp file, report count, change nothing
  --config PATH           Strict KEY=value configuration file
  -h, --help              Show usage and exit

Exit status:
  0  list rebuilt and manager restarted, list written to --output, or dry run completed
  1  invalid argument, download failure, validation failure, install error, restart failure, or verify failure
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_value() {
  local option=$1
  local value=${2-}
  [[ -n $value ]] || die "$option requires a value"
}

cleanup() {
  if [[ -n ${tmp_file:-} && -f $tmp_file ]]; then
    rm -f "$tmp_file"
  fi
  if [[ -n ${raw_feed:-} && -f $raw_feed ]]; then
    rm -f "$raw_feed"
  fi
  if [[ -n ${staged_file:-} && -f $staged_file ]]; then
    rm -f "$staged_file"
  fi
}
trap cleanup EXIT

set_config_value() {
  local key=$1
  local value=$2
  case $key in
    FEED_URL) feed_url=$value ;;
    LIST_PATH) list_path=$value ;;
    OWNER) owner=$value ;;
    GROUP) group=$value ;;
    MODE) mode=$value ;;
    MIN_ENTRIES) min_entries=$value ;;
    CURL_TIMEOUT) curl_timeout=$value ;;
    RESTART_UNIT) restart_unit=$value ;;
    RESTART_COMMAND) restart_command=$value ;;
    VERIFY_COMMAND) verify_command=$value ;;
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

script_path=${BASH_SOURCE[0]}
script_parent=$([[ $script_path == */* ]] && printf '%s' "${script_path%/*}" || printf '.')
script_dir=$(cd -- "$script_parent" && pwd -P)

arguments=("$@")
for ((index = 0; index < ${#arguments[@]}; index++)); do
  if [[ ${arguments[index]} == --config ]]; then
    ((index + 1 < ${#arguments[@]})) || die '--config requires a value'
    [[ -z $config_file ]] || die '--config may be specified only once'
    config_file=${arguments[index + 1]}
    index=$((index + 1))
  fi
done

if [[ -z $config_file && -f "$script_dir/config.local.conf" ]]; then
  config_file="$script_dir/config.local.conf"
fi
[[ -z $config_file ]] || load_config "$config_file"

while (($# > 0)); do
  case $1 in
    --config)
      shift 2
      ;;
    --feed-url)
      require_value "$1" "${2-}"
      feed_url=$2
      shift 2
      ;;
    --list-path)
      require_value "$1" "${2-}"
      list_path=$2
      shift 2
      ;;
    --owner)
      require_value "$1" "${2-}"
      owner=$2
      shift 2
      ;;
    --group)
      require_value "$1" "${2-}"
      group=$2
      shift 2
      ;;
    --mode)
      require_value "$1" "${2-}"
      mode=$2
      shift 2
      ;;
    --min-entries)
      require_value "$1" "${2-}"
      min_entries=$2
      shift 2
      ;;
    --curl-timeout)
      require_value "$1" "${2-}"
      curl_timeout=$2
      shift 2
      ;;
    --restart-unit)
      require_value "$1" "${2-}"
      restart_unit=$2
      shift 2
      ;;
    --restart-command)
      require_value "$1" "${2-}"
      restart_command=$2
      shift 2
      ;;
    --verify-command)
      require_value "$1" "${2-}"
      verify_command=$2
      shift 2
      ;;
    --output | -o)
      require_value "$1" "${2-}"
      output_path=$2
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --help | -h)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

if ! [[ $min_entries =~ ^[0-9]+$ ]] || ((min_entries < 1)); then
  die "min-entries must be a positive integer: $min_entries"
fi
if ! [[ $curl_timeout =~ ^[0-9]+$ ]] || ((curl_timeout < 1)); then
  die "curl-timeout must be a positive integer: $curl_timeout"
fi

for command in curl grep install mv sed sort tr wc; do
  command -v "$command" >/dev/null 2>&1 || die "required command not found: $command"
done

if [[ -n $output_path ]]; then
  target_path=$output_path
  should_restart=false
else
  target_path=$list_path
  should_restart=true
fi

if [[ $dry_run == false && $should_restart == true && -z $restart_command ]]; then
  command -v systemctl >/dev/null 2>&1 || die "required command not found: systemctl"
fi

if [[ $dry_run == false ]]; then
  target_dir=$(dirname "$target_path")
  if [[ ! -d $target_dir ]]; then
    mkdir -p "$target_dir" || die "failed to create directory: $target_dir"
  fi
fi

tmp_file=$(mktemp -t wazuh-hash-list.XXXXXX)

# The EICAR test file hash is pinned permanently at line 1 so file-integrity
# rule evaluation can be verified on demand without handling live malware.
printf '%s\n' "$eicar_entry" > "$tmp_file"

raw_feed=$(mktemp -t wazuh-raw-feed.XXXXXX)
if ! curl -sfL --max-time "$curl_timeout" "$feed_url" > "$raw_feed"; then
  die "failed to download feed from $feed_url"
fi

# Strip quotes and carriage returns, keep only exact 64-character hex strings,
# normalize to lowercase, exclude any duplicate EICAR hash, deduplicate, and
# format as key:value for the Wazuh manager CDB list.
tr -d '"\r' < "$raw_feed" \
  | grep -Eio '^[a-f0-9]{64}$' \
  | tr 'A-F' 'a-f' \
  | grep -F -v -x "$eicar_hash" \
  | sort -u \
  | sed 's/$/:malwarebazaar/' >> "$tmp_file" || true

count=$(wc -l < "$tmp_file" | tr -d ' ')

# Refuse replacement if feed yielded fewer than min_entries (e.g. empty or down).
if (( count < min_entries )); then
  die "refusing to install a list with $count entries (minimum is $min_entries) -- the feed returned nothing usable"
fi

if [[ $dry_run == true ]]; then
  printf 'dry run: built %s entries for target %s (no changes made)\n' "$count" "$target_path"
  exit 0
fi

install_args=()
# Skip ownership changes when not running as root or when owner/group are empty in config.
if [[ -z $output_path || $(id -u) -eq 0 ]]; then
  if [[ -n $owner ]]; then
    install_args+=(-o "$owner")
  fi
  if [[ -n $group ]]; then
    install_args+=(-g "$group")
  fi
fi
if [[ -n $mode ]]; then
  install_args+=(-m "$mode")
fi

staged_file=$(mktemp -p "$target_dir" .wazuh-hash-list.XXXXXX) || die "failed to create temporary staging file in $target_dir"

if ! install "${install_args[@]}" "$tmp_file" "$staged_file"; then
  die "failed to install list to $target_path"
fi

if ! mv -f "$staged_file" "$target_path"; then
  die "failed to replace $target_path with staged list"
fi

if [[ $should_restart == true ]]; then
  if [[ -n $restart_command ]]; then
    if ! bash -c "$restart_command"; then
      die "restart command failed: $restart_command"
    fi
    if [[ -n $verify_command ]]; then
      if ! bash -c "$verify_command"; then
        die "verify command failed: $verify_command"
      fi
    fi
  else
    # Wazuh analysisd compiles the referenced list on service startup.
    # The manager must restart for updated CDB entries to take effect.
    if ! systemctl restart "$restart_unit"; then
      die "failed to restart $restart_unit"
    fi
    if ! systemctl is-active --quiet "$restart_unit"; then
      die "$restart_unit is not active after restart"
    fi
  fi
  printf 'known-bad hash list rebuilt: %s entries, manager restarted\n' "$count"
else
  printf 'known-bad hash list written: %s entries to %s (service restart skipped)\n' "$count" "$target_path"
fi
