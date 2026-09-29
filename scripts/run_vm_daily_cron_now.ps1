param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteDir = "/opt/resumen-escolar",
    [int]$MonitorTimeoutSeconds = 2400,
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-manual-daily-cron.log"
)

$ErrorActionPreference = "Continue"
$remote = "${VmUser}@${VmHost}"
$knownHostsPath = Join-Path $env:TEMP "codex_known_hosts_resumen_escolar"
$sshArgs = @(
    "-i", $KeyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=2",
    "-o", "UserKnownHostsFile=$knownHostsPath",
    "-o", "StrictHostKeyChecking=accept-new"
)

function Write-Stage {
    param([int]$Number, [string]$Message)
    Write-Output ("[{0}/12] {1}" -f $Number, $Message)
}

function Invoke-Remote {
    param([string]$Script, [string]$Label)
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script))
    $output = & ssh.exe @sshArgs $remote "echo $encoded | base64 -d | bash" 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Label fallo con exit=$exitCode. $($output -join ' ')"
    }
    return @($output)
}

$startedAt = Get-Date
$exitCode = 1
$transcriptStarted = $false

try {
    Start-Transcript -LiteralPath $LogPath -Append | Out-Null
    $transcriptStarted = $true

    Write-Stage 1 "Inicio: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Stage 2 "Validando llave SSH local"
    if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
        throw "No se encontro la llave SSH configurada."
    }

    Write-Stage 3 "Verificando conexion con la VM"
    $connection = & ssh.exe @sshArgs $remote "printf SSH_OK" 2>&1
    if ($LASTEXITCODE -ne 0 -or ($connection -join "") -notmatch "SSH_OK") {
        throw "No fue posible conectar por SSH a la VM."
    }

    Write-Stage 4 "Iniciando una ejecucion manual no bloqueante"
    $startScript = @"
set -euo pipefail
APP_DIR='$RemoteDir'
LOG_DIR="`$APP_DIR/logs"
LOG_FILE="`$LOG_DIR/daily-report-`$(TZ=America/Santiago date +%F).log"
mkdir -p "`$LOG_DIR"
LOG_START_LINE=`$(wc -l < "`$LOG_FILE" 2>/dev/null || echo 0)
nohup "`$APP_DIR/scripts/run_daily_report.sh" >/dev/null 2>&1 &
echo "RUN_PID=`$!"
echo "LOG_FILE=`$LOG_FILE"
echo "LOG_START_LINE=`$LOG_START_LINE"
"@
    $startOutput = Invoke-Remote $startScript "Inicio remoto"
    $pidLine = @($startOutput | Where-Object { $_ -match '^RUN_PID=\d+$' } | Select-Object -Last 1)
    $logLine = @($startOutput | Where-Object { $_ -match '^LOG_FILE=' } | Select-Object -Last 1)
    $startLine = @($startOutput | Where-Object { $_ -match '^LOG_START_LINE=\d+$' } | Select-Object -Last 1)
    if (-not $pidLine -or -not $logLine -or -not $startLine) {
        throw "La VM no entrego el PID o la posicion inicial del log."
    }
    $runPid = ($pidLine -replace '^RUN_PID=', '').Trim()
    $remoteLog = ($logLine -replace '^LOG_FILE=', '').Trim()
    $nextLogLine = [int](($startLine -replace '^LOG_START_LINE=', '').Trim()) + 1
    $runStartLine = $nextLogLine
    Write-Output "RUN_PID=$runPid"
    Write-Output "REMOTE_LOG=$remoteLog"

    Write-Stage 5 "Monitoreando el cron; recibiras una actualizacion cada 15 segundos"
    $deadline = (Get-Date).AddSeconds($MonitorTimeoutSeconds)
    $poll = 0
    $finished = $false
    $lastConsoleUpdate = 0
    do {
        Start-Sleep -Seconds 15
        $poll++
        $checkScript = @"
if kill -0 $runPid 2>/dev/null; then echo RUN_STATE=ACTIVE; else echo RUN_STATE=FINISHED; fi
if [ -f '$remoteLog' ]; then
  tail -n +$nextLogLine '$remoteLog' 2>/dev/null || true
  echo "LOG_LINE_COUNT=`$(wc -l < '$remoteLog')"
else
  echo "LOG_LINE_COUNT=0"
fi
"@
        $checkOutput = Invoke-Remote $checkScript "Consulta de estado"
        $stateLine = @($checkOutput | Where-Object { $_ -match '^RUN_STATE=' } | Select-Object -Last 1)
        $lineCountLine = @($checkOutput | Where-Object { $_ -match '^LOG_LINE_COUNT=\d+$' } | Select-Object -Last 1)
        $state = if ($stateLine) { ($stateLine -replace '^RUN_STATE=', '').Trim() } else { "UNKNOWN" }
        $elapsed = [Math]::Round(((Get-Date) - $startedAt).TotalSeconds, 0)
        $newLogLines = @($checkOutput | Where-Object { $_ -notmatch '^(RUN_STATE|LOG_LINE_COUNT)=' })
        if ($newLogLines.Count -gt 0) {
            Write-Output "[6/12] Estado=$state elapsed_seconds=$elapsed poll=$poll (nuevas lineas)"
            $newLogLines | ForEach-Object { Write-Output "  LOG: $_" }
            $lastConsoleUpdate = $elapsed
        } elseif (($elapsed - $lastConsoleUpdate) -ge 60) {
            Write-Output "[6/12] Estado=$state elapsed_seconds=$elapsed (sin nuevas lineas; runner sigue vivo)"
            $lastConsoleUpdate = $elapsed
        }
        if ($lineCountLine) {
            $nextLogLine = [int](($lineCountLine -replace '^LOG_LINE_COUNT=', '').Trim()) + 1
        }
        if ($state -eq "FINISHED") {
            $finished = $true
            break
        }
        if ($elapsed -ge 1500 -and $elapsed -lt 1516) {
            Write-Stage 7 "AVISO 25 MINUTOS: sigue activa. ACCION: NO cierres Chrome/noVNC ni interrumpas; continua esperando. Solo intervenir si no hay latidos durante 2 minutos."
        }
    } while ((Get-Date) -lt $deadline)

    if (-not $finished) {
        Write-Output "RESULTADO: PENDIENTE - El monitor alcanzo su limite, pero la ejecucion remota sigue activa."
        $exitCode = 2
        return
    }

    Write-Stage 8 "La ejecucion remota termino; validando resultado real"
    $finalOutput = Invoke-Remote (("tail -n +{0} '{1}' 2>/dev/null || true" -f $runStartLine, $remoteLog)) "Lectura de resultado"
    $finalOutput | ForEach-Object { Write-Output "  FINAL: $_" }
    $success = ($finalOutput -join "`n") -match 'runner terminado: exit=0'
    if (-not $success) {
        throw "El runner termino sin confirmar exit=0. No se puede afirmar que el reporte se publico."
    }

    Write-Stage 9 "Extraccion terminada correctamente"
    Write-Stage 10 "Publicacion confirmada por el runner"
    Write-Stage 11 "Log remoto revisado"
    Write-Stage 12 "RESULTADO: OK"
    $exitCode = 0
}
catch {
    Write-Output "RESULTADO: FALLO - $($_.Exception.Message)"
    Write-Output "LOG: $LogPath"
}
finally {
    $elapsed = [Math]::Round(((Get-Date) - $startedAt).TotalSeconds, 1)
    Write-Output "FIN duration_seconds=$elapsed exit=$exitCode"
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}

exit $exitCode
