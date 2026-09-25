# shellcheck shell=bash disable=SC2154

stack_preflight() {
  local resolved_candidate=''
  local resolved_source=''

  for command in cmp cp flock ifdown ifquery ifup install mktemp mv readlink rm stat; do
    command -v "$command" >/dev/null 2>&1 ||
      die "required ifupdown command not found: $command"
  done
  [[ -f $source_file ]] || die "ifupdown source file not found: $source_file"
  [[ -f $candidate_file ]] || die "candidate file not found: $candidate_file"
  resolved_candidate=$(readlink -f -- "$candidate_file")
  resolved_source=$(readlink -f -- "$source_file")
  [[ $resolved_candidate != "$resolved_source" ]] ||
    die 'candidate and source files must be different'
  cmp -s -- "$resolved_candidate" "$resolved_source" &&
    die 'candidate is identical to the current ifupdown file'
  ifquery --interfaces="$resolved_source" "$interface" >/dev/null 2>&1 ||
    die "$interface is not owned by $resolved_source"
  ifquery --interfaces="$resolved_candidate" "$interface" >/dev/null 2>&1 &&
    die "candidate still configures $interface"
  candidate_file=$resolved_candidate
  source_file=$resolved_source
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
  staged_file=$(mktemp "${source_file}.candidate.XXXXXX")
  install -o "$owner" -g "$group" -m "$mode" -- "$candidate_file" "$staged_file"

  ifdown --interfaces="$source_file" "$interface"
  mv -f -- "$staged_file" "$source_file"
  staged_file=''
  systemctl restart NetworkManager.service
}

stack_rollback() {
  if [[ -n $backup_file && -f $backup_file ]]; then
    cp -a -- "$backup_file" "$source_file" ||
      printf 'rollback-error: ifupdown file was not restored\n' >&2
    systemctl restart networking.service ||
      ifup --interfaces="$source_file" "$interface" ||
      printf 'rollback-error: ifupdown did not reactivate the interface\n' >&2
    systemctl restart NetworkManager.service || true
  fi
}
