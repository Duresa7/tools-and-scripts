#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

readonly interfaces_file=/etc/network/interfaces

interface=''
connection_name=''
candidate_file=''
expected_ipv4=''
expected_gateway=''
dns_probe='deb.debian.org'
timeout_seconds=30
confirm=false
dry_run=false
skip_dns=false
backup_file=''
staged_file=''
change_started=false

usage() {
  cat <<'EOF'
Usage:
  networkmanager-ifupdown-cutover.sh \
    --interface INTERFACE \
    --connection CONNECTION \
    --candidate PATH \
    --expect-ipv4 ADDRESS/CIDR \
    --expect-gateway ADDRESS \
    [--dns-probe HOSTNAME] \
    [--timeout SECONDS] \
    [--skip-dns-check] \
    [--dry-run | --confirm]

The candidate file must be a complete replacement for /etc/network/interfaces.
It must not define the selected interface, including through source directives.
The NetworkManager connection profile must already exist and target the selected
interface. A live cutover requires --confirm and root privileges.
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

while (($# > 0)); do
  case $1 in
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
    --skip-dns-check)
      skip_dns=true
      shift
      ;;
    --confirm)
      confirm=true
      shift
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

[[ -n $interface ]] || die '--interface is required'
[[ -n $connection_name ]] || die '--connection is required'
[[ -n $candidate_file ]] || die '--candidate is required'
[[ -n $expected_ipv4 ]] || die '--expect-ipv4 is required'
[[ -n $expected_gateway ]] || die '--expect-gateway is required'
[[ $interface =~ ^[a-zA-Z0-9_.:-]+$ ]] || die 'invalid interface name'
[[ $connection_name != *$'\n'* ]] || die 'connection name contains a newline'
[[ $expected_ipv4 == */* && $expected_ipv4 != *[[:space:]]* ]] ||
  die '--expect-ipv4 must contain an address and prefix length'
[[ $expected_gateway != *[[:space:]]* ]] ||
  die '--expect-gateway must contain one address'
[[ $timeout_seconds =~ ^[1-9][0-9]*$ ]] ||
  die '--timeout must be a positive integer'
[[ $dry_run == false || $confirm == false ]] ||
  die '--dry-run and --confirm cannot be used together'
[[ $dry_run == true || $confirm == true ]] ||
  die 'a live cutover requires --confirm; use --dry-run for preflight'
[[ $dry_run == true || $EUID -eq 0 ]] || die 'a live cutover must run as root'

for command in awk cat cmp cp date flock getent grep ifdown ifquery ifup install \
  ip mktemp mv nmcli readlink rm sleep stat systemctl; do
  command -v "$command" >/dev/null 2>&1 || die "required command not found: $command"
done

[[ -f $interfaces_file ]] || die "not a file: $interfaces_file"
[[ -f $candidate_file ]] || die "not a file: $candidate_file"
candidate_file=$(readlink -f -- "$candidate_file")
[[ $candidate_file != "$interfaces_file" ]] ||
  die 'candidate and live interfaces file must be different'
cmp -s -- "$candidate_file" "$interfaces_file" &&
  die 'candidate is identical to the live interfaces file'

if ! ifquery --interfaces="$interfaces_file" "$interface" >/dev/null 2>&1; then
  die "$interface is not configured through $interfaces_file"
fi
if ifquery --interfaces="$candidate_file" "$interface" >/dev/null 2>&1; then
  die "candidate still configures $interface"
fi

systemctl cat NetworkManager.service >/dev/null 2>&1 ||
  die 'NetworkManager.service is not installed'
profile_interface=$(
  nmcli -g connection.interface-name connection show "$connection_name"
) || die "NetworkManager connection not found: $connection_name"
[[ $profile_interface == "$interface" ]] ||
  die "connection targets '$profile_interface', not '$interface'"

printf 'preflight-ok: interface=%s connection=%q candidate=%s\n' \
  "$interface" "$connection_name" "$candidate_file"
printf 'expected-state: ipv4=%s gateway=%s dns-check=%s\n' \
  "$expected_ipv4" "$expected_gateway" "$([[ $skip_dns == true ]] && echo skipped || echo "$dns_probe")"

if [[ $dry_run == true ]]; then
  printf 'dry-run: no files, interfaces, connections, or services were changed\n'
  exit 0
fi

readonly lock_file=/run/lock/networkmanager-ifupdown-cutover.lock
[[ -d ${lock_file%/*} ]] || die "lock directory does not exist: ${lock_file%/*}"
exec 9>"$lock_file"
flock -n 9 || die "another cutover process holds $lock_file"

cleanup() {
  if [[ -n $staged_file && -e $staged_file ]]; then
    rm -f -- "$staged_file"
  fi
}

rollback() {
  local return_code=${1:-1}
  local file_restored=false
  local networking_restarted=false
  trap - ERR INT TERM
  set +e
  printf 'cutover-failed: exit=%s\n' "$return_code" >&2
  if [[ $change_started == true && -f $backup_file ]]; then
    printf 'rollback-started: restoring=%s\n' "$backup_file" >&2
    if cp -a -- "$backup_file" "$interfaces_file" &&
      cmp -s -- "$backup_file" "$interfaces_file"; then
      file_restored=true
    else
      printf 'rollback-error: interfaces file was not restored\n' >&2
    fi
    nmcli connection down "$connection_name" >/dev/null 2>&1 || true
    if systemctl restart networking.service ||
      ifup --interfaces="$interfaces_file" "$interface"; then
      networking_restarted=true
    else
      printf 'rollback-error: legacy networking did not restart\n' >&2
    fi
    systemctl restart NetworkManager.service ||
      printf 'rollback-warning: NetworkManager did not restart\n' >&2
    if [[ $file_restored == true && $networking_restarted == true ]]; then
      printf 'rollback-finished: verify network access before retrying\n' >&2
    else
      printf 'rollback-incomplete: use a console to restore networking\n' >&2
    fi
  fi
  exit "$return_code"
}

network_ready() {
  [[ $(nmcli -t -f STATE general) == connected ]] || return 1
  nmcli -t -f DEVICE,STATE device status |
    awk -F: -v device="$interface" \
      '$1 == device && $2 == "connected" { found = 1 } END { exit !found }' ||
    return 1
  ip -o -4 address show dev "$interface" |
    awk '{print $4}' |
    grep -Fqx -- "$expected_ipv4" || return 1
  ip -4 route show default dev "$interface" |
    awk -v gateway="$expected_gateway" -v device="$interface" '
      $1 == "default" {
        for (index = 1; index <= NF; index++) {
          if ($index == "via" && $(index + 1) == gateway) via = 1
          if ($index == "dev" && $(index + 1) == device) dev = 1
        }
      }
      END { exit !(via && dev) }
    ' || return 1
  [[ $skip_dns == true ]] || getent ahosts "$dns_probe" >/dev/null
}

trap cleanup EXIT
trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_file="${interfaces_file}.pre-networkmanager.${timestamp}"
[[ ! -e $backup_file ]] || die "backup path already exists: $backup_file"
cp -a -- "$interfaces_file" "$backup_file"
change_started=true

owner=$(stat -c %u "$interfaces_file")
group=$(stat -c %g "$interfaces_file")
mode=$(stat -c %a "$interfaces_file")
staged_file=$(mktemp "${interfaces_file}.candidate.XXXXXX")
install -o "$owner" -g "$group" -m "$mode" -- "$candidate_file" "$staged_file"

ifdown --interfaces="$interfaces_file" "$interface"
mv -f -- "$staged_file" "$interfaces_file"
staged_file=''
systemctl restart NetworkManager.service
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
printf 'backup-file=%s\n' "$backup_file"
nmcli -t -f STATE general
nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status
ip -o -4 address show dev "$interface"
ip -4 route show default dev "$interface"
