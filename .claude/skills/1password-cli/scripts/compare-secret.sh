#!/usr/bin/env bash
# Compare a secret in 1Password with another value, and print only the verdict.
#
# Use this when someone asks "is the stored password the same as the one on the
# server?". The answer is MATCH or DIFFERENT. No value reaches the screen, the
# history, or a log, so the answer is safe to keep in a transcript.
#
# Usage:
#   ./compare-secret.sh "op://Automation/Grafana/password"
#   ./compare-secret.sh "op://Automation/Grafana/password" "op://Backup/Grafana/password"
#
# With one reference the script asks for the other value with a hidden prompt.
# The fingerprint is the first 12 characters of the SHA-256 hash. A full hash of
# a short or guessable secret can be attacked offline, so keep it short, and
# prefer the verdict over the fingerprint.

set -euo pipefail

reference="${1:-}"
against="${2:-}"

if [ -z "$reference" ]; then
  echo "usage: $0 <op://reference> [op://other-reference]" >&2
  exit 2
fi

fingerprint() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | cut -d' ' -f1
  else
    printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1   # macOS
  fi
}

a="$(op read -n "$reference")" || { echo "could not read $reference" >&2; exit 2; }

if [ -n "$against" ]; then
  b="$(op read -n "$against")" || { echo "could not read $against" >&2; exit 2; }
  source_name="$against"
else
  printf 'Value to compare (hidden): ' >&2
  IFS= read -rs b
  printf '\n' >&2
  source_name="the value you typed"
fi

fa="$(fingerprint "$a")"
fb="$(fingerprint "$b")"

if [ "$fa" = "$fb" ]; then
  echo "MATCH — $reference and $source_name hold the same value."
  status=0
else
  echo "DIFFERENT — $reference and $source_name do not match."
  echo "  stored length ${#a}, other length ${#b}"
  status=1
fi

unset a b
exit "$status"
