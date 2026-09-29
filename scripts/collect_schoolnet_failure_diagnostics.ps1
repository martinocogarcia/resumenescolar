param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteDir = "/opt/resumen-escolar",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-schoolnet-failure-diagnostics.log"
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
        throw "ssh de diagnostico termino con exit code $exitCode."
    }
}

& {
    "START $(Get-Date -Format o)"
    Write-Stage "[1/8] Resolviendo y conectando por SSH (timeout 15 s)"
    $remoteScript = @"
set -euo pipefail
APP_DIR='$RemoteDir'
LOG_DIR="`$APP_DIR/logs"
echo '[2/8] Localizando el ultimo log diario'
LATEST_LOG="`$(ls -1t "`$LOG_DIR"/daily-report-*.log 2>/dev/null | head -n 1 || true)"
test -n "`$LATEST_LOG"
echo "LATEST_LOG=`$LATEST_LOG"
echo '[3/8] Extrayendo solo eventos tecnicos relevantes'
grep -Ei -C 3 'needs_login|no pude construir|schoolnet|login|cdp|singleton|traceback|error|exception|selector|table|calificaciones' "`$LATEST_LOG" | tail -n 260 || true
echo '[4/8] Verificando estado CDP sin acceder al perfil'
if curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:9222/json/version | grep -q '"Browser"'; then
  echo 'CDP_BROWSER_PRESENT=true'
else
  echo 'CDP_BROWSER_PRESENT=false'
  exit 4
fi
echo '[5/8] Verificando acceso al secreto sin imprimirlo'
"`$APP_DIR/scripts/test_schoolnet_vault_secret.sh"
echo '[6/8] Verificando configuracion del cron'
crontab -l | grep -F "`$APP_DIR/scripts/run_daily_report.sh"
echo '[7/8] Listando artefactos diagnosticos sin abrirlos'
find "`$APP_DIR" -maxdepth 2 -type d -name 'diagnostic_bundle_*' -printf '%f\n' | tail -n 8 || true
echo '[8/9] Verificando proteccion de sesion manual y procesos Chromium'
if test -d "`$APP_DIR/.runtime/login-session"; then
  echo 'NOVNC_SESSION_MARKER=true'
else
  echo 'NOVNC_SESSION_MARKER=false'
fi
ps -eo pid=,etime=,args= | grep -E '[c]hromium.*remote-debugging-port=9222' | sed -E 's#--user-data-dir=[^ ]+#--user-data-dir=[REDACTED]#g' || true
echo '[9/9] Diagnostico remoto terminado'
"@
    # Nunca pasar grep, pipes o comillas complejas como argumento SSH directo:
    # enviamos el script completo codificado para que Bash lo interprete intacto.
    $encodedScript = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($remoteScript))
    $remoteCommand = "echo '$encodedScript' | base64 -d | bash"
    Invoke-SshChecked $remoteCommand
    "END $(Get-Date -Format o)"
    "exit=0"
} 2>&1 | Tee-Object -FilePath $LogPath
