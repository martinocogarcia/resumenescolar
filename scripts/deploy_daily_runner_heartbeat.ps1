param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteDir = "/opt/resumen-escolar",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-deploy-daily-runner.log"
)

$ErrorActionPreference = "Continue"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$localScript = Join-Path $projectRoot "scripts\run_daily_report.sh"
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
    Write-Output ("[{0}/10] {1}" -f $Number, $Message)
}

function Invoke-NativeChecked {
    param([string]$Label, [string]$FilePath, [string[]]$CommandArgs)
    & $FilePath @CommandArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Label fallo con exit=$exitCode."
    }
}

$startedAt = Get-Date
$exitCode = 1
$transcriptStarted = $false

try {
    Start-Transcript -LiteralPath $LogPath -Append | Out-Null
    $transcriptStarted = $true

    Write-Stage 1 "Inicio: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Stage 2 "Validando el runner local"
    if (-not (Test-Path -LiteralPath $localScript -PathType Leaf)) {
        throw "No existe el runner local: $localScript"
    }
    if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
        throw "No se encontro la llave SSH configurada."
    }

    Write-Stage 3 "Verificando conexion SSH con la VM"
    Invoke-NativeChecked "Conexion SSH" "ssh.exe" ($sshArgs + @($remote, "printf SSH_OK"))

    $version = Get-Date -Format "yyyyMMdd-HHmmss"
    $remoteTemp = "/tmp/resumen-escolar-runner-$version.sh"
    $remoteTarget = "$RemoteDir/scripts/run_daily_report.sh"
    $remoteBackup = "$RemoteDir/scripts/run_daily_report.sh.backup-$version"

    Write-Stage 4 "Registrando version actual del runner remoto"
    Invoke-NativeChecked "Lectura de version remota" "ssh.exe" ($sshArgs + @($remote, "sha256sum '$remoteTarget' || true"))

    Write-Stage 5 "Subiendo el runner actualizado"
    Invoke-NativeChecked "Carga SCP" "scp.exe" ($sshArgs + @($localScript, "${remote}:$remoteTemp"))

    Write-Stage 6 "Validando sintaxis Bash en la VM"
    Invoke-NativeChecked "Validacion Bash" "ssh.exe" ($sshArgs + @($remote, "bash -n '$remoteTemp' && echo BASH_SYNTAX_OK"))

    Write-Stage 7 "Creando respaldo remoto recuperable"
    Invoke-NativeChecked "Respaldo remoto" "ssh.exe" ($sshArgs + @($remote, "cp '$remoteTarget' '$remoteBackup' && echo BACKUP_CREATED"))

    Write-Stage 8 "Instalando runner con permisos ejecutables"
    Invoke-NativeChecked "Instalacion remota" "ssh.exe" ($sshArgs + @($remote, "install -m 755 '$remoteTemp' '$remoteTarget' && rm -f '$remoteTemp'"))

    Write-Stage 9 "Verificando archivo instalado"
    Invoke-NativeChecked "Verificacion remota" "ssh.exe" ($sshArgs + @($remote, "bash -n '$remoteTarget' && grep -q 'ADVERTENCIA: la ejecucion supera 25 minutos' '$remoteTarget' && echo RUNNER_HEARTBEAT_READY"))

    Write-Stage 10 "RESULTADO: OK - El proximo cron mostrara latidos cada minuto y advertencia a los 25 minutos."
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
