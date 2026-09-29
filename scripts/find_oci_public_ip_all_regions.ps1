param(
    [string]$PublicIp = "129.153.51.108",
    [string]$OciProfile = "",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-oci-public-ip-all-regions.log"
)

$ErrorActionPreference = "Continue"

function Write-Redacted([string]$Value) {
    $Value = $Value -replace 'ocid1\.[A-Za-z0-9._-]+', '[OCID_REDACTED]'
    $Value = $Value -replace '[A-Fa-f0-9]{2}(:[A-Fa-f0-9]{2}){15,}', '[FINGERPRINT_REDACTED]'
    $Value = $Value -replace 'C:\\Users\\Martin\\[^\s\r\n]+', '[PATH_REDACTED]'
    Write-Output $Value
}

function Invoke-OciJson([string[]]$ArgsList, [string]$Region = "") {
    $globalArgs = @()
    if ($OciProfile) {
        $globalArgs += @("--profile", $OciProfile)
    }
    if ($Region) {
        $globalArgs += @("--region", $Region)
    }
    $globalArgs += @("--connection-timeout", "8", "--read-timeout", "15", "--no-retry")

    $raw = & oci @globalArgs @ArgsList 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Redacted ("OCI_ERROR region={0} command=oci {1} {2}" -f $Region, ($globalArgs -join " "), ($ArgsList -join " "))
        $raw | Select-Object -First 8 | ForEach-Object { Write-Redacted ([string]$_) }
        return $null
    }

    try {
        return ($raw | Out-String | ConvertFrom-Json)
    } catch {
        Write-Redacted ("OCI_JSON_PARSE_ERROR region={0} command=oci {1} error={2}" -f $Region, ($ArgsList -join " "), $_.Exception.Message)
        return $null
    }
}

function Get-SubscribedRegions {
    $result = Invoke-OciJson @("iam", "region-subscription", "list")
    if ($result -and $result.data) {
        return @($result.data | ForEach-Object { $_.'region-name' } | Where-Object { $_ } | Sort-Object -Unique)
    }

    return @(
        "ca-toronto-1",
        "us-ashburn-1",
        "us-phoenix-1",
        "sa-santiago-1",
        "sa-saopaulo-1",
        "sa-vinhedo-1"
    )
}

& {
    Write-Redacted ("START {0}" -f (Get-Date -Format o))
    Write-Redacted "PUBLIC_IP=$PublicIp"
    Write-Redacted "OCI_PROFILE=$OciProfile"

    if (-not $env:OCI_CLI_KEY_PASSPHRASE) {
        $secure = Read-Host "OCI key passphrase (Enter si no aplica; no se guarda ni se imprime)" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $passphrase = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            if ($passphrase) {
                $env:OCI_CLI_KEY_PASSPHRASE = $passphrase
                $env:OCI_CLI_PASSPHRASE = $passphrase
                $env:OCI_CLI_PASS_PHRASE = $passphrase
            }
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }

    Write-Redacted ("OCI_VERSION={0}" -f (& oci --version 2>&1))
    $regions = @(Get-SubscribedRegions)
    Write-Redacted "REGION_COUNT=$($regions.Count)"

    foreach ($region in $regions) {
        Write-Redacted "STAGE=public_ip_get region=$region"
        $publicIpResult = Invoke-OciJson @("network", "public-ip", "get", "--public-ip-address", $PublicIp) $region
        if (-not $publicIpResult -or -not $publicIpResult.data) {
            Write-Redacted "PUBLIC_IP_FOUND region=$region false"
            continue
        }

        $data = $publicIpResult.data
        Write-Redacted "PUBLIC_IP_FOUND region=$region true"
        Write-Redacted "PUBLIC_IP_LIFETIME=$($data.lifetime)"
        Write-Redacted "PUBLIC_IP_SCOPE=$($data.scope)"
        Write-Redacted "PUBLIC_IP_LIFECYCLE=$($data.'lifecycle-state')"
        Write-Redacted "PRIVATE_IP_ID_PRESENT=$([bool]$data.'private-ip-id')"

        if ($data.'private-ip-id') {
            $privateIp = Invoke-OciJson @("network", "private-ip", "get", "--private-ip-id", $data.'private-ip-id') $region
            if ($privateIp -and $privateIp.data) {
                Write-Redacted "PRIVATE_IP_ADDRESS=$($privateIp.data.'ip-address')"
                Write-Redacted "PRIVATE_IP_DISPLAY_NAME=$($privateIp.data.'display-name')"
                if ($privateIp.data.'vnic-id') {
                    $vnic = Invoke-OciJson @("network", "vnic", "get", "--vnic-id", $privateIp.data.'vnic-id') $region
                    if ($vnic -and $vnic.data) {
                        Write-Redacted "VNIC_DISPLAY_NAME=$($vnic.data.'display-name')"
                        Write-Redacted "VNIC_PUBLIC_IP=$($vnic.data.'public-ip')"
                        Write-Redacted "VNIC_PRIVATE_IP=$($vnic.data.'private-ip')"
                        Write-Redacted "VNIC_NSG_COUNT=$(@($vnic.data.'nsg-ids').Count)"
                        Write-Redacted "VNIC_SUBNET_PRESENT=$([bool]$vnic.data.'subnet-id')"
                    }
                }
            }
        }

        Write-Redacted "MATCH_REGION=$region"
        Write-Redacted ("END {0}" -f (Get-Date -Format o))
        exit 0
    }

    Write-Redacted "PUBLIC_IP_NOT_FOUND_IN_CHECKED_REGIONS=true"
    Write-Redacted ("END {0}" -f (Get-Date -Format o))
    exit 2
} *> $LogPath

exit $LASTEXITCODE
