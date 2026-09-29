param(
    [string]$PublicIp = "129.153.51.108",
    [string]$OciProfile = "",
    [string]$OciRegion = "",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-oci-public-ip-target.log"
)

$ErrorActionPreference = "Continue"

function Write-Redacted([string]$Value) {
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
        Write-Redacted ("OCI_ERROR command=oci {0} {1}" -f ($globalArgs -join " "), ($ArgsList -join " "))
        $raw | ForEach-Object { Write-Redacted ([string]$_) }
        return $null
    }

    try {
        return ($raw | Out-String | ConvertFrom-Json)
    } catch {
        Write-Redacted ("OCI_JSON_PARSE_ERROR command=oci {0} error={1}" -f ($ArgsList -join " "), $_.Exception.Message)
        return $null
    }
}

& {
    Write-Redacted ("START {0}" -f (Get-Date -Format o))
    Write-Redacted "PUBLIC_IP=$PublicIp"
    Write-Redacted "OCI_PROFILE=$OciProfile"
    Write-Redacted "OCI_REGION=$OciRegion"

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

    Write-Redacted "STAGE=public_ip_get"
    $publicIpResult = Invoke-OciJson @("network", "public-ip", "get", "--public-ip-address", $PublicIp)
    if ($publicIpResult -and $publicIpResult.data) {
        $data = $publicIpResult.data
        Write-Redacted "PUBLIC_IP_RESOURCE_FOUND=true"
        Write-Redacted "PUBLIC_IP_LIFETIME=$($data.lifetime)"
        Write-Redacted "PUBLIC_IP_SCOPE=$($data.scope)"
        Write-Redacted "PUBLIC_IP_LIFECYCLE=$($data.'lifecycle-state')"
        if ($data.'private-ip-id') {
            Write-Redacted "PUBLIC_IP_HAS_PRIVATE_IP_ID=true"
            $privateIp = Invoke-OciJson @("network", "private-ip", "get", "--private-ip-id", $data.'private-ip-id')
            if ($privateIp -and $privateIp.data) {
                Write-Redacted "PRIVATE_IP_ADDRESS=$($privateIp.data.'ip-address')"
                Write-Redacted "PRIVATE_IP_DISPLAY_NAME=$($privateIp.data.'display-name')"
                if ($privateIp.data.'vnic-id') {
                    $vnic = Invoke-OciJson @("network", "vnic", "get", "--vnic-id", $privateIp.data.'vnic-id')
                    if ($vnic -and $vnic.data) {
                        Write-Redacted "VNIC_DISPLAY_NAME=$($vnic.data.'display-name')"
                        Write-Redacted "VNIC_PUBLIC_IP=$($vnic.data.'public-ip')"
                        Write-Redacted "VNIC_PRIVATE_IP=$($vnic.data.'private-ip')"
                        Write-Redacted "VNIC_NSG_COUNT=$(@($vnic.data.'nsg-ids').Count)"
                        Write-Redacted "VNIC_SUBNET_PRESENT=$([bool]$vnic.data.'subnet-id')"
                    }
                }
            }
        } else {
            Write-Redacted "PUBLIC_IP_HAS_PRIVATE_IP_ID=false"
        }
    } else {
        Write-Redacted "PUBLIC_IP_RESOURCE_FOUND=false"
    }

    Write-Redacted "STAGE=free_text_search"
    $search = Invoke-OciJson @("search", "resource", "free-text-search", "--text", $PublicIp)
    if ($search -and $search.data) {
        $items = @($search.data.items)
        Write-Redacted "SEARCH_ITEM_COUNT=$($items.Count)"
        foreach ($item in ($items | Select-Object -First 20)) {
            Write-Redacted "SEARCH_ITEM type=$($item.'resource-type') name=$($item.'display-name') lifecycle=$($item.'lifecycle-state')"
        }
    } else {
        Write-Redacted "SEARCH_ITEM_COUNT=0"
    }

    Write-Redacted ("END {0}" -f (Get-Date -Format o))
} *> $LogPath

exit $LASTEXITCODE
