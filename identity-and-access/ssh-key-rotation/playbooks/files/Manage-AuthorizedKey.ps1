[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)] [string] $Path,
    [Parameter(Mandatory)] [string] $PublicKey,
    [Parameter(Mandatory)] [string] $Owner,
    [Parameter(Mandatory)] [ValidateSet('standard', 'administrator')] [string] $AccountType,
    [Parameter(Mandatory)] [ValidateSet('present', 'absent')] [string] $State
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# AUTHORIZED_KEY_COMMON_FUNCTIONS

function Set-StrictAcl {
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [Parameter(Mandatory)] [string] $PrincipalSid
    )

    $acl = Get-Acl -LiteralPath $LiteralPath
    $acl.SetAccessRuleProtection($true, $false)
    $identities = @($acl.Access | ForEach-Object { $_.IdentityReference })
    foreach ($identity in $identities) {
        $acl.PurgeAccessRules($identity)
    }
    foreach ($sid in (@($PrincipalSid, 'S-1-5-18') | Sort-Object -Unique)) {
        $identity = [System.Security.Principal.SecurityIdentifier]::new($sid)
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $identity,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        [void] $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $LiteralPath -AclObject $acl
}

$targetMaterial = Get-KeyMaterial -Line $PublicKey
if (-not $targetMaterial) { throw 'Invalid public key' }
$principalSid = Get-PrincipalSid -Account $Owner -Type $AccountType
$exists = Test-Path -LiteralPath $Path -PathType Leaf
$lines = if ($exists) { @(Get-Content -LiteralPath $Path) } else { @() }
$matchingLines = @($lines | Where-Object { (Get-KeyMaterial -Line $_) -eq $targetMaterial })
$aclValid = $exists -and (Test-StrictAcl -LiteralPath $Path -PrincipalSid $principalSid)

if ($State -eq 'present') {
    $keyChange = $matchingLines.Count -eq 0
    $Ansible.Changed = $keyChange -or -not $aclValid
    if (-not $Ansible.CheckMode) {
        $directory = Split-Path -Parent $Path
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
        if ($keyChange) {
            $updated = @($lines) + $PublicKey.Trim()
            [System.IO.File]::WriteAllLines(
                $Path,
                $updated,
                [System.Text.UTF8Encoding]::new($false)
            )
        }
        if (-not (Test-StrictAcl -LiteralPath $Path -PrincipalSid $principalSid)) {
            Set-StrictAcl -LiteralPath $Path -PrincipalSid $principalSid
        }
    }
    $Ansible.Result = @{ present = $true; acl_valid = $true }
    return
}

if (-not $exists) {
    $Ansible.Changed = $false
    $Ansible.Result = @{ removed = 0; acl_valid = $true }
    return
}

$kept = @($lines | Where-Object { (Get-KeyMaterial -Line $_) -ne $targetMaterial })
$removed = $matchingLines.Count
$Ansible.Changed = $removed -gt 0 -or -not $aclValid
if (-not $Ansible.CheckMode) {
    if ($removed -gt 0) {
        [System.IO.File]::WriteAllLines(
            $Path,
            $kept,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    if (-not (Test-StrictAcl -LiteralPath $Path -PrincipalSid $principalSid)) {
        Set-StrictAcl -LiteralPath $Path -PrincipalSid $principalSid
    }
}
$Ansible.Result = @{ removed = $removed; acl_valid = $true }
