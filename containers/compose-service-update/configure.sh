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

Discover running Docker Compose containers, project names, service names,
and Compose directories. The script never modifies containers or images and
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
    *)
      die "unknown argument: $1"
      ;;
  esac
done

for command in awk head; do
  command -v "$command" >/dev/null 2>&1 || die "required command not found: $command"
done

docker_available=false
detected_container=''
detected_project=''
detected_service=''
detected_compose_root='/opt/docker'

if command -v docker >/dev/null 2>&1; then
  if container_info=$(docker ps --filter "label=com.docker.compose.project" --format '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}' 2>/dev/null | head -n 1) && [[ -n $container_info ]]; then
    docker_available=true
    detected_container=$(printf '%s' "$container_info" | awk -F'\t' '{print $1}')
    detected_project=$(printf '%s' "$container_info" | awk -F'\t' '{print $2}')
    detected_service=$(printf '%s' "$container_info" | awk -F'\t' '{print $3}')

    if [[ -n $detected_container ]]; then
      config_files=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$detected_container" 2>/dev/null || true)
      if [[ -n $config_files && $config_files != "<no value>" ]]; then
        first_file=${config_files%%,*}
        if [[ -n $first_file && $first_file == /* ]]; then
          parent_dir=$(dirname "$first_file")
          grandparent_dir=$(dirname "$parent_dir")
          if [[ $grandparent_dir != "/" && -n $grandparent_dir ]]; then
            detected_compose_root=$grandparent_dir
          else
            detected_compose_root=$parent_dir
          fi
        fi
      fi
    fi
  elif docker info >/dev/null 2>&1; then
    docker_available=true
  fi
fi

detected_container=${detected_container:-web-service-1}
detected_project=${detected_project:-web}
detected_service=${detected_service:-web}
detected_compose_root=${detected_compose_root:-/opt/docker}

printf 'docker-available=%s\n' "$([[ $docker_available == true ]] && printf yes || printf no)"
printf 'detected-container=%s\n' "$detected_container"
printf 'detected-project=%s\n' "$detected_project"
printf 'detected-service=%s\n' "$detected_service"
printf 'detected-compose-root=%s\n' "$detected_compose_root"
[[ $print_only == false ]] || exit 0

[[ ! -e $output_file ]] || die "refusing to replace existing file: $output_file"

read -r -p "Container name [$detected_container]: " container_name
container_name=${container_name%$'\r'}
container_name=${container_name:-$detected_container}

read -r -p "Compose project name [$detected_project]: " project_name
project_name=${project_name%$'\r'}
project_name=${project_name:-$detected_project}

read -r -p "Compose service name [$detected_service]: " service_name
service_name=${service_name%$'\r'}
service_name=${service_name:-$detected_service}

read -r -p "Allowed Compose root [$detected_compose_root]: " allowed_root
allowed_root=${allowed_root%$'\r'}
allowed_root=${allowed_root:-$detected_compose_root}

[[ -n $container_name ]] || die 'container name is required'
[[ -n $project_name ]] || die 'project name is required'
[[ -n $service_name ]] || die 'service name is required'
[[ $allowed_root == /* ]] || die 'allowed compose root must be an absolute path'

if ! (
  # Noclobber makes the redirection an exclusive create. A concurrent process
  # cannot replace a config after the earlier existence check.
  set -o noclobber
  umask 077
  {
    printf '# Generated from local discovery. Review every CUSTOMIZE marker.\n'
    printf '# Strict KEY=value format. Values are literal; shell expansion is not evaluated.\n\n'
    printf '# CUSTOMIZE: Name of the running container to inspect.\nCONTAINER_NAME=%s\n' "$container_name"
    printf '# CUSTOMIZE: Compose project name passed to docker compose.\nPROJECT_NAME=%s\n' "$project_name"
    printf '# CUSTOMIZE: Compose service name to pull and recreate.\nSERVICE_NAME=%s\n' "$service_name"
    printf '# CUSTOMIZE: Absolute directory path that all Compose definition files must reside within.\nALLOWED_COMPOSE_ROOT=%s\n' "$allowed_root"
  } >"$output_file"
); then
  die "refusing to replace existing file: $output_file"
fi

printf 'configuration-written=%s\n' "$output_file"
printf 'next-step: inspect the file, then run compose-service-update.sh --config %q --dry-run\n' "$output_file"
