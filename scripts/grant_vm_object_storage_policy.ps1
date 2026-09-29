param(
    [string]$BucketName = "resumen-escolar-gabitin",
    [string]$VmName = "oracle-form-app-vm",
    [string]$OciProfile = "",
    [string]$OciRegion = "ca-toronto-1",
    [string]$DynamicGroupName = "resumen_escolar_gabitin_publishers",
    [string]$PolicyName = "resumen_escolar_gabitin_object_publish",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-iam-policy-grant.log"
)

$ErrorActionPreference = "Continue"

$logDir = Split-Path -Path $LogPath -Parent
if ($logDir) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}
Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue
$script:TempOciConfigPath = ""

function Write-Redacted([string]$Value) {
    $Value = $Value -replace 'ocid1\.[A-Za-z0-9._-]+', '[OCID_REDACTED]'
    $Value = $Value -replace '[A-Fa-f0-9]{2}(:[A-Fa-f0-9]{2}){15,}', '[FINGERPRINT_REDACTED]'
    $Value = $Value -replace 'C:\\Users\\Martin\\[^\s\r\n]+', '[PATH_REDACTED]'
    Add-Content -LiteralPath $LogPath -Value $Value
    Write-Output $Value
}

function Get-OciProfileValue([string]$Name) {
    $configPath = if ($script:TempOciConfigPath) { $script:TempOciConfigPath } else { Join-Path $env:USERPROFILE ".oci\config" }
    if (-not (Test-Path -LiteralPath $configPath)) {
        throw "No existe OCI config local."
    }

    $profileName = if ([string]::IsNullOrWhiteSpace($OciProfile)) { "DEFAULT" } else { $OciProfile }
    $inProfile = $false
    foreach ($line in Get-Content -LiteralPath $configPath) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^\[(.+)\]$') {
            $inProfile = ($matches[1] -eq $profileName)
            continue
        }
        if ($inProfile -and $trimmed -match "^$([regex]::Escape($Name))\s*=\s*(.+)$") {
            return $matches[1].Trim()
        }
    }
    throw "No se encontro '$Name' en el perfil OCI '$profileName'."
}

function New-TemporaryOciConfig([string]$Passphrase) {
    if ([string]::IsNullOrEmpty($Passphrase)) {
        throw "La passphrase OCI esta vacia."
    }

    $sourcePath = if ($env:OCI_CLI_CONFIG_FILE) { $env:OCI_CLI_CONFIG_FILE } else { Join-Path $env:USERPROFILE ".oci\config" }
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "No existe OCI config local."
    }

    $profileName = if ([string]::IsNullOrWhiteSpace($OciProfile)) { "DEFAULT" } else { $OciProfile }
    $targetPath = Join-Path $env:TEMP ("oci-config-resumen-escolar-" + [guid]::NewGuid().ToString("N"))
    $lines = Get-Content -LiteralPath $sourcePath
    $out = New-Object System.Collections.Generic.List[string]
    $inProfile = $false
    $profileSeen = $false
    $passphraseWritten = $false

    foreach ($line in $lines) {
        if ($line.Trim() -match '^\[(.+)\]$') {
            if ($inProfile -and -not $passphraseWritten) {
                $out.Add("pass_phrase=$Passphrase")
                $passphraseWritten = $true
            }
            $inProfile = ($matches[1] -eq $profileName)
            if ($inProfile) {
                $profileSeen = $true
            }
        }

        if ($inProfile -and $line.Trim() -match '^pass_phrase\s*=') {
            if (-not $passphraseWritten) {
                $out.Add("pass_phrase=$Passphrase")
                $passphraseWritten = $true
            }
            continue
        }

        $out.Add($line)
    }

    if (-not $profileSeen) {
        throw "No se encontro el perfil OCI '$profileName'."
    }
    if ($inProfile -and -not $passphraseWritten) {
        $out.Add("pass_phrase=$Passphrase")
    }

    Set-Content -LiteralPath $targetPath -Value $out -Encoding utf8
    return $targetPath
}

