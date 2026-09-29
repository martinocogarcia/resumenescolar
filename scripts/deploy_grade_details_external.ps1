param(
    [string]$VmHost = '129.153.51.108',
    [string]$VmUser = 'ubuntu',
    [string]$KeyPath = 'C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511',
    [int]$DailyTimeoutSeconds = 2400
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DeployLog = Join-Path $ProjectRoot 'codex-grade-details-deploy-internal.log'
$DailyLog = Join-Path $ProjectRoot 'codex-grade-details-daily-internal.log'
$DailyProcessLog = Join-Path $ProjectRoot 'codex-grade-details-daily-process.log'
$Remote = "${VmUser}@${VmHost}"
$SshOptions = @(
    '-i', $KeyPath,
    '-o', 'IdentitiesOnly=yes',
    '-o', 'BatchMode=yes',
    '-o', 'ConnectTimeout=15',
    '-o', 'ServerAliveInterval=10',
    '-o', 'ServerAliveCountMax=2',
    '-o', "UserKnownHostsFile=$env:TEMP\codex_known_hosts_resumen_escolar",
    '-o', 'StrictHostKeyChecking=accept-new'
)

$script:PreviousStageNumber = 0
$script:PreviousStageStart = $null

function Stage {
    param([int]$Number, [string]$Description)
    $now = Get-Date
    if ($script:PreviousStageStart) {
        $elapsed = [Math]::Round(($now - $script:PreviousStageStart).TotalSeconds, 1)
        Write-Host ('[{0}/11] FIN | duration_seconds={1}' -f $script:PreviousStageNumber, $elapsed)
    }
    Write-Host ('[{0}/11] INICIO {1} | {2}' -f $Number, $now.ToString('HH:mm:ss'), $Description)
    $script:PreviousStageNumber = $Number
    $script:PreviousStageStart = $now
}

function Invoke-RemoteBash {
    param([string]$Script)
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script))
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & ssh.exe @SshOptions $Remote "echo $encoded | base64 -d | bash" 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { throw "La comprobacion remota fallo (exit=$code)." }
    return @($output)
}

