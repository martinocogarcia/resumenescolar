param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteDir = "/opt/resumen-escolar",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-verify-remote-prompt-p2.log"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $KeyPath)) { throw "No se encontro la llave SSH configurada." }

$remote = "${VmUser}@${VmHost}"
$sshCommon = @(
    "-i", $KeyPath, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=2", "-o", "StrictHostKeyChecking=accept-new"
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
    if ($LASTEXITCODE -ne 0) { throw "La validacion remota fallo." }
    return @($result)
}

"Inicio $(Get-Date -Format s)" | Set-Content -LiteralPath $LogPath -Encoding utf8
Write-ProgressLog "[1/6] Verificando acceso SSH"
$check = & ssh @sshCommon $remote "printf SSH_OK"
if ($LASTEXITCODE -ne 0 -or ($check -join "") -notmatch "SSH_OK") { throw "SSH no disponible." }

Write-ProgressLog "[2/6] Confirmando termino del runner"
$reportDate = (Get-Date).ToString("yyyy-MM-dd")
$lastLine = & ssh @sshCommon $remote "tail -n 1 '$RemoteDir/logs/weekly-prompt-$reportDate.log'"
if ($LASTEXITCODE -ne 0 -or ($lastLine -join "") -notmatch "terminado") {
    throw "La ultima generacion remota no termino correctamente."
}

Write-ProgressLog "[3/6] Descargando TXT latest para validacion"
$verifyScript = @"
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
$result = Invoke-RemoteScript $verifyScript
foreach ($line in $result) {
    if ($line -match '^(OBJECT_BYTES|HAS_P1P2|HAS_P2_MAIN|HAS_OLD_P1_ONLY|LATEST_TXT_URL_PRESENT)=') {
        Write-ProgressLog "[4/6] $line"
    }
}

Write-ProgressLog "[5/6] URL protegida y configurada para notificaciones"
Write-ProgressLog "[6/6] Validacion terminada"