function Get-OciProfileValueOptional([string]$Name) {
    try {
        return Get-OciProfileValue $Name
    } catch {
        return ""
    }
}

function Invoke-OciJson([string[]]$ArgsList, [switch]$AllowFailure) {
    $globalArgs = @()
    if (-not [string]::IsNullOrWhiteSpace($OciProfile)) {
        $globalArgs += @("--profile", $OciProfile)
    }
    if ($script:TempOciConfigPath) {
        $globalArgs += @("--config-file", $script:TempOciConfigPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($OciRegion)) {
        $globalArgs += @("--region", $OciRegion)
    }
    Write-Redacted ("OCI_STEP command=oci {0} {1}" -f ($globalArgs -join " "), ($ArgsList -join " "))
    $raw = & oci @globalArgs @ArgsList 2>&1
    $exitCode = $LASTEXITCODE
    Write-Redacted ("OCI_STEP_EXIT={0}" -f $exitCode)
    if ($exitCode -ne 0) {
        Write-Redacted ("OCI_ERROR command=oci {0} {1}" -f ($globalArgs -join " "), ($ArgsList -join " "))
        $raw | ForEach-Object { Write-Redacted ([string]$_) }
        if ($AllowFailure) { return $null }
        throw "OCI command failed"
    }
    try {
        return ($raw | Out-String | ConvertFrom-Json)
    } catch {
        Write-Redacted ("OCI_JSON_ERROR={0}" -f $_.Exception.Message)
        $raw | ForEach-Object { Write-Redacted ([string]$_) }
        if ($AllowFailure) { return $null }
        throw "OCI JSON parse failed"
    }
}

function Resolve-TenancyId([string]$CompartmentId) {
    $fromConfig = Get-OciProfileValueOptional "tenancy"
    if (-not $fromConfig) {
        $fromConfig = Get-OciProfileValueOptional "tenancy_ocid"
    }
    if ($fromConfig) {
        return $fromConfig
    }

    $current = $CompartmentId
    for ($i = 0; $i -lt 10; $i++) {
        if ($current -like "ocid1.tenancy.*") {
            return $current
        }
        $compartment = Invoke-OciJson @("iam", "compartment", "get", "--compartment-id", $current)
        if (-not $compartment -or -not $compartment.data) {
            break
        }
        $parent = $compartment.data.'compartment-id'
        if (-not $parent) {
            break
        }
        $current = $parent
    }

    throw "No se pudo resolver la tenancy desde el perfil OCI ni desde la jerarquia de compartimentos."
}

function Find-ByName($Items, [string]$Name) {
    @($Items) | Where-Object { $_.name -eq $Name -and $_.'lifecycle-state' -ne "DELETED" } | Select-Object -First 1
}

function Read-OciPassphrase {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $secure = Read-Host "OCI key passphrase (no se guarda ni se imprime)" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        if (-not [string]::IsNullOrEmpty($plain)) {
            return $plain
        }
        Write-Redacted "PASSPHRASE_EMPTY_RETRY=$attempt"
    }
    throw "La passphrase OCI esta vacia."
}

