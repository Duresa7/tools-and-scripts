#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

readonly default_directory=/var/lib/prometheus/node-exporter
script_path=${BASH_SOURCE[0]}
script_parent=$([[ $script_path == */* ]] && printf '%s' "${script_path%/*}" || printf '.')
script_dir=$(cd -- "$script_parent" && pwd -P)
output_file="$script_dir/config.local.conf"
print_only=false

usage() {
  cat <<'EOF'
Usage: configure.sh [--output PATH] [--print-discovery]

Detect the node_exporter textfile directory, the running kernel, and the newest
installed kernel. The script never runs dnf, never writes a metrics file, and
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

# A running node_exporter names its textfile directory on its command line.
detected_directory=''
if command -v pgrep >/dev/null 2>&1; then
  detected_directory=$(
    { pgrep -a node_exporter 2>/dev/null || true; } |
      awk '{
        for (field = 1; field <= NF; field++) {
          if ($field ~ /^--collector\.textfile\.directory=/) {
            sub(/^--collector\.textfile\.directory=/, "", $field)
            print $field
            exit
          }
          if ($field == "--collector.textfile.directory" && field < NF) {
            print $(field + 1)
            exit
          }
        }
      }'
  )
fi
textfile_directory=${detected_directory:-$default_directory}

running_kernel=unknown
command -v uname >/dev/null 2>&1 && running_kernel=$(uname -r)
newest_kernel=unknown
if command -v rpm >/dev/null 2>&1; then
  newest_kernel=$(
    { rpm -q kernel --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n' 2>/dev/null || true; } |
      awk '!/is not installed/' | sort -V | tail -n 1
  )
fi

printf 'textfile-directory=%s (%s)\n' "$textfile_directory" \
  "$([[ -n $detected_directory ]] && printf 'from node_exporter' || printf 'default')"
printf 'textfile-directory-exists=%s\n' "$([[ -d $textfile_directory ]] && printf yes || printf no)"
printf 'running-kernel=%s\n' "$running_kernel"
printf 'newest-installed-kernel=%s\n' "${newest_kernel:-unknown}"
[[ $print_only == false ]] || exit 0

[[ ! -e $output_file ]] || die "refusing to replace existing file: $output_file"
default_output="$textfile_directory/dnf.prom"
read -r -p "Textfile to write [$default_output]: " metrics_file
metrics_file=${metrics_file%$'\r'}
metrics_file=${metrics_file:-$default_output}
[[ $metrics_file == /*.prom ]] || die 'the textfile must be an absolute path ending in .prom'
read -r -p "Kernel package [kernel]: " kernel_package
kernel_package=${kernel_package%$'\r'}
kernel_package=${kernel_package:-kernel}

if ! (
  # Noclobber makes the redirection an exclusive create. A concurrent process
  # cannot replace a config after the earlier existence check.
  set -o noclobber
  umask 077
  {
    printf '# Generated from local, read-only discovery. Review every CUSTOMIZE marker.\n'
    printf '# CUSTOMIZE: Confirm the textfile path inside the collector directory.\nOUTPUT_FILE=%s\n' "$metrics_file"
    printf '# CUSTOMIZE: Confirm the kernel package that provides the running kernel.\nKERNEL_PACKAGE=%s\n' "$kernel_package"
  } >"$output_file"
); then
  die "refusing to replace existing file: $output_file"
fi
printf 'configuration-written=%s\n' "$output_file"
printf 'next-step: inspect the file, then run dnf-updates-textfile.sh --config %q --dry-run\n' "$output_file"
