param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteDir = "/opt/resumen-escolar",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-schoolnet-auto-relogin-background.log"
)

$ErrorActionPreference = "Stop"

function Invoke-SshChecked {
    param([string]$CommandText)
    $commandArgs = @("-i", $KeyPath, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "$VmUser@$VmHost", $CommandText)
    $prior = $ErrorActionPreference
    try { $ErrorActionPreference = "Continue"; & ssh @commandArgs; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prior }
    if ($code -ne 0) { throw "ssh termino con exit code $code." }
}

& {
    "START $(Get-Date -Format o)"
    $remoteScript = @"
set -euo pipefail
APP_DIR='$RemoteDir'
RUN_DIR="`$APP_DIR/.runtime/schoolnet-auto-relogin-test"
mkdir -p "`$RUN_DIR"
set -a
source "`$APP_DIR/config/automation.env"
set +a
export PYTHONPATH="`$APP_DIR:`$APP_DIR/.runtime/site-packages:`${PYTHONPATH:-}"
echo '[2/8] Verificando Chromium/CDP seguro'
"`$APP_DIR/scripts/ensure_chromium_cdp.sh"
echo '[3/8] Ejecutando SchoolNet sin publicar ni notificar'
RESULT="`$RUN_DIR/result.txt"
set +e
"`$RESUMEN_ESCOLAR_PYTHON" -u -m resumen_escolar.automation run >"`$RESULT" 2>&1
CODE="`$?"
set -e
echo '[4/8] Resumen tecnico'
grep -Ei '"ok"|needs_login|re-login automatico|schoolnet redirigio|lectura estructurada|error' "`$RESULT" | tail -n 80 || true
rm -f "`$RESULT"
echo "DONE exit=`$CODE"
echo "`$CODE" > "`$RUN_DIR/status"
exit "`$CODE"
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteScript))
    $runDir = "$RemoteDir/.runtime/schoolnet-auto-relogin-test"
    Write-Host "[1/8] Iniciando prueba remota en segundo plano"
    Invoke-SshChecked "mkdir -p '$runDir'; rm -f '$runDir/status'; echo '$encoded' | base64 -d > '$runDir/run.sh'; chmod 700 '$runDir/run.sh'; nohup bash '$runDir/run.sh' > '$runDir/run.log' 2>&1 < /dev/null & echo `$! > '$runDir/pid'; echo STARTED"
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        Start-Sleep -Seconds 15
        Write-Host "[5/8] Consultando resultado remoto ($attempt/20)"
        $state = "if [ -f '$runDir/status' ]; then echo STATUS=`$(cat '$runDir/status'); tail -n 100 '$runDir/run.log'; else echo STATUS=running; tail -n 12 '$runDir/run.log' 2>/dev/null || true; fi"
        $output = & ssh -i $KeyPath -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "$VmUser@$VmHost" $state 2>&1
        $output | ForEach-Object { Write-Output $_ }
        $statusMatch = [regex]::Match(([string]::Join("`n", @($output))), 'STATUS=(\d+)')
        if ($statusMatch.Success) {
            $resultCode = [int]$statusMatch.Groups[1].Value
            Write-Host "[6/8] Prueba terminada con exit=$resultCode"
            if ($resultCode -ne 0) { throw "La prueba remota termino con exit code $resultCode." }
            break
        }
        if ($attempt -eq 20) { throw "La prueba sigue activa despues de 5 minutos; revisar $runDir/run.log." }
    }
    Write-Host "[7/8] Validando cron horario Chile"
    Invoke-SshChecked "crontab -l | grep -F 'RESUMEN_ESCOLAR_CRON_GATE=1'"
    Write-Host "[8/8] Validacion terminada"
    "END $(Get-Date -Format o)"
    "exit=0"
} 2>&1 | Tee-Object -FilePath $LogPath
