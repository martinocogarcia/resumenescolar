param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteDir = "/opt/resumen-escolar",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-schoolnet-auto-recovery-readiness.log"
)

$ErrorActionPreference = "Stop"

function Write-Stage([string]$Message) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "o"), $Message)
}

function Invoke-SshChecked {
    param([string]$CommandText)

    $commandArgs = @(
        "-i", $KeyPath,
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        "-o", "StrictHostKeyChecking=accept-new",
        "$VmUser@$VmHost",
        $CommandText
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & ssh @commandArgs
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "ssh de verificacion termino con exit code $exitCode."
    }
}

& {
    "START $(Get-Date -Format o)"
    Write-Stage "[1/4] Conectando a la VM para una prueba no invasiva"
    $remoteCommand = @"
set -euo pipefail
APP_DIR='$RemoteDir'
echo '[2/4] Verificando Chromium/CDP sin cerrar procesos ni borrar locks'
"`$APP_DIR/scripts/ensure_chromium_cdp.sh"
echo '[3/4] Verificando acceso al secreto SchoolNet sin imprimirlo'
"`$APP_DIR/scripts/test_schoolnet_vault_secret.sh"
echo '[4/4] Verificando que el cron use el runner diario'
crontab -l | grep -F "`$APP_DIR/scripts/run_daily_report.sh"
"@
    Invoke-SshChecked $remoteCommand
    "END $(Get-Date -Format o)"
    "exit=0"
} 2>&1 | Tee-Object -FilePath $LogPath
