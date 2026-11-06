[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)] [string] $Path,
    [Parameter(Mandatory)] [string] $Owner,
    [Parameter(Mandatory)] [ValidateSet('standard', 'administrator')] [string] $AccountType
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# AUTHORIZED_KEY_COMMON_FUNCTIONS

$exists = Test-Path -LiteralPath $Path -PathType Leaf
$materials = @()
$aclValid = $false
if ($exists) {
    foreach ($line in (Get-Content -LiteralPath $Path)) {
        $material = Get-KeyMaterial -Line $line
        if ($material) { $materials += $material }
    }
    $principalSid = Get-PrincipalSid -Account $Owner -Type $AccountType
    $aclValid = Test-StrictAcl -LiteralPath $Path -PrincipalSid $principalSid
}
$Ansible.Changed = $false
$Ansible.Result = @{
    exists = $exists
    materials = $materials
    acl_valid = $aclValid
}
