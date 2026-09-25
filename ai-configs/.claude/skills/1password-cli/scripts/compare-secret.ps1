<#
.SYNOPSIS
  Compare a secret in 1Password with another value, and print only the verdict.

.DESCRIPTION
  Use this when someone asks "is the stored password the same as the one on the
  server?". The answer is MATCH or DIFFERENT. No value reaches the screen, the
  history, or a log, so the answer is safe to keep in a transcript.

  It compares in one of two ways:
    -Against <reference>  compares two 1Password references.
    (no -Against)         asks for the other value with a hidden prompt.

  The fingerprint is the first 12 characters of the SHA-256 hash. A full hash of
  a short or guessable secret can be attacked offline, so keep it short, and
  prefer the verdict over the fingerprint.

.EXAMPLE
  ./compare-secret.ps1 -Reference "op://Automation/Grafana/password"

.EXAMPLE
  ./compare-secret.ps1 -Reference "op://Automation/Grafana/password" `
                       -Against "op://Backup/Grafana/password"
#>
param(
  [Parameter(Mandatory = $true)][string]$Reference,
  [string]$Against,
  [switch]$ShowFingerprint
)

$ErrorActionPreference = "Stop"

function Get-Fingerprint([string]$Value) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Value))
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
  } finally { $sha.Dispose() }
}

try {
  $a = op read $Reference
  if (-not $a) { throw "could not read $Reference" }

  if ($Against) {
    $b = op read $Against
    if (-not $b) { throw "could not read $Against" }
    $source = $Against
  } else {
    $secure = Read-Host -Prompt "Value to compare (hidden)" -AsSecureString
    $b = ConvertFrom-SecureString $secure -AsPlainText
    $source = "the value you typed"
  }

  $fa = Get-Fingerprint $a
  $fb = Get-Fingerprint $b

  if ($fa -eq $fb) {
    Write-Output "MATCH - $Reference and $source hold the same value."
  } else {
    Write-Output "DIFFERENT - $Reference and $source do not match."
    Write-Output ("  stored length {0}, other length {1}" -f $a.Length, $b.Length)
    if ($a.Trim() -eq $b.Trim()) {
      Write-Output "  The two values differ only in leading or trailing whitespace."
    }
  }

  if ($ShowFingerprint) { Write-Output ("  stored fingerprint {0}" -f $fa.Substring(0, 12)) }
  exit ($(if ($fa -eq $fb) { 0 } else { 1 }))
}
catch {
  # Keep the failure to one line. op has already printed its own error above.
  Write-Output ("compare-secret: {0}" -f $_.Exception.Message)
  exit 2
}
finally {
  $a = $null; $b = $null; $secure = $null; $fa = $null; $fb = $null
  Remove-Variable a, b, secure, fa, fb -ErrorAction SilentlyContinue
  [System.GC]::Collect()
}
