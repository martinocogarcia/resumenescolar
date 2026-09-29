param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteDir = "/opt/resumen-escolar",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-remote-prompt-publish.log",
    [int]$TimeoutSeconds = 1200
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $KeyPath)) {
    throw "No se encontro la llave SSH configurada."
}

$remote = "${VmUser}@${VmHost}"
$sshCommon = @(
    "-i", $KeyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=20",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=2",
    "-o", "StrictHostKeyChecking=accept-new"
)

function Write-ProgressLog {
    param([string]$Message)
    $line = "$(Get-Date -Format s) | $Message"
    Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
    Write-Host $Message
}

function Invoke-RemoteScript {
    param([string]$Script)
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script))
    $result = & ssh @sshCommon $remote "echo $encoded | base64 -d | bash"
    if ($LASTEXITCODE -ne 0) {
        throw "La operacion remota fallo."
    }
    return @($result)
}

New-Item -ItemType Directory -Force (Split-Path -Parent $LogPath) | Out-Null
"Inicio $(Get-Date -Format s)" | Set-Content -LiteralPath $LogPath -Encoding utf8

Write-ProgressLog "[1/12] Verificando acceso SSH"
$sshCheck = & ssh @sshCommon $remote "printf SSH_OK"
if ($LASTEXITCODE -ne 0 -or ($sshCheck -join "") -notmatch "SSH_OK") {
    throw "No fue posible verificar SSH hacia la VM."
}

Write-ProgressLog "[2/12] Creando o renovando URL de solo lectura"
$configurePar = @"
set -euo pipefail
APP_DIR='$RemoteDir'
cd "`$APP_DIR"
set -a
source "`$APP_DIR/config/automation.env"
set +a
url="`$("`$APP_DIR/scripts/create_readonly_par.sh")"
test -n "`$url"
tmp="`$(mktemp)"
grep -v '^RESUMEN_ESCOLAR_LATEST_TXT_URL=' "`$APP_DIR/config/automation.env" > "`$tmp" || true
echo "RESUMEN_ESCOLAR_LATEST_TXT_URL=`$url" >> "`$tmp"
install -m 600 "`$tmp" "`$APP_DIR/config/automation.env"
rm -f "`$tmp"
echo PAR_CONFIGURED=1
"@
$parResult = Invoke-RemoteScript $configurePar
if (($parResult -join "") -notmatch "PAR_CONFIGURED=1") {
    throw "No se pudo configurar la URL de lectura del TXT."
}

Write-ProgressLog "[3/12] Iniciando generacion remota"
$startRun = @"
set -euo pipefail
APP_DIR='$RemoteDir'
export RESUMEN_ESCOLAR_APP_DIR="`$APP_DIR"
export RESUMEN_ESCOLAR_LOG_DIR="`$APP_DIR/logs"
export RESUMEN_ESCOLAR_LOCK_FILE="`$APP_DIR/.runtime/manual-prompt-publish.lock"
nohup "`$APP_DIR/scripts/run_weekly_prompt.sh" >/dev/null 2>&1 &
echo "RUN_PID=`$!"
"@
$startResult = Invoke-RemoteScript $startRun
$pidLine = @($startResult | Where-Object { $_ -match '^RUN_PID=\d+$' } | Select-Object -Last 1)
if (-not $pidLine) {
    throw "No se recibio el identificador de la generacion remota."
}
$runPid = ($pidLine -replace '^RUN_PID=', '').Trim()

Write-ProgressLog "[4/12] Esperando extraccion y publicacion"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$poll = 0
do {
    Start-Sleep -Seconds 15
    $poll++
    $status = & ssh @sshCommon $remote "if kill -0 $runPid 2>/dev/null; then printf RUNNING; else printf FINISHED; fi"
    if ($LASTEXITCODE -ne 0) {
        throw "Se perdio la conexion SSH durante el monitoreo."
    }
    $state = ($status -join "").Trim()
    Write-ProgressLog "[5/12] Generacion remota: $state ($($poll * 15)s)"
    if ($state -eq "FINISHED") { break }
} while ((Get-Date) -lt $deadline)

if ($state -ne "FINISHED") {
    throw "La generacion no termino dentro del limite configurado."
}

Write-ProgressLog "[6/12] Confirmando resultado de la generacion"
$reportDate = (Get-Date).ToString("yyyy-MM-dd")
$runnerLog = & ssh @sshCommon $remote "tail -n 1 '$RemoteDir/logs/weekly-prompt-$reportDate.log'"
if ($LASTEXITCODE -ne 0 -or ($runnerLog -join "") -notmatch "terminado") {
    throw "La generacion remota termino con error; el TXT anterior se conserva sin sobrescribir."
}

Write-ProgressLog "[7/12] Validando TXT publicado"
$verifyPublished = @"
set -euo pipefail
APP_DIR='$RemoteDir'
set -a
source "`$APP_DIR/config/automation.env"
set +a
test -n "`$RESUMEN_ESCOLAR_LATEST_TXT_URL"
tmp="`$(mktemp)"
trap 'rm -f "`$tmp"' EXIT
"`$RESUMEN_ESCOLAR_OCI_CLI" --auth "`$RESUMEN_ESCOLAR_OCI_AUTH" os object get --bucket-name "`$RESUMEN_ESCOLAR_BUCKET" --name "`$RESUMEN_ESCOLAR_LATEST_OBJECT" --file "`$tmp" >/dev/null
echo "OBJECT_BYTES=`$(wc -c < "`$tmp")"
grep -q 'CALIFICACIONES P1/P2 CANONICAS SCHOOLNET' "`$tmp" && echo HAS_P1P2=1 || echo HAS_P1P2=0
grep -q 'P2 como columna principal' "`$tmp" && echo HAS_P2_MAIN=1 || echo HAS_P2_MAIN=0
grep -q 'CALIFICACIONES P1 CANONICAS SCHOOLNET' "`$tmp" && echo HAS_OLD_P1_ONLY=1 || echo HAS_OLD_P1_ONLY=0
echo LATEST_TXT_URL_PRESENT=1
"@
$verification = Invoke-RemoteScript $verifyPublished
foreach ($line in $verification) {
    if ($line -match '^(OBJECT_BYTES|HAS_P1P2|HAS_P2_MAIN|HAS_OLD_P1_ONLY|LATEST_TXT_URL_PRESENT)=') {
        Write-ProgressLog "[8/12] $line"
    }
}

Write-ProgressLog "[9/12] URL del TXT configurada para el correo"
Write-ProgressLog "[10/12] Prompt publicado en Object Storage"
Write-ProgressLog "[11/12] Validacion final completada"
Write-ProgressLog "[12/12] Terminado"
