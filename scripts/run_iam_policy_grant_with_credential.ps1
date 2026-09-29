param(
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-iam-policy-grant.log"
)

$ErrorActionPreference = "Stop"

$grantScript = Join-Path $PSScriptRoot "grant_vm_object_storage_policy.ps1"
$cred = Get-Credential -UserName "oci-key" -Message "Ingresa la passphrase de la llave OCI en Password"

if (-not $cred) {
    throw "No se recibio credencial."
}

$passphrase = $cred.GetNetworkCredential().Password
if ([string]::IsNullOrEmpty($passphrase)) {
    throw "La passphrase OCI esta vacia."
}

try {
    $env:OCI_CLI_PASSPHRASE = $passphrase
    & $grantScript -LogPath $LogPath
    exit $LASTEXITCODE
} finally {
    Remove-Item Env:\OCI_CLI_PASSPHRASE -ErrorAction SilentlyContinue
}
