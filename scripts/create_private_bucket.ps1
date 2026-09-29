param(
    [string]$BucketName = "resumen-escolar-gabitin",
    [string]$VmName = "oracle-form-app-vm",
    [string]$OciProfile = "OCI",
    [string]$OciRegion = "ca-toronto-1",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-bucket-create.log"
)

$ErrorActionPreference = "Continue"

function Write-Redacted([string]$Value) {
    $Value = $Value -replace 'ocid1\.[A-Za-z0-9._-]+', '[OCID_REDACTED]'
    $Value = $Value -replace '[A-Fa-f0-9]{2}(:[A-Fa-f0-9]{2}){15,}', '[FINGERPRINT_REDACTED]'
    $Value = $Value -replace 'C:\\Users\\Martin\\[^\s\r\n]+', '[PATH_REDACTED]'
    Write-Output $Value
}

function Invoke-OciJson([string[]]$ArgsList, [switch]$AllowFailure) {
    Write-Redacted ("OCI_STEP command=oci --profile {0} --region {1} {2}" -f $OciProfile, $OciRegion, ($ArgsList -join " "))
    $raw = & oci --profile $OciProfile --region $OciRegion @ArgsList 2>&1
    $exitCode = $LASTEXITCODE
    Write-Redacted ("OCI_STEP_EXIT={0}" -f $exitCode)
    if ($LASTEXITCODE -ne 0) {
        Write-Redacted ("OCI_ERROR command=oci --profile {0} --region {1} {2}" -f $OciProfile, $OciRegion, ($ArgsList -join " "))
        $raw | ForEach-Object { Write-Redacted ([string]$_) }
        if ($AllowFailure) { return $null }
        return $null
    }
    try {
        return ($raw | Out-String | ConvertFrom-Json)
    } catch {
        Write-Redacted ("OCI_JSON_ERROR={0}" -f $_.Exception.Message)
        $raw | ForEach-Object { Write-Redacted ([string]$_) }
        if ($AllowFailure) { return $null }
        return $null
    }
}

& {
    "START $(Get-Date -Format o)"
    "OCI_PROFILE=$OciProfile"
    "OCI_REGION=$OciRegion"
    "BUCKET_NAME=$BucketName"
    "VM_NAME=$VmName"

    if (-not $env:OCI_CLI_PASSPHRASE) {
        $secure = Read-Host "OCI key passphrase (no se guarda ni se imprime)" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $env:OCI_CLI_PASSPHRASE = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }

    "OCI_VERSION=$(& oci --version 2>&1)"
    $ns = Invoke-OciJson @("os", "ns", "get")
    if (-not $ns) {
        "STOP namespace_failed"
        "exit=1"
        exit 1
    }
    "NAMESPACE_OK=$([bool]$ns)"

    $search = Invoke-OciJson @(
        "search", "resource", "structured-search",
        "--query-text", "query instance resources where displayName = '$VmName'"
    )
    if (-not $search) {
        "STOP vm_search_failed"
        "exit=1"
        exit 1
    }
    $items = @($search.data.items)
    if ($items.Count -eq 0) {
        "VM_NOT_FOUND=$VmName"
        exit 2
    }
    $compartmentId = ($items | Select-Object -First 1).'compartment-id'
    "VM_COMPARTMENT_FOUND=true"

    $existing = Invoke-OciJson @("os", "bucket", "get", "--name", $BucketName) -AllowFailure
    if ($existing -and $existing.data) {
        "BUCKET_EXISTS=true"
    } else {
        $created = Invoke-OciJson @(
            "os", "bucket", "create",
            "--compartment-id", $compartmentId,
            "--name", $BucketName,
            "--public-access-type", "NoPublicAccess",
            "--storage-tier", "Standard"
        )
        if (-not $created) {
            "STOP bucket_create_failed"
            "exit=1"
            exit 1
        }
        "BUCKET_CREATED=true"
    }

    $bucket = Invoke-OciJson @("os", "bucket", "get", "--name", $BucketName)
    if (-not $bucket) {
        "STOP bucket_get_failed"
        "exit=1"
        exit 1
    }
    "BUCKET_PRIVATE_PUBLIC_ACCESS_TYPE=$($bucket.data.'public-access-type')"
    "END $(Get-Date -Format o)"
    "exit=0"
} *> $LogPath

Remove-Item Env:\OCI_CLI_PASSPHRASE -ErrorAction SilentlyContinue
