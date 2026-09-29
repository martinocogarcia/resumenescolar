param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteDir = "/opt/resumen-escolar",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-schoolnet-auto-relogin-dry-run.log"
)

$ErrorActionPreference = "Stop"

function Invoke-SshChecked {
    param([string]$CommandText)
    $commandArgs = @("-i", $KeyPath, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "$VmUser@$VmHost", $CommandText)
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & ssh @commandArgs
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) { throw "ssh de prueba termino con exit code $exitCode." }
}

& {
    "START $(Get-Date -Format o)"
    Write-Host "[1/6] Conectando a la VM (timeout 15 s)"
    $remoteCommand = @"
set -euo pipefail
APP_DIR='$RemoteDir'
set -a
source "`$APP_DIR/config/automation.env"
set +a
export PYTHONPATH="`$APP_DIR:`$APP_DIR/.runtime/site-packages:`${PYTHONPATH:-}"
cd "`$APP_DIR"
echo '[2/6] Verificando Chromium/CDP seguro'
"`$APP_DIR/scripts/ensure_chromium_cdp.sh"
echo '[3/6] Ejecutando extraccion SchoolNet sin publicar ni notificar'
RESULT="`$(mktemp)"
set +e
"`$RESUMEN_ESCOLAR_PYTHON" -u -m resumen_escolar.automation run >"`$RESULT" 2>&1
CODE="`$?"
set -e
echo '[4/6] Resumen tecnico de la extraccion'
grep -Ei '"ok"|needs_login|re-login automatico|schoolnet redirigio|lectura estructurada|error' "`$RESULT" | tail -n 80 || true
rm -f "`$RESULT"
echo '[5/6] Verificando cron con puerta horaria Chile'
crontab -l | grep -F 'RESUMEN_ESCOLAR_CRON_GATE=1'
echo "[6/6] dry_run_exit=`$CODE"
exit "`$CODE"
"@
    Invoke-SshChecked $remoteCommand
    "END $(Get-Date -Format o)"
    "exit=0"
} 2>&1 | Tee-Object -FilePath $LogPath