try {
& {
    Write-Redacted "START $(Get-Date -Format o)"
    $profileForLog = if ([string]::IsNullOrWhiteSpace($OciProfile)) { "(default)" } else { $OciProfile }
    Write-Redacted "OCI_PROFILE=$profileForLog"
    Write-Redacted "OCI_REGION=$OciRegion"
    Write-Redacted "BUCKET_NAME=$BucketName"
    Write-Redacted "VM_NAME=$VmName"
    Write-Redacted "DYNAMIC_GROUP_NAME=$DynamicGroupName"
    Write-Redacted "POLICY_NAME=$PolicyName"

    if ([string]::IsNullOrEmpty($env:OCI_CLI_PASSPHRASE)) {
        $env:OCI_CLI_PASSPHRASE = Read-OciPassphrase
    }

    $script:TempOciConfigPath = New-TemporaryOciConfig $env:OCI_CLI_PASSPHRASE
    Write-Redacted "TEMP_OCI_CONFIG_CREATED=true"

    Write-Redacted "OCI_VERSION=$(& oci --version 2>&1)"
    $search = Invoke-OciJson @(
        "search", "resource", "structured-search",
        "--query-text", "query instance resources where displayName = '$VmName'"
    )
    $items = @($search.data.items)
    if ($items.Count -eq 0) {
        Write-Redacted "STOP vm_not_found"
        exit 2
    }
    $instance = $items | Select-Object -First 1
    $instanceId = $instance.identifier
    $compartmentId = $instance.'compartment-id'
    Write-Redacted "VM_FOUND=true"
    Write-Redacted "VM_COMPARTMENT_FOUND=true"

    $tenancyId = Resolve-TenancyId $compartmentId
    Write-Redacted "TENANCY_ID_FOUND=true"

    $bucket = Invoke-OciJson @("os", "bucket", "get", "--name", $BucketName)
    if (-not $bucket -or -not $bucket.data) {
        Write-Redacted "STOP bucket_not_found_for_profile"
        exit 3
    }
    Write-Redacted "BUCKET_FOUND_FOR_PROFILE=true"

    $matchingRule = "ALL {instance.id = '$instanceId'}"
    $groups = Invoke-OciJson @("iam", "dynamic-group", "list", "--compartment-id", $tenancyId, "--all")
    $group = Find-ByName $groups.data $DynamicGroupName
    if ($group) {
        if ($group.'matching-rule' -ne $matchingRule) {
            Write-Redacted "STOP dynamic_group_name_exists_with_different_rule"
            exit 4
        }
        Write-Redacted "DYNAMIC_GROUP_EXISTS=true"
    } else {
        $group = Invoke-OciJson @(
            "iam", "dynamic-group", "create",
            "--compartment-id", $tenancyId,
            "--name", $DynamicGroupName,
            "--description", "Resumen Escolar publisher for oracle-form-app-vm",
            "--matching-rule", $matchingRule
        )
        Write-Redacted "DYNAMIC_GROUP_CREATED=true"
    }

    $statement = "Allow dynamic-group $DynamicGroupName to manage objects in compartment id $compartmentId where target.bucket.name = '$BucketName'"
    $policies = Invoke-OciJson @("iam", "policy", "list", "--compartment-id", $compartmentId, "--all")
    $policy = Find-ByName $policies.data $PolicyName
    if ($policy) {
        $existingStatements = @($policy.statements)
        if ($existingStatements -contains $statement) {
            Write-Redacted "POLICY_EXISTS_WITH_STATEMENT=true"
        } else {
            $updatedStatements = @($existingStatements + $statement)
            $statementsJson = $updatedStatements | ConvertTo-Json -Compress
            $policy = Invoke-OciJson @(
                "iam", "policy", "update",
                "--policy-id", $policy.id,
                "--statements", $statementsJson,
                "--force"
            )
            Write-Redacted "POLICY_UPDATED=true"
        }
    } else {
        $statementsJson = @($statement) | ConvertTo-Json -Compress
        $policy = Invoke-OciJson @(
            "iam", "policy", "create",
            "--compartment-id", $compartmentId,
            "--name", $PolicyName,
            "--description", "Allow oracle-form-app-vm to publish Resumen Escolar prompt objects",
            "--statements", $statementsJson
        )
        Write-Redacted "POLICY_CREATED=true"
    }

    Write-Redacted "POLICY_STATEMENT=Allow dynamic-group $DynamicGroupName to manage objects in compartment id [OCID_REDACTED] where target.bucket.name = '$BucketName'"
    Write-Redacted "NOTE=IAM propagation can take a minute or two before instance principal calls succeed."
    Write-Redacted "END $(Get-Date -Format o)"
    Write-Redacted "exit=0"
}

} finally {
    if ($script:TempOciConfigPath) {
        Remove-Item -LiteralPath $script:TempOciConfigPath -Force -ErrorAction SilentlyContinue
    }
    Remove-Item Env:\OCI_CLI_PASSPHRASE -ErrorAction SilentlyContinue
}
