#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

readonly default_toolkit_file=/usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js
readonly default_package=proxmox-widget-toolkit
script_path=${BASH_SOURCE[0]}
script_parent=$([[ $script_path == */* ]] && printf '%s' "${script_path%/*}" || printf '.')
script_dir=$(cd -- "$script_parent" && pwd -P)
output_file="$script_dir/config.local.conf"
print_only=false

usage() {
  cat <<'EOF'
Usage: configure.sh [--output PATH] [--print-discovery]

Detect the proxmox-widget-toolkit file, its package version, its current
subscription-check layout, and the web proxy service. The script never changes
the toolkit file or a service and refuses to replace an existing output file.
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

detected_version=unknown
if command -v dpkg-query >/dev/null 2>&1; then
  # shellcheck disable=SC2016
  detected_version=$(dpkg-query -W -f='${Version}' "$default_package" 2>/dev/null) ||
    detected_version=unknown
fi

# Proxmox VE serves the UI from pveproxy; Proxmox Backup Server uses
# proxmox-backup-proxy with the same toolkit file.
detected_service=pveproxy
if command -v systemctl >/dev/null 2>&1; then
  for candidate in pveproxy proxmox-backup-proxy; do
    if systemctl cat "$candidate.service" >/dev/null 2>&1; then
      detected_service=$candidate
      break
    fi
  done
fi

detected_layout=missing
if [[ -f $default_toolkit_file ]]; then
  detected_layout=$(
    bash "$script_dir/proxmox-subscription-notice.sh" --check \
      --file "$default_toolkit_file" --package "$default_package" 2>&1 || true
  )
fi

printf 'toolkit-file=%s\n' "$default_toolkit_file"
printf 'toolkit-version=%s\n' "${detected_version:-unknown}"
printf 'web-service=%s\n' "$detected_service"
printf 'layout=%s\n' "$detected_layout"
[[ $print_only == false ]] || exit 0

[[ ! -e $output_file ]] || die "refusing to replace existing file: $output_file"
read -r -p "Toolkit file [$default_toolkit_file]: " toolkit_file
toolkit_file=${toolkit_file%$'\r'}
toolkit_file=${toolkit_file:-$default_toolkit_file}
read -r -p "Package that owns the file [$default_package]: " toolkit_package
toolkit_package=${toolkit_package%$'\r'}
toolkit_package=${toolkit_package:-$default_package}
read -r -p "Web proxy service [$detected_service]: " service_name
service_name=${service_name%$'\r'}
service_name=${service_name:-$detected_service}
read -r -p "Restart the service after a change, yes or no [yes]: " restart_service
restart_service=${restart_service%$'\r'}
restart_service=${restart_service:-yes}
[[ $restart_service =~ ^(yes|no)$ ]] || die 'answer yes or no for the restart setting'

if ! (
  # Noclobber makes the redirection an exclusive create. A concurrent process
  # cannot replace a config after the earlier existence check.
  set -o noclobber
  umask 077
  {
    printf '# Generated from local, read-only discovery. Review every CUSTOMIZE marker.\n'
    printf '# CUSTOMIZE: Confirm the proxmoxlib.js path.\nTOOLKIT_FILE=%s\n' "$toolkit_file"
    printf '# CUSTOMIZE: Confirm the package that owns the file.\nTOOLKIT_PACKAGE=%s\n' "$toolkit_package"
    printf '# CUSTOMIZE: Confirm the web proxy service.\nSERVICE_NAME=%s\n' "$service_name"
    printf '# CUSTOMIZE: Choose yes to restart the service after a change.\nRESTART_SERVICE=%s\n' "$restart_service"
  } >"$output_file"
); then
  die "refusing to replace existing file: $output_file"
fi
printf 'configuration-written=%s\n' "$output_file"
printf 'next-step: inspect the file, then run proxmox-subscription-notice.sh --config %q --check\n' "$output_file"
