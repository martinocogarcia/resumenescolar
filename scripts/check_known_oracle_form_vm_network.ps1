param(
    [string]$InstanceId = "ocid1.instance.oc1.ca-toronto-1.an2g6ljrooq74kqckwx6ktylj4cvnhkeszdkc6kcofp4x47zav2nw6t7zvsa",
    [string]$VmPublicIp = "129.153.51.108",
    [string]$OciProfile = "",
    [string]$OciRegion = "ca-toronto-1",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-known-vm-network.log"
)

$ErrorActionPreference = "Continue"

function Write-Redacted([string]$Value) {
    $Value = $Value -replace 'ocid1\.[A-Za-z0-9._-]+', '[OCID_REDACTED]'
    $Value = $Value -replace '[A-Fa-f0-9]{2}(:[A-Fa-f0-9]{2}){15,}', '[FINGERPRINT_REDACTED]'
    $Value = $Value -replace 'C:\\Users\\Martin\\[^\s\r\n]+', '[PATH_REDACTED]'
    Write-Output $Value
}

function Invoke-OciJson([string[]]$ArgsList) {
    $globalArgs = @("--region", $OciRegion, "--connection-timeout", "8", "--read-timeout", "15", "--no-retry")
    if ($OciProfile) {
        $globalArgs += @("--profile", $OciProfile)
    }

    $raw = & oci @globalArgs @ArgsList 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Redacted ("OCI_ERROR command=oci {0} {1}" -f ($globalArgs -join " "), ($ArgsList -join " "))
        $raw | Select-Object -First 12 | ForEach-Object { Write-Redacted ([string]$_) }
        return $null
    }

    try {
        return ($raw | Out-String | ConvertFrom-Json)
    } catch {
        Write-Redacted ("OCI_JSON_PARSE_ERROR command=oci {0} error={1}" -f ($ArgsList -join " "), $_.Exception.Message)
        return $null
    }
}

function Show-SshRulesForVnic($VnicData) {
    $nsgIds = @($VnicData.'nsg-ids') | Where-Object { $_ }
    Write-Redacted "NSG_COUNT=$($nsgIds.Count)"
    foreach ($nsgId in $nsgIds) {
        $nsg = Invoke-OciJson @("network", "nsg", "get", "--nsg-id", $nsgId)
        $nsgName = if ($nsg -and $nsg.data) { $nsg.data.'display-name' } else { "[unknown]" }
        Write-Redacted "NSG=$nsgName"

        $rules = Invoke-OciJson @("network", "nsg", "rules", "list", "--nsg-id", $nsgId, "--all")
        if (-not $rules) { continue }
        foreach ($rule in @($rules.data)) {
            $min = $rule.'tcp-options'.'destination-port-range'.min
            $max = $rule.'tcp-options'.'destination-port-range'.max
            if ($rule.direction -eq "INGRESS" -and $rule.protocol -eq "6" -and $min -le 22 -and $max -ge 22) {
                Write-Redacted "NSG_SSH_RULE source=$($rule.source) stateless=$($rule.'is-stateless')"
            }
        }
    }

    if (-not $VnicData.'subnet-id') {
        Write-Redacted "SUBNET_ID_PRESENT=false"
        return
    }
    $subnet = Invoke-OciJson @("network", "subnet", "get", "--subnet-id", $VnicData.'subnet-id')
    if (-not $subnet -or -not $subnet.data) { return }
    foreach ($slId in @($subnet.data.'security-list-ids')) {
        $sl = Invoke-OciJson @("network", "security-list", "get", "--security-list-id", $slId)
        if (-not $sl -or -not $sl.data) { continue }
        Write-Redacted "SECURITY_LIST=$($sl.data.'display-name')"
        foreach ($rule in @($sl.data.'ingress-security-rules')) {
            $min = $rule.'tcp-options'.'destination-port-range'.min
            $max = $rule.'tcp-options'.'destination-port-range'.max
            if ($rule.protocol -eq "6" -and $min -le 22 -and $max -ge 22) {
                Write-Redacted "SECLIST_SSH_RULE source=$($rule.source) stateless=$($rule.'is-stateless')"
            }
        }
    }
}

