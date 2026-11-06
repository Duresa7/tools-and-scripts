function Get-KeyMaterial {
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Line)

    $parts = @($Line.Trim() -split '\s+')
    for ($index = 0; $index -lt ($parts.Count - 1); $index++) {
        if ($parts[$index] -match '^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521))$') {
            return $parts[$index] + ' ' + $parts[$index + 1]
        }
    }
    return $null
}

function Get-PrincipalSid {
    param(
        [Parameter(Mandatory)] [string] $Account,
        [Parameter(Mandatory)] [string] $Type
    )

    if ($Type -eq 'administrator') {
        return 'S-1-5-32-544'
    }
    try {
        $name = [System.Security.Principal.NTAccount]::new($Account)
        return $name.Translate([System.Security.Principal.SecurityIdentifier]).Value
    }
    catch {
        throw "Could not resolve the managed Windows account: $Account"
    }
}

function Test-StrictAcl {
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [Parameter(Mandatory)] [string] $PrincipalSid
    )

    $acl = Get-Acl -LiteralPath $LiteralPath
    if (-not $acl.AreAccessRulesProtected) { return $false }
    $rules = @($acl.GetAccessRules(
        $true,
        $false,
        [System.Security.Principal.SecurityIdentifier]
    ))
    $expected = @($PrincipalSid, 'S-1-5-18') | Sort-Object -Unique
    if ($rules.Count -ne $expected.Count) { return $false }
    foreach ($sid in $expected) {
        $matching = @($rules | Where-Object {
            $_.IdentityReference.Value -eq $sid -and
            $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
            ($_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -eq
                [System.Security.AccessControl.FileSystemRights]::FullControl
        })
        if ($matching.Count -ne 1) { return $false }
    }
    return $true
}
