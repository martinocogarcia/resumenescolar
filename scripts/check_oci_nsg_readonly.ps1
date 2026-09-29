param(
    [string]$VmName = "oracle-form-app-vm",
    [string]$VmPublicIp = "129.153.51.108",
    [string]$OciProfile = "",
    [string]$OciRegion = ""
)

$ErrorActionPreference = "Continue"

function Write-Line([string]$Value) {
    $Value = $Value -replace 'ocid1\.[A-Za-z0-9._-]+', '[OCID_REDACTED]'
    $Value = $Value -replace '[A-Fa-f0-9]{2}(:[A-Fa-f0-9]{2}){15,}', '[FINGERPRINT_REDACTED]'
    $Value = $Value -replace 'C:\\Users\\Martin\\[^\s\r\n]+', '[PATH_REDACTED]'
    Write-Output $Value
}

function Invoke-OciJson([string[]]$ArgsList) {
    $globalArgs = @()
    if ($OciProfile) {
        $globalArgs += @("--profile", $OciProfile)
    }
    if ($OciRegion) {
        $globalArgs += @("--region", $OciRegion)
    }
    $globalArgs += @("--connection-timeout", "8", "--read-timeout", "15", "--no-retry")
    $raw = & oci @globalArgs @ArgsList 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Line ("OCI_ERROR command=oci {0} {1}" -f ($globalArgs -join " "), ($ArgsList -join " "))
        $raw | ForEach-Object { Write-Line ([string]$_) }
        return $null
    }
    try {
        return ($raw | Out-String | ConvertFrom-Json)
    } catch {
        Write-Line ("OCI_JSON_PARSE_ERROR command=oci {0} error={1}" -f ($ArgsList -join " "), $_.Exception.Message)
        return $null
    }
}

function Show-SshRulesForVnic($VnicData) {
    Write-Line "TARGET_VNIC_ID_FOUND=true"

    $nsgIds = @($VnicData.'nsg-ids')
    Write-Line "NSG_COUNT=$($nsgIds.Count)"
    foreach ($nsgId in $nsgIds) {
        $nsg = Invoke-OciJson @("network", "nsg", "get", "--nsg-id", $nsgId)
        $nsgName = if ($nsg) { $nsg.data.'display-name' } else { "[unknown]" }
        Write-Line "NSG=$nsgName"
        $rules = Invoke-OciJson @("network", "nsg", "rules", "list", "--nsg-id", $nsgId, "--all")
        if (-not $rules) { continue }
        foreach ($rule in @($rules.data)) {
            $min = $rule.'tcp-options'.'destination-port-range'.min
            $max = $rule.'tcp-options'.'destination-port-range'.max
            if ($rule.direction -eq "INGRESS" -and $rule.protocol -eq "6" -and $min -le 22 -and $max -ge 22) {
                Write-Line "NSG_SSH_RULE source=$($rule.source) stateless=$($rule.'is-stateless')"
            }
        }
    }

    $subnet = Invoke-OciJson @("network", "subnet", "get", "--subnet-id", $VnicData.'subnet-id')
    if (-not $subnet) { return }
    foreach ($slId in @($subnet.data.'security-list-ids')) {
        $sl = Invoke-OciJson @("network", "security-list", "get", "--security-list-id", $slId)
        if (-not $sl) { continue }
        Write-Line "SECURITY_LIST=$($sl.data.'display-name')"
        foreach ($rule in @($sl.data.'ingress-security-rules')) {
            $min = $rule.'tcp-options'.'destination-port-range'.min
            $max = $rule.'tcp-options'.'destination-port-range'.max
            if ($rule.protocol -eq "6" -and $min -le 22 -and $max -ge 22) {
                Write-Line "SECLIST_SSH_RULE source=$($rule.source) stateless=$($rule.'is-stateless')"
            }
        }
    }
}

Write-Line ("START {0}" -f (Get-Date -Format o))
Write-Line "OCI_PROFILE=$OciProfile"
Write-Line "OCI_REGION=$OciRegion"

try {
    $publicIp = Invoke-RestMethod -UseBasicParsing -Uri "https://api.ipify.org" -TimeoutSec 15
    Write-Line "CURRENT_PUBLIC_IP=$publicIp"
} catch {
    Write-Line "CURRENT_PUBLIC_IP_ERROR=$($_.Exception.Message)"
}

try {
    $sshOk = Test-NetConnection -ComputerName $VmPublicIp -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue
    Write-Line "SSH_22_TO_VM=$sshOk"
} catch {
    Write-Line "SSH_22_TO_VM_ERROR=$($_.Exception.Message)"
}