try {
    Stage 1 'Comprobando proyecto, script y llave local'
    $deployScript = Join-Path $PSScriptRoot 'deploy_to_oracle_form_vm.ps1'
    $dailyScript = Join-Path $PSScriptRoot 'run_vm_daily_cron_now.ps1'
    foreach ($path in @($deployScript, $dailyScript, $KeyPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Falta un archivo necesario: $path"
        }
    }

    Stage 2 'Destino de VM identificado desde la configuracion del proyecto'
    Stage 3 'Comprobando TCP 22'
    if (-not (Test-NetConnection $VmHost -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
        throw 'La VM no responde en TCP 22. Se detiene antes de empaquetar.'
    }

    Stage 4 'Comprobando autenticacion SSH'
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $auth = & ssh.exe @SshOptions $Remote 'printf SSH_AUTH_OK' 2>&1
        $authCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($authCode -ne 0 -or ($auth -join '') -notmatch 'SSH_AUTH_OK') {
        throw "La llave no autentico en la VM (exit=$authCode). No se subio ningun archivo."
    }

    Stage 5 'Empaquetando y desplegando mediante el script oficial'
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $deployScript -KeyPath $KeyPath -LogPath $DeployLog 2>&1 |
            ForEach-Object { Add-Content -LiteralPath $DeployLog -Value ([string]$_) }
        $deployExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($deployExit -ne 0 -or -not (Select-String -LiteralPath $DeployLog -Pattern '^deploy-ok$' -Quiet)) {
        throw "El despliegue fallo. Revisa el log interno de deploy (exit=$deployExit)."
    }

    Stage 6 'Confirmando respaldo automatico y configuracion preservada'
    if (-not (Select-String -LiteralPath $DeployLog -Pattern '^rollback-created=true$' -Quiet)) {
        throw 'El despliegue no confirmo el respaldo de codigo anterior.'
    }
    if (-not (Select-String -LiteralPath $DeployLog -Pattern '^config-preserved=true' -Quiet)) {
        throw 'El despliegue no confirmo que preservo la configuracion privada.'
    }

    Stage 7 'Comparando el codigo local con el instalado en la VM'
    $localApp = (Get-FileHash -LiteralPath (Join-Path $ProjectRoot 'resumen_escolar\app.py') -Algorithm SHA256).Hash.ToLowerInvariant()
    $localReport = (Get-FileHash -LiteralPath (Join-Path $ProjectRoot 'resumen_escolar\daily_report.py') -Algorithm SHA256).Hash.ToLowerInvariant()
    $remoteHashes = Invoke-RemoteBash 'sha256sum /opt/resumen-escolar/resumen_escolar/app.py /opt/resumen-escolar/resumen_escolar/daily_report.py'
    $remoteText = $remoteHashes -join "`n"
    if ($remoteText -notmatch [regex]::Escape($localApp) -or $remoteText -notmatch [regex]::Escape($localReport)) {
        throw 'Los hashes del codigo instalado no coinciden con el codigo local.'
    }
    Write-Host 'CODIGO_REMOTO_COINCIDE=true'

    Stage 8 'Confirmando que el cron diario sigue instalado'
    $cron = Invoke-RemoteBash 'set -euo pipefail; crontab -l | grep -q run_daily_report.sh; printf CRON_OK'
    if (($cron -join '') -notmatch 'CRON_OK') { throw 'No se encontro el cron diario en la VM.' }

    Stage 9 'Ejecutando el reporte diario para comprobar la extraccion y publicacion'
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $dailyScript -KeyPath $KeyPath -MonitorTimeoutSeconds $DailyTimeoutSeconds -LogPath $DailyLog 2>&1 |
            ForEach-Object {
                $line = [string]$_
                Add-Content -LiteralPath $DailyProcessLog -Value $line
                if ($line -match '^\[\d+/12\]' -or $line -match '^RESULTADO:' -or $line -match '^FIN duration') {
                    Write-Host $line
                }
            }
        $dailyExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($dailyExit -ne 0) { throw "El reporte diario no confirmo publicacion exitosa (exit=$dailyExit)." }

    Stage 10 'Validando conteo de notas en el JSON de la VM sin mostrar datos personales'
    $verify = @'
set -euo pipefail
app=/opt/resumen-escolar
date_chile=$(TZ=America/Santiago date +%F)
state="$app/outbox/$date_chile/daily_report_state.json"
test -f "$state"
"$app/.venv/bin/python" - "$state" <<'PY'
import json
import sys

with open(sys.argv[1], encoding='utf-8') as stream:
    state = json.load(stream)
grades = state.get('grades') or []
actual = sum(len(row.get('assessments') or []) for row in grades)
summary = state.get('grade_details') or {}
print('JSON_VERSION=' + str(state.get('version')))
print('JSON_SUBJECTS=' + str(len(grades)))
print('JSON_ASSESSMENTS=' + str(actual))
print('JSON_COUNT_MATCH=' + str(actual == summary.get('assessment_count')).lower())
if state.get('version', 0) < 2 or not grades or not actual or actual != summary.get('assessment_count'):
    raise SystemExit(2)
PY
'@
    $verification = Invoke-RemoteBash $verify
    $verification | ForEach-Object { Write-Host ([string]$_) }

    Stage 11 'Despliegue y validacion terminados'
    Write-Host 'RESULTADO: OK'
    $elapsed = [Math]::Round(((Get-Date) - $script:PreviousStageStart).TotalSeconds, 1)
    Write-Host ('[11/11] FIN | duration_seconds={0}' -f $elapsed)
} catch {
    Write-Host ('RESULTADO: FALLO | ' + $_.Exception.Message)
    Write-Host ('LOG_DEPLOY=' + $DeployLog)
    Write-Host ('LOG_DAILY=' + $DailyLog)
    exit 1
}
