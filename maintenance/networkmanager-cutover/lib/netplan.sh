# shellcheck shell=bash disable=SC2154

stack_preflight() {
  local candidate_name=''
  local temp_root=''

  for command in basename cmp cp install mktemp netplan rm stat; do
    command -v "$command" >/dev/null 2>&1 ||
      die "required Netplan command not found: $command"
  done
  [[ -f $source_file ]] || die "Netplan source file not found: $source_file"
  [[ -f $candidate_file ]] || die "candidate file not found: $candidate_file"
  [[ $source_file == /etc/netplan/*.yaml || $source_file == /etc/netplan/*.yml ]] ||
    die '--source-file must name one YAML file under /etc/netplan'
  cmp -s -- "$candidate_file" "$source_file" &&
    die 'candidate is identical to the current Netplan file'
  grep -Eq '^[[:space:]]*renderer:[[:space:]]*NetworkManager[[:space:]]*$' \
    "$candidate_file" || die 'candidate must set renderer: NetworkManager'
  grep -Fq -- "$interface" "$candidate_file" ||
    die "candidate does not contain interface name: $interface"

  # Netplan parses the candidate in an isolated root before the live file changes.
  temp_root=$(mktemp -d)
  candidate_name=$(basename "$source_file")
  if ! mkdir -p "$temp_root/etc/netplan" ||
    ! cp -- "$candidate_file" "$temp_root/etc/netplan/$candidate_name" ||
    ! netplan generate --root-dir "$temp_root"; then
    rm -r -- "$temp_root"
    die 'Netplan rejected the candidate'
  fi
  rm -r -- "$temp_root"
}

stack_apply() {
  local group=''
  local mode=''
  local owner=''
  local timestamp=''

  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  backup_file="${source_file}.pre-networkmanager.${timestamp}"
  [[ ! -e $backup_file ]] || die "backup already exists: $backup_file"
  cp -a -- "$source_file" "$backup_file"
  owner=$(stat -c %u "$source_file")
  group=$(stat -c %g "$source_file")
  mode=$(stat -c %a "$source_file")
  install -o "$owner" -g "$group" -m "$mode" -- "$candidate_file" "$source_file"
  netplan generate
  netplan apply
}

stack_rollback() {
  if [[ -n $backup_file && -f $backup_file ]]; then
    cp -a -- "$backup_file" "$source_file" ||
      printf 'rollback-error: Netplan file was not restored\n' >&2
    netplan generate && netplan apply ||
      printf 'rollback-error: restored Netplan configuration did not apply\n' >&2
  fi
}