if (-not $env:OCI_CLI_PASSPHRASE) {
    $secure = Read-Host "OCI key passphrase (no se guarda ni se imprime)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $passphrase = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        $env:OCI_CLI_PASSPHRASE = $passphrase
        $env:OCI_CLI_KEY_PASSPHRASE = $passphrase
        $env:OCI_CLI_PASS_PHRASE = $passphrase
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

Write-Line ("OCI_VERSION={0}" -f (& oci --version 2>&1))

$search = Invoke-OciJson @(
    "search", "resource", "structured-search",
    "--query-text", "query instance resources where displayName = '$VmName'"
)
if (-not $search) {
    Write-Line "STOP search_failed"
    exit 1
}

$items = @($search.data.items)
if ($items.Count -eq 0) {
    Write-Line "VM_NOT_FOUND=$VmName"
    exit 2
}

$instance = $items | Select-Object -First 1
$instanceId = $instance.identifier
$compartmentId = $instance.'compartment-id'
$availabilityDomain = $instance.'availability-domain'
Write-Line "VM_FOUND=$VmName"
Write-Line "VM_AVAILABILITY_DOMAIN=$availabilityDomain"

$attachments = Invoke-OciJson @(
    "compute", "vnic-attachment", "list",
    "--compartment-id", $compartmentId,
    "--instance-id", $instanceId,
    "--all"
)
if (-not $attachments) {
    Write-Line "STOP vnic_attachment_failed"
    exit 1
}

$matched = $false
foreach ($attachment in @($attachments.data)) {
    $vnic = Invoke-OciJson @("network", "vnic", "get", "--vnic-id", $attachment.'vnic-id')
    if (-not $vnic) { continue }
    $vnicData = $vnic.data
    Write-Line "VNIC_PUBLIC_IP=$($vnicData.'public-ip')"
    Write-Line "VNIC_PRIVATE_IP=$($vnicData.'private-ip')"
    Write-Line "VNIC_SUBNET_ID=$($vnicData.'subnet-id')"
    Write-Line "VNIC_RULES_FOR_NAMED_VM_BEGIN=true"
    Show-SshRulesForVnic $vnicData
    Write-Line "VNIC_RULES_FOR_NAMED_VM_END=true"
    if ($vnicData.'public-ip' -ne $VmPublicIp) { continue }

    $matched = $true
    Write-Line "MATCHED_TARGET_VNIC=true"
    Show-SshRulesForVnic $vnicData
}

if (-not $matched) {
    Write-Line "TARGET_PUBLIC_IP_NOT_FOUND_ON_VM=$VmPublicIp"
    Write-Line "LOOKUP_PUBLIC_IP_BY_ADDRESS=$VmPublicIp"
    $publicIp = Invoke-OciJson @("network", "public-ip", "get", "--public-ip-address", $VmPublicIp)
    if ($publicIp -and $publicIp.data) {
        Write-Line "PUBLIC_IP_RESOURCE_FOUND=true"
        Write-Line "PUBLIC_IP_LIFETIME=$($publicIp.data.lifetime)"
        Write-Line "PUBLIC_IP_SCOPE=$($publicIp.data.scope)"
        Write-Line "PUBLIC_IP_LIFECYCLE=$($publicIp.data.'lifecycle-state')"
        $privateIpId = $publicIp.data.'private-ip-id'
        if ($privateIpId) {
            $privateIp = Invoke-OciJson @("network", "private-ip", "get", "--private-ip-id", $privateIpId)
            if ($privateIp -and $privateIp.data) {
                Write-Line "PRIVATE_IP_ADDRESS=$($privateIp.data.'ip-address')"
                $targetVnic = Invoke-OciJson @("network", "vnic", "get", "--vnic-id", $privateIp.data.'vnic-id')
                if ($targetVnic -and $targetVnic.data) {
                    Write-Line "PUBLIC_IP_ATTACHED_VNIC_PUBLIC_IP=$($targetVnic.data.'public-ip')"
                    Show-SshRulesForVnic $targetVnic.data
                }
            }
        } else {
            Write-Line "PUBLIC_IP_PRIVATE_IP_ID_EMPTY=true"
        }
    }

    Write-Line "LIST_PUBLIC_IPS_REGION_RESERVED_BEGIN=true"
    $regionPublicIps = Invoke-OciJson @(
        "network", "public-ip", "list",
        "--compartment-id", $compartmentId,
        "--scope", "REGION",
        "--lifetime", "RESERVED",
        "--all"
    )
    if ($regionPublicIps -and $regionPublicIps.data) {
        foreach ($item in @($regionPublicIps.data)) {
            Write-Line "PUBLIC_IP_REGION_RESERVED ip=$($item.'ip-address') lifecycle=$($item.'lifecycle-state')"
        }
    }
    Write-Line "LIST_PUBLIC_IPS_REGION_RESERVED_END=true"

    if ($availabilityDomain) {
        Write-Line "LIST_PUBLIC_IPS_AD_EPHEMERAL_BEGIN=true"
        $adPublicIps = Invoke-OciJson @(
            "network", "public-ip", "list",
            "--compartment-id", $compartmentId,
            "--scope", "AVAILABILITY_DOMAIN",
            "--availability-domain", $availabilityDomain,
            "--lifetime", "EPHEMERAL",
            "--all"
        )
        if ($adPublicIps -and $adPublicIps.data) {
            foreach ($item in @($adPublicIps.data)) {
                Write-Line "PUBLIC_IP_AD_EPHEMERAL ip=$($item.'ip-address') lifecycle=$($item.'lifecycle-state')"
            }
        }
        Write-Line "LIST_PUBLIC_IPS_AD_EPHEMERAL_END=true"
    }
}

Write-Line ("END {0}" -f (Get-Date -Format o))
