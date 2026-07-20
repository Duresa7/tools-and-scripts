#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

readonly confirmation_phrase='CUTOVER_NETWORK_CONNECTION'
script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)

stack=''
interface=''
connection_name=''
candidate_file=''
source_file=''
expected_ipv4=''
expected_gateway=''
expected_dns=''
dns_probe='example.com'
timeout_seconds=30
config_file=''
mode=''
backup_file=''
staged_file=''
previous_connection=''
change_started=false

usage() {
  cat <<'EOF'
Usage:
  networkmanager-cutover.sh [--config PATH] [OPTIONS] --dry-run
  sudo networkmanager-cutover.sh [--config PATH] [OPTIONS] \
    --confirm CUTOVER_NETWORK_CONNECTION
  networkmanager-cutover.sh [--config PATH] [OPTIONS] --validate-only

Configuration precedence: command line, config file, documented default.

Required inputs:
  --stack NAME             ifupdown, netplan, or networkmanager
  --interface NAME         Linux interface, such as enp1s0
  --connection NAME        Existing NetworkManager connection profile
  --expect-ipv4 CIDR       Exact global IPv4 address and prefix
  --expect-gateway ADDRESS Exact default IPv4 gateway
  --expect-dns LIST        Exact comma-separated DNS server set

Preparation inputs:
  --candidate PATH         Reviewed replacement file for ifupdown or Netplan
  --source-file PATH       File replaced during ifupdown or Netplan cutover

Optional inputs:
  --dns-probe HOSTNAME     Name that must resolve after activation (example.com)
  --timeout SECONDS        Readiness timeout (30)
  --config PATH            Strict KEY=value configuration file
  --validate-only          Check current state without changing it
  --dry-run                Run all available preflight checks
  --confirm PHRASE         Perform the cutover after an exact phrase match

Rocky 10 uses --stack networkmanager and does not accept --candidate or
--source-file. The other stacks require both preparation paths.
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

set_config_value() {
  local key=$1
  local value=$2
  case $key in
    STACK) stack=$value ;;
    INTERFACE) interface=$value ;;
    CONNECTION_NAME) connection_name=$value ;;
    CANDIDATE_FILE) candidate_file=$value ;;
    SOURCE_FILE) source_file=$value ;;
    EXPECT_IPV4) expected_ipv4=$value ;;
    EXPECT_GATEWAY) expected_gateway=$value ;;
    EXPECT_DNS) expected_dns=$value ;;
    DNS_PROBE) dns_probe=$value ;;
    TIMEOUT_SECONDS) timeout_seconds=$value ;;
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
    [[ $value != *$'\n'* && $value != *$'\r'* ]] ||
      die "$path:$line_number: multiline values are not supported"
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
    --stack)
      require_value "$1" "${2-}"
      stack=$2
      shift 2
      ;;
    --interface)
      require_value "$1" "${2-}"
      interface=$2
      shift 2
      ;;
    --connection)
      require_value "$1" "${2-}"
      connection_name=$2
      shift 2
      ;;
    --candidate)
      require_value "$1" "${2-}"
      candidate_file=$2
      shift 2
      ;;
    --source-file)
      require_value "$1" "${2-}"
      source_file=$2
      shift 2
      ;;
    --expect-ipv4)
      require_value "$1" "${2-}"
      expected_ipv4=$2
      shift 2
      ;;
    --expect-gateway)
      require_value "$1" "${2-}"
      expected_gateway=$2
      shift 2
      ;;
    --expect-dns)
      require_value "$1" "${2-}"
      expected_dns=$2
      shift 2
      ;;
    --dns-probe)
      require_value "$1" "${2-}"
      dns_probe=$2
      shift 2
      ;;
    --timeout)
      require_value "$1" "${2-}"
      timeout_seconds=$2
      shift 2
      ;;
    --dry-run)
      [[ -z $mode ]] || die 'select one execution mode'
      mode='dry-run'
      shift
      ;;
    --validate-only)
      [[ -z $mode ]] || die 'select one execution mode'
      mode='validate'
      shift
      ;;
    --confirm)
      require_value "$1" "${2-}"
      [[ $2 == "$confirmation_phrase" ]] ||
        die "--confirm must equal $confirmation_phrase"
      [[ -z $mode ]] || die 'select one execution mode'
      mode='cutover'
      shift 2
      ;;
    --help | -h)
      usage
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ $stack =~ ^(ifupdown|netplan|networkmanager)$ ]] ||
  die '--stack must be ifupdown, netplan, or networkmanager'