& {
    Write-Redacted ("START {0}" -f (Get-Date -Format o))
    Write-Redacted "OCI_PROFILE=$OciProfile"
    Write-Redacted "OCI_REGION=$OciRegion"
    Write-Redacted "TARGET_PUBLIC_IP=$VmPublicIp"

    if (-not $env:OCI_CLI_PASSPHRASE -and -not $env:OCI_CLI_KEY_PASSPHRASE) {
        $secure = Read-Host "OCI key passphrase (Enter si no aplica; no se guarda ni se imprime)" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $passphrase = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            if ($passphrase) {
                $env:OCI_CLI_PASSPHRASE = $passphrase
                $env:OCI_CLI_KEY_PASSPHRASE = $passphrase
                $env:OCI_CLI_PASS_PHRASE = $passphrase
            }
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }

    Write-Redacted ("OCI_VERSION={0}" -f (& oci --version 2>&1))
    try {
        $detectedIp = (& curl.exe -4 -sS --max-time 8 https://api.ipify.org 2>&1 | Select-Object -First 1)
        Write-Redacted "CURRENT_PUBLIC_IP=$detectedIp"
    } catch {
        Write-Redacted "CURRENT_PUBLIC_IP_ERROR=$($_.Exception.Message)"
    }
    try {
        $sshOk = Test-NetConnection -ComputerName $VmPublicIp -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue
        Write-Redacted "SSH_22_TO_VM=$sshOk"
    } catch {
        Write-Redacted "SSH_22_TO_VM_ERROR=$($_.Exception.Message)"
    }

    Write-Redacted "STAGE=compute_instance_get"
    $instance = Invoke-OciJson @("compute", "instance", "get", "--instance-id", $InstanceId)
    if (-not $instance -or -not $instance.data) {
        Write-Redacted "STOP instance_get_failed"
        Write-Redacted ("END {0}" -f (Get-Date -Format o))
        exit 2
    }
    Write-Redacted "INSTANCE_DISPLAY_NAME=$($instance.data.'display-name')"
    Write-Redacted "INSTANCE_LIFECYCLE=$($instance.data.'lifecycle-state')"
    Write-Redacted "INSTANCE_AD_PRESENT=$([bool]$instance.data.'availability-domain')"
    Write-Redacted "COMPARTMENT_ID_PRESENT=$([bool]$instance.data.'compartment-id')"

    Write-Redacted "STAGE=vnic_attachment_list"
    $attachments = Invoke-OciJson @(
        "compute", "vnic-attachment", "list",
        "--compartment-id", $instance.data.'compartment-id',
        "--instance-id", $InstanceId,
        "--all"
    )
    if (-not $attachments) {
        Write-Redacted "STOP vnic_attachment_list_failed"
        Write-Redacted ("END {0}" -f (Get-Date -Format o))
        exit 2
    }
    $items = @($attachments.data)
    Write-Redacted "VNIC_ATTACHMENT_COUNT=$($items.Count)"
    foreach ($attachment in $items) {
        $vnic = Invoke-OciJson @("network", "vnic", "get", "--vnic-id", $attachment.'vnic-id')
        if (-not $vnic -or -not $vnic.data) {
            continue
        }
        $vnicData = $vnic.data
        Write-Redacted "VNIC_DISPLAY_NAME=$($vnicData.'display-name')"
        Write-Redacted "VNIC_PUBLIC_IP=$($vnicData.'public-ip')"
        Write-Redacted "VNIC_PRIVATE_IP=$($vnicData.'private-ip')"
        Write-Redacted "VNIC_SUBNET_PRESENT=$([bool]$vnicData.'subnet-id')"
        Show-SshRulesForVnic $vnicData
    }

    Write-Redacted ("END {0}" -f (Get-Date -Format o))
    exit 0
} *> $LogPath

exit $LASTEXITCODE
