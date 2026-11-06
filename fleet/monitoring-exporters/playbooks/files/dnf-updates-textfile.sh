#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

output_file=/var/lib/prometheus/node-exporter/dnf.prom
kernel_package=kernel
config_file=''
dry_run=false
staged_file=''

usage() {
  cat <<'EOF'
Usage:
  sudo dnf-updates-textfile.sh [--config PATH] [OPTIONS]
  dnf-updates-textfile.sh [--config PATH] [OPTIONS] --dry-run

Write pending dnf upgrade counts, a security upgrade count, a reboot flag, and
a check timestamp in the node_exporter textfile format.

Configuration precedence: command line, config file, documented default.

Options:
  --config PATH          Strict KEY=value configuration file
  --output PATH          Textfile to replace; must end in .prom
                         (/var/lib/prometheus/node-exporter/dnf.prom)
  --kernel-package NAME  Package compared with the running kernel (kernel)
  --dry-run              Print the metrics to stdout and write nothing

A failed dnf check leaves the existing textfile unchanged, so node_exporter
keeps serving the last good result and its timestamp shows the age.

Exit status:
  0  textfile written, or metrics printed with --dry-run
  1  invalid input, missing command, or unsafe output path
  2  dnf check-update failed; the existing textfile was left unchanged
  3  the textfile could not be written; the existing textfile was left unchanged
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
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

set_config_value() {
  local key=$1
  local value=$2
  case $key in
    OUTPUT_FILE) output_file=$value ;;
    KERNEL_PACKAGE) kernel_package=$value ;;
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
    --output)
      require_value "$1" "${2-}"
      output_file=$2
      shift 2
      ;;
    --kernel-package)
      require_value "$1" "${2-}"
      kernel_package=$2
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
    *) die "unknown argument: $1" ;;
  esac
done

# node_exporter reads only *.prom files. Requiring the suffix also stops a typo
# from pointing the atomic replace at an unrelated file.
[[ $output_file == /*.prom && $output_file != *$'\n'* ]] ||
  die 'the output file must be an absolute path ending in .prom'
[[ $kernel_package =~ ^[A-Za-z0-9][A-Za-z0-9+._-]*$ ]] ||
  die 'invalid kernel package name'

for command in awk chmod date dnf grep mktemp mv rm rpm sort tail uname; do
  command -v "$command" >/dev/null 2>&1 || die "required command not found: $command"
done

output_directory=${output_file%/*}
output_directory=${output_directory:-/}
[[ -d $output_directory ]] ||
  die "output directory not found: $output_directory; create the textfile collector directory first"
# An existing file must carry this tool's timestamp metric. A file written by
# another collector is never replaced.
if [[ -s $output_file ]] &&
  ! grep -qx '# TYPE dnf_updates_check_timestamp_seconds gauge' "$output_file"; then
  die "refusing to replace $output_file because it wasn't written by this tool"
fi
if [[ $dry_run == false ]]; then
  [[ -w $output_directory ]] ||
    die "no write access to $output_directory; run with elevation or use --dry-run"
fi

# check-update exits 100 when updates exist and 0 when none do. Any other
# status is a real failure, and the old textfile stays in place.
if all_updates=$(dnf -q check-update 2>/dev/null); then
  :
else
  status=$?
  ((status == 100)) ||
    fail 2 "dnf check-update exited with status $status; $output_file was left unchanged"
fi
if security_updates=$(dnf -q check-update --security 2>/dev/null); then
  :
else
  status=$?
  ((status == 100)) ||
    fail 2 "dnf check-update --security exited with status $status; $output_file was left unchanged"
fi

running_kernel=$(uname -r)
newest_kernel=''
# rpm prints "package ... is not installed" on stdout when the package is
# absent, so only a successful query is compared with the running kernel.
if installed_kernels=$(
  rpm -q "$kernel_package" --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n' 2>/dev/null
); then
  newest_kernel=$(printf '%s\n' "$installed_kernels" | sort -V | tail -n 1)
fi
reboot_required=0
if [[ -n $newest_kernel && $running_kernel != "$newest_kernel" ]]; then
  reboot_required=1
fi

# Rows are "name.arch version repo". Rows under an "Obsoleting Packages"
# header have the same shape but aren't pending upgrades.
upgrade_rows() {
  printf '%s\n' "$1" | awk '/^Obsoleting/ { obsoleting = 1 } !obsoleting && NF == 3'
}

count_rows() {
  upgrade_rows "$1" | awk 'END { print NR }'
}

render_metrics() {
  printf '%s\n' \
    '# HELP dnf_upgrades_pending Pending package upgrades by repository, from dnf check-update.' \
    '# TYPE dnf_upgrades_pending gauge'
  upgrade_rows "$all_updates" |
    awk '
      { counts[$3]++ }
      END {
        for (repo in counts) {
          # Repository IDs are plain names; anything else is replaced so a
          # label value never needs escaping.
          label = repo
          gsub(/[^A-Za-z0-9_.:@-]/, "_", label)
          printf "dnf_upgrades_pending{repo=\"%s\"} %d\n", label, counts[repo]
        }
      }
    ' | sort
  printf '%s\n' \
    '# HELP dnf_security_upgrades_pending Pending package upgrades that carry a security advisory.' \
    '# TYPE dnf_security_upgrades_pending gauge' \
    "dnf_security_upgrades_pending $(count_rows "$security_updates")" \
    '# HELP node_reboot_required 1 when the running kernel is not the newest installed kernel.' \
    '# TYPE node_reboot_required gauge' \
    "node_reboot_required $reboot_required" \
    '# HELP dnf_updates_check_timestamp_seconds When this file was written.' \
    '# TYPE dnf_updates_check_timestamp_seconds gauge' \
    "dnf_updates_check_timestamp_seconds $(date +%s)"
}

summary="pending=$(count_rows "$all_updates") security=$(count_rows "$security_updates") reboot_required=$reboot_required"

if [[ $dry_run == true ]]; then
  render_metrics
  printf 'dry-run: %s; %s was not changed\n' "$summary" "$output_file" >&2
  exit 0
fi

cleanup() {
  if [[ -n $staged_file && -e $staged_file ]]; then
    rm -f -- "$staged_file"
  fi
}
trap cleanup EXIT

# The temporary name doesn't end in .prom, so node_exporter never reads a
# partial file. Creating it in the same directory keeps the rename atomic.
staged_file=$(mktemp "$output_file.XXXXXX") ||
  fail 3 "could not create a temporary file beside $output_file"
render_metrics >"$staged_file" ||
  fail 3 "could not write metrics; $output_file was left unchanged"
chmod 0644 -- "$staged_file" ||
  fail 3 "could not set the textfile mode; $output_file was left unchanged"
mv -f -- "$staged_file" "$output_file" ||
  fail 3 "could not replace $output_file; the existing file was left unchanged"
staged_file=''
printf 'textfile-written: %s %s\n' "$output_file" "$summary"
