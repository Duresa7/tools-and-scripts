#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

script_path=${BASH_SOURCE[0]}
script_parent=$([[ $script_path == */* ]] && printf '%s' "${script_path%/*}" || printf '.')
script_dir=$(cd -- "$script_parent" && pwd -P)
output_file="$script_dir/config.local.conf"
print_only=false

usage() {
  cat <<'EOF'
Usage: configure.sh [--output PATH] [--print-discovery]

Detect local interfaces, the default route, active NetworkManager profiles, and
the likely configuration stack. The script never changes network state and
refuses to replace an existing output file.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while (($# > 0)); do
  case $1 in
    --output)
      [[ -n ${2-} ]] || die '--output requires a value'
      output_file=$2
      shift 2
      ;;
    --print-discovery)
      print_only=true
      shift
      ;;
    --help | -h)
      usage
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

for command in awk head ip nmcli paste sort; do
  command -v "$command" >/dev/null 2>&1 || die "required command not found: $command"
done

default_interface=$(ip -4 route show default | awk 'NR == 1 {print $5}')
[[ -n $default_interface ]] || die 'no default-route interface was detected'
current_ipv4=$(ip -o -4 address show dev "$default_interface" scope global | awk 'NR == 1 {print $4}')
current_gateway=$(ip -4 route show default dev "$default_interface" | awk 'NR == 1 && $2 == "via" {print $3}')
current_routes=$(
  ip -4 route show table all default dev "$default_interface" |
    awk '{$1=$1; print}' | sort -u | paste -sd'|' -
)
current_connection=$(nmcli -g GENERAL.CONNECTION device show "$default_interface")
current_dns=$(nmcli -g IP4.DNS device show "$default_interface" | sort -u | paste -sd, -)

detected_stack=networkmanager
detected_source=''
if command -v ifquery >/dev/null 2>&1 &&
  [[ -f /etc/network/interfaces ]] &&
  ifquery --interfaces=/etc/network/interfaces "$default_interface" >/dev/null 2>&1; then
  detected_stack=ifupdown
  detected_source=/etc/network/interfaces
elif compgen -G '/etc/netplan/*.yaml' >/dev/null ||
  compgen -G '/etc/netplan/*.yml' >/dev/null; then
  detected_stack=netplan
  detected_source=$(compgen -G '/etc/netplan/*.yaml' | head -n 1)
  [[ -n $detected_source ]] || detected_source=$(compgen -G '/etc/netplan/*.yml' | head -n 1)
fi

printf 'detected-stack=%s\n' "$detected_stack"
printf 'default-interface=%s\n' "$default_interface"
printf 'active-connection=%s\n' "$current_connection"
printf 'current-ipv4=%s\n' "$current_ipv4"
printf 'current-gateway=%s\n' "$current_gateway"
printf 'current-default-routes=%s\n' "$current_routes"
printf 'current-dns=%s\n' "$current_dns"
printf 'source-file=%s\n' "${detected_source:-not-applicable}"
[[ $print_only == false ]] || exit 0

[[ ! -e $output_file ]] || die "refusing to replace existing file: $output_file"
read -r -p "Stack [$detected_stack]: " stack
stack=${stack%$'\r'}
stack=${stack:-$detected_stack}
read -r -p "Interface [$default_interface]: " interface
interface=${interface%$'\r'}
interface=${interface:-$default_interface}
read -r -p "NetworkManager connection [$current_connection]: " connection
connection=${connection%$'\r'}
connection=${connection:-$current_connection}
read -r -p "Expected IPv4 [$current_ipv4]: " expected_ipv4
expected_ipv4=${expected_ipv4%$'\r'}
expected_ipv4=${expected_ipv4:-$current_ipv4}
read -r -p "Expected gateway [$current_gateway]: " expected_gateway
expected_gateway=${expected_gateway%$'\r'}
expected_gateway=${expected_gateway:-$current_gateway}
read -r -p "Exact default route set [$current_routes]: " expected_routes
expected_routes=${expected_routes%$'\r'}
expected_routes=${expected_routes:-$current_routes}
read -r -p "Expected DNS set [$current_dns]: " expected_dns
expected_dns=${expected_dns%$'\r'}
expected_dns=${expected_dns:-$current_dns}

candidate=''
source_path=''
if [[ $stack != networkmanager ]]; then
  read -r -p "Reviewed candidate path: " candidate
  candidate=${candidate%$'\r'}
  [[ -n $candidate ]] || die 'candidate path is required for this stack'
  read -r -p "Source file [$detected_source]: " source_path
  source_path=${source_path%$'\r'}
  source_path=${source_path:-$detected_source}
fi

if ! (
  # Noclobber makes the redirection an exclusive create. A concurrent process
  # cannot replace a config after the earlier existence check.
  set -o noclobber
  umask 077
  {
    printf '# Generated from local, read-only discovery. Review every CUSTOMIZE marker.\n'
    printf '# CUSTOMIZE: Confirm the detected stack.\nSTACK=%s\n' "$stack"
    printf '# CUSTOMIZE: Confirm the interface.\nINTERFACE=%s\n' "$interface"
    printf '# CUSTOMIZE: Confirm the existing profile name.\nCONNECTION_NAME=%s\n' "$connection"
    printf '# CUSTOMIZE: Supply a reviewed replacement for ifupdown or Netplan.\nCANDIDATE_FILE=%s\n' "$candidate"
    printf '# CUSTOMIZE: Confirm the source file replaced during cutover.\nSOURCE_FILE=%s\n' "$source_path"
    printf '# CUSTOMIZE: Confirm the exact address.\nEXPECT_IPV4=%s\n' "$expected_ipv4"
    printf '# CUSTOMIZE: Confirm the exact gateway.\nEXPECT_GATEWAY=%s\n' "$expected_gateway"
    printf '# CUSTOMIZE: Confirm every default route, including attributes.\nEXPECT_ROUTES=%s\n' "$expected_routes"
    printf '# CUSTOMIZE: Confirm the exact DNS set.\nEXPECT_DNS=%s\n' "$expected_dns"
    printf '# CUSTOMIZE: Choose a DNS name that must resolve.\nDNS_PROBE=example.com\n'
    printf '# CUSTOMIZE: Adjust only when activation needs more than 30 seconds.\nTIMEOUT_SECONDS=30\n'
  } >"$output_file"
); then
  die "refusing to replace existing file: $output_file"
fi
printf 'configuration-written=%s\n' "$output_file"
printf 'next-step: inspect the file, then run networkmanager-cutover.sh --config %q --dry-run\n' "$output_file"
