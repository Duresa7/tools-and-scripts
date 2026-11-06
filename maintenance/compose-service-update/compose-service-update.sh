#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

container_name=''
project_name=''
service_name=''
allowed_root=''
config_file=''
dry_run=false

usage() {
  cat <<'EOF'
Usage:
  compose-service-update.sh [--config PATH] [OPTIONS] --dry-run
  compose-service-update.sh [--config PATH] [OPTIONS]

Update one Docker Compose service container by inspecting its active Compose
definition, validating that all Compose files stay within an allowed directory
root, pulling the service image, and recreating the container with existing
dependencies preserved.

Configuration precedence: command line, config file, documented default.

Inputs:
  --container NAME            Running container name to inspect
  --project NAME              Docker Compose project name
  --service NAME              Compose service name to pull and recreate
  --allowed-root PATH         Allowed directory root for Compose files
  --config PATH               Strict KEY=value configuration file
  --dry-run                   Print resolved files and planned commands without executing
  -h, --help                  Show this help message and exit
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
    CONTAINER_NAME) container_name=$value ;;
    PROJECT_NAME) project_name=$value ;;
    SERVICE_NAME) service_name=$value ;;
    ALLOWED_COMPOSE_ROOT | ALLOWED_ROOT) allowed_root=$value ;;
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
    --container | --container-name)
      require_value "$1" "${2-}"
      container_name=$2
      shift 2
      ;;
    --project | --project-name)
      require_value "$1" "${2-}"
      project_name=$2
      shift 2
      ;;
    --service | --service-name)
      require_value "$1" "${2-}"
      service_name=$2
      shift 2
      ;;
    --allowed-root | --allowed-compose-root)
      require_value "$1" "${2-}"
      allowed_root=$2
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

[[ -n $container_name ]] || die 'missing required container name (--container or CONTAINER_NAME in config)'
[[ -n $project_name ]] || die 'missing required project name (--project or PROJECT_NAME in config)'
[[ -n $service_name ]] || die 'missing required service name (--service or SERVICE_NAME in config)'
[[ -n $allowed_root ]] || die 'missing required allowed root (--allowed-root or ALLOWED_COMPOSE_ROOT in config)'

[[ $allowed_root == /* ]] || die "allowed root must be an absolute path: $allowed_root"
while [[ $allowed_root == */ && $allowed_root != "/" ]]; do
  allowed_root="${allowed_root%/}"
done

command -v docker >/dev/null 2>&1 || die 'required command not found: docker'
command -v realpath >/dev/null 2>&1 || die 'required command not found: realpath'

canonical_root=$(realpath "$allowed_root" 2>/dev/null) ||
  die "failed to resolve canonical path for allowed root '$allowed_root'"

raw_files_output=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$container_name" 2>/dev/null) ||
  die "failed to inspect container: $container_name"

trimmed_output=$(echo "$raw_files_output" | awk '{$1=$1;print}')
if [[ -z $trimmed_output || $trimmed_output == "<no value>" ]]; then
  die "container '$container_name' has no Compose configuration files label (com.docker.compose.project.config_files)"
fi

compose_file_args=()
resolved_files=()

old_ifs=$IFS
IFS=','
read -r -a raw_files <<< "$raw_files_output"
IFS=$old_ifs

for raw_file in "${raw_files[@]}"; do
  file=$(echo "$raw_file" | awk '{$1=$1;print}')
  [[ -n $file ]] || continue

  [[ $file == /* ]] || die "Compose file path must be absolute: $file"

  if [[ $allowed_root == "/" ]]; then
    :
  else
    case "$file" in
      "$allowed_root"/*) ;;
      *) die "Compose file '$file' is outside allowed root '$allowed_root'" ;;
    esac
  fi

  [[ -f $file ]] || die "Compose file does not exist: $file"

  canonical_file=$(realpath "$file" 2>/dev/null) ||
    die "failed to resolve canonical path for Compose file '$file'"

  if [[ $canonical_root != "/" ]]; then
    case "$canonical_file" in
      "$canonical_root"/*) ;;
      *) die "Compose file '$file' resolves outside allowed root '$allowed_root'" ;;
    esac
  fi

  compose_file_args+=("-f" "$file")
  resolved_files+=("$file")
done

[[ ${#resolved_files[@]} -gt 0 ]] ||
  die "container '$container_name' has no valid Compose configuration files"

pull_cmd=(docker compose --project-name "$project_name" "${compose_file_args[@]}" pull "$service_name")
up_cmd=(docker compose --project-name "$project_name" "${compose_file_args[@]}" up -d --no-deps --no-build --pull never "$service_name")

if [[ $dry_run == true ]]; then
  printf 'Resolved Compose files:\n'
  for resolved in "${resolved_files[@]}"; do
    printf '  %s\n' "$resolved"
  done
  printf 'Planned commands:\n'
  printf '  %s\n' "${pull_cmd[*]}"
  printf '  %s\n' "${up_cmd[*]}"
  exit 0
fi

printf 'Pulling service image: %s\n' "$service_name"
"${pull_cmd[@]}"

printf 'Recreating service container: %s\n' "$service_name"
"${up_cmd[@]}"

printf 'Update completed successfully for service %s in project %s.\n' "$service_name" "$project_name"