[[ $interface =~ ^[a-zA-Z0-9_.:-]+$ ]] || die 'invalid or missing interface'
[[ -n $connection_name && $connection_name != *$'\n'* ]] ||
  die '--connection is required and cannot contain a newline'
[[ $expected_ipv4 == */* && $expected_ipv4 != *[[:space:]]* ]] ||
  die '--expect-ipv4 must contain one address and prefix length'
[[ -n $expected_gateway && $expected_gateway != *[[:space:],]* ]] ||
  die '--expect-gateway must contain one address'
[[ -n $expected_dns && $expected_dns != *[[:space:]]* ]] ||
  die '--expect-dns must contain a comma-separated address set'
[[ $timeout_seconds =~ ^[1-9][0-9]*$ ]] ||
  die '--timeout must be a positive integer'
[[ -n $mode ]] || die 'select --dry-run, --validate-only, or --confirm'
[[ $mode != cutover || $EUID -eq 0 ]] || die 'the live cutover requires elevation'

if [[ $stack == networkmanager ]]; then
  [[ -z $candidate_file && -z $source_file ]] ||
    die 'the networkmanager stack does not replace a source file'
elif [[ $mode != validate ]]; then
  [[ -n $candidate_file ]] || die '--candidate is required for this stack'
  [[ -n $source_file ]] || die '--source-file is required for this stack'
fi

for command in awk date flock getent grep ip nmcli paste sleep sort systemctl tr; do
  command -v "$command" >/dev/null 2>&1 || die "required command not found: $command"
done

normalize_dns() {
  tr ',' '\n' | awk 'NF' | sort -u | paste -sd, -
}

expected_dns=$(printf '%s\n' "$expected_dns" | normalize_dns)
[[ -n $expected_dns ]] || die '--expect-dns contains no addresses'

systemctl cat NetworkManager.service >/dev/null 2>&1 ||
  die 'NetworkManager.service is not installed'
profile_interface=$(nmcli -g connection.interface-name connection show "$connection_name") ||
  die "NetworkManager connection not found: $connection_name"
[[ $profile_interface == "$interface" ]] ||
  die "connection targets '$profile_interface', not '$interface'"
[[ $(nmcli -g ipv4.method connection show "$connection_name") != disabled ]] ||
  die 'the selected profile has IPv4 disabled'
profile_ipv4=$(
  nmcli -g ipv4.addresses connection show "$connection_name" |
    tr ',' '\n' | awk 'NF' | sort -u | paste -sd, -
)
[[ $profile_ipv4 == "$expected_ipv4" ]] ||
  die "profile IPv4 is '$profile_ipv4', expected '$expected_ipv4'"
profile_gateway=$(nmcli -g ipv4.gateway connection show "$connection_name")
[[ $profile_gateway == "$expected_gateway" ]] ||
  die "profile gateway is '$profile_gateway', expected '$expected_gateway'"
profile_dns=$(
  nmcli -g ipv4.dns connection show "$connection_name" | normalize_dns
)
[[ $profile_dns == "$expected_dns" ]] ||
  die "profile DNS is '$profile_dns', expected '$expected_dns'"

# Each stack module changes only the file that currently owns the interface.
case $stack in
  ifupdown)
    # shellcheck disable=SC1091
    source "$script_dir/lib/ifupdown.sh"
    ;;
  netplan)
    # shellcheck disable=SC1091
    source "$script_dir/lib/netplan.sh"
    ;;
  networkmanager)
    # shellcheck disable=SC1091
    source "$script_dir/lib/networkmanager.sh"
    ;;
esac

network_ready() {
  local actual_connection=''
  local actual_ipv4=''
  local actual_gateway=''
  local actual_dns=''

  [[ $(nmcli -t -f STATE general) == connected ]] || return 1
  actual_connection=$(nmcli -g GENERAL.CONNECTION device show "$interface") || return 1
  [[ $actual_connection == "$connection_name" ]] || return 1
  actual_ipv4=$(
    ip -o -4 address show dev "$interface" scope global |
      awk '{print $4}' | sort -u | paste -sd, -
  )
  [[ $actual_ipv4 == "$expected_ipv4" ]] || return 1
  actual_gateway=$(
    ip -4 route show default dev "$interface" |
      awk '$1 == "default" && $2 == "via" {print $3}' | sort -u | paste -sd, -
  )
  [[ $actual_gateway == "$expected_gateway" ]] || return 1
  actual_dns=$(nmcli -g IP4.DNS device show "$interface" | normalize_dns)
  [[ $actual_dns == "$expected_dns" ]] || return 1
  [[ -z $dns_probe ]] || getent ahosts "$dns_probe" >/dev/null
}

print_state() {
  printf 'connection=%s\n' "$(nmcli -g GENERAL.CONNECTION device show "$interface")"
  printf 'ipv4=%s\n' "$({ ip -o -4 address show dev "$interface" scope global || true; } | awk '{print $4}' | sort -u | paste -sd, -)"
  printf 'gateway=%s\n' "$({ ip -4 route show default dev "$interface" || true; } | awk '$1 == "default" && $2 == "via" {print $3}' | sort -u | paste -sd, -)"
  printf 'dns=%s\n' "$({ nmcli -g IP4.DNS device show "$interface" || true; } | normalize_dns)"
}

rollback() {
  local return_code=${1:-1}
  trap - ERR INT TERM
  set +e
  printf 'cutover-failed: exit=%s\n' "$return_code" >&2
  if [[ $change_started == true ]]; then
    if [[ -n $staged_file && -e $staged_file ]]; then
      rm -f -- "$staged_file"
    fi
    nmcli connection down "$connection_name" >/dev/null 2>&1 || true
    stack_rollback
    if [[ -n $previous_connection && $previous_connection != -- ]]; then
      nmcli connection up "$previous_connection" >/dev/null 2>&1 ||
        printf 'rollback-warning: prior connection did not reactivate\n' >&2
    fi
    printf 'rollback-finished: verify access from a local console\n' >&2
  fi
  exit "$return_code"
}

if [[ $mode == validate ]]; then
  if network_ready; then
    print_state
    printf 'validation-passed: current state matches every configured assertion\n'
    exit 0
  fi
  print_state
  die 'current state does not match the configured assertions'
fi

stack_preflight
printf 'preflight-ok: stack=%s interface=%s connection=%q\n' \
  "$stack" "$interface" "$connection_name"
printf 'expected-state: ipv4=%s gateway=%s dns=%s probe=%s\n' \
  "$expected_ipv4" "$expected_gateway" "$expected_dns" "${dns_probe:-skipped}"

if [[ $mode == dry-run ]]; then
  printf 'dry-run: no network files, profiles, interfaces, or services were changed\n'
  exit 0
fi

printf 'warning: this operation can interrupt remote access; keep a local console open\n' >&2
readonly lock_file=/run/lock/networkmanager-cutover.lock
exec 9>"$lock_file"
flock -n 9 || die "another cutover process holds $lock_file"
previous_connection=$(nmcli -g GENERAL.CONNECTION device show "$interface" || true)
change_started=true
trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

stack_apply
nmcli connection up "$connection_name"

deadline=$((SECONDS + timeout_seconds))
attempt=0
until network_ready; do
  attempt=$((attempt + 1))
  if ((SECONDS >= deadline)); then
    printf 'network-readiness-timeout: attempts=%s\n' "$attempt" >&2
    false
  fi
  sleep 2
done

trap - ERR INT TERM
change_started=false
printf 'cutover-verified: attempts=%s\n' "$((attempt + 1))"
[[ -z $backup_file ]] || printf 'backup-file=%s\n' "$backup_file"
print_state
printf 'reboot-check: reboot when safe, then rerun this command with --validate-only\n'
