param(
    [string]$OciProfile = "",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

if (-not $LogPath) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
    $LogPath = Join-Path $ProjectRoot "codex-oci-config-key-diagnosis.log"
}

function Redact-Ocid([string]$Value) {
    if ($null -eq $Value) { return "" }
    return ($Value -replace "ocid1\.[A-Za-z0-9._-]+", "[OCID_REDACTED]")
}

function Add-Log([string]$Text) {
    Add-Content -LiteralPath $LogPath -Value (Redact-Ocid $Text)
}

function Get-ProfileMap {
    $configPath = Join-Path $env:USERPROFILE ".oci\config"
    if (-not (Test-Path -LiteralPath $configPath)) {
        throw "No existe OCI config en $configPath"
    }
    $profiles = @{}
    $current = ""
    foreach ($line in Get-Content -LiteralPath $configPath) {
        $trimmed = $line.Trim()
        if ($trimmed -match "^\[(.+)\]$") {
            $current = $Matches[1]
            $profiles[$current] = @{}
            continue
        }
        if (-not $current -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        $profiles[$current][$parts[0].Trim()] = $parts[1].Trim()
    }
    return $profiles
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
Set-Content -LiteralPath $LogPath -Value "START $(Get-Date -Format o)"

Write-Host "[1/3] Leyendo config OCI"
$profiles = Get-ProfileMap
$profileName = if ($OciProfile) { $OciProfile } else { "DEFAULT" }
Add-Log "PROFILES=$(@($profiles.Keys) -join ',')"
Add-Log "SELECTED_PROFILE=$profileName"

if (-not $profiles.ContainsKey($profileName)) {
    throw "No existe el perfil OCI '$profileName'."
}

$profile = $profiles[$profileName]
$keyFile = $profile["key_file"]
$region = $profile["region"]
$tenancy = $profile["tenancy"]
$user = $profile["user"]
$fingerprint = $profile["fingerprint"]
$passPhrase = $profile["pass_phrase"]

Add-Log "REGION=$region"
Add-Log "TENANCY=$tenancy"
Add-Log "USER=$user"
Add-Log "FINGERPRINT_PRESENT=$([int](-not [string]::IsNullOrWhiteSpace($fingerprint)))"
Add-Log "PASS_PHRASE_FIELD_PRESENT=$([int]($profile.ContainsKey('pass_phrase')))"
Add-Log "PASS_PHRASE_FIELD_EMPTY=$([int]([string]::IsNullOrWhiteSpace($passPhrase)))"

Write-Host "[2/3] Revisando archivo de llave"
if (-not $keyFile) {
    throw "El perfil no tiene key_file."
}
$expandedKeyFile = [Environment]::ExpandEnvironmentVariables($keyFile)
if ($expandedKeyFile.StartsWith("~")) {
    $expandedKeyFile = Join-Path $env:USERPROFILE $expandedKeyFile.Substring(1).TrimStart("\", "/")
}
Add-Log "KEY_FILE_BASENAME=$(Split-Path -Leaf $expandedKeyFile)"
Add-Log "KEY_FILE_EXISTS=$([int](Test-Path -LiteralPath $expandedKeyFile))"
if (-not (Test-Path -LiteralPath $expandedKeyFile)) {
    throw "No existe el key_file configurado."
}

$keyHead = Get-Content -LiteralPath $expandedKeyFile -TotalCount 5
$firstLine = ($keyHead | Select-Object -First 1)
$isEncrypted = (
    ($keyHead -join "`n") -match "ENCRYPTED" -or
    ($keyHead -join "`n") -match "Proc-Type:\s*4,ENCRYPTED"
)
Add-Log "KEY_FIRST_LINE=$firstLine"
Add-Log "KEY_LOOKS_ENCRYPTED=$([int]$isEncrypted)"

Write-Host "[3/3] Probando OCI CLI sin passphrase"
$env:OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING = "True"
$ociArgs = @()
if ($OciProfile) { $ociArgs += @("--profile", $OciProfile) }
if ($region) { $ociArgs += @("--region", $region) }
$ociArgs += @("iam", "region", "list", "--query", "data[0].name", "--raw-output")
$output = & oci @ociArgs 2>&1
$exit = $LASTEXITCODE
Add-Log "OCI_TEST_EXIT=$exit"
Add-Log (($output | Out-String).Trim())

Add-Log "END $(Get-Date -Format o)"
Write-Host "Listo. Log: $LogPath"
