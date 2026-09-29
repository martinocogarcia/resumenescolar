param(
    [string]$BucketName = "resumen-escolar-gabitin",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511"
$Remote = "ubuntu@129.153.51.108"
$RemoteApp = "/opt/resumen-escolar"
$RemoteTmpApp = "/tmp/resumen_escolar_app_attachments.py"
$RemoteTmpAutomation = "/tmp/resumen_escolar_automation_attachments.py"

if (-not $LogPath) {
    $LogPath = Join-Path $ProjectRoot "codex-classroom-attachment-reader.log"
}

function Write-Step([string]$Text) {
    Write-Host $Text
    Add-Content -LiteralPath $LogPath -Value $Text
}

function Invoke-RemoteScript([string]$Name, [string]$Script, [string[]]$ExtraSshArgs = @()) {
    Write-Step "[remote] $Name"
    ($Script -replace "`r", "") | & ssh -i $KeyPath -o BatchMode=yes -o ConnectTimeout=12 @ExtraSshArgs -o StrictHostKeyChecking=accept-new $Remote "bash -s" 2>&1 |
        ForEach-Object {
            Add-Content -LiteralPath $LogPath -Value $_
            Write-Host $_
        }
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit $LASTEXITCODE"
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue
Set-Location $ProjectRoot

Write-Step "START $(Get-Date -Format o)"
Write-Step "[1/4] Subiendo app.py y automation.py a la VM"
& scp -i $KeyPath -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new `
    "$ProjectRoot\resumen_escolar\app.py" "${Remote}:$RemoteTmpApp" 2>&1 |
    ForEach-Object { Add-Content -LiteralPath $LogPath -Value $_ }
if ($LASTEXITCODE -ne 0) { throw "scp app.py failed with exit $LASTEXITCODE" }

& scp -i $KeyPath -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new `
    "$ProjectRoot\resumen_escolar\automation.py" "${Remote}:$RemoteTmpAutomation" 2>&1 |
    ForEach-Object { Add-Content -LiteralPath $LogPath -Value $_ }
if ($LASTEXITCODE -ne 0) { throw "scp automation.py failed with exit $LASTEXITCODE" }

Write-Step "[2/4] Instalando archivos y validando sintaxis remota"
$deployRemote = @"
set -euo pipefail
cp $RemoteApp/resumen_escolar/app.py $RemoteApp/resumen_escolar/app.py.bak-attachments-`$(date -u +%Y%m%dT%H%M%SZ)
cp $RemoteApp/resumen_escolar/automation.py $RemoteApp/resumen_escolar/automation.py.bak-attachments-`$(date -u +%Y%m%dT%H%M%SZ)
mv $RemoteTmpApp $RemoteApp/resumen_escolar/app.py
mv $RemoteTmpAutomation $RemoteApp/resumen_escolar/automation.py
cd $RemoteApp
PYTHONPATH=$RemoteApp $RemoteApp/.venv/bin/python -m py_compile resumen_escolar/app.py resumen_escolar/automation.py
echo REMOTE_COMPILE_OK=1
"@
Invoke-RemoteScript "deploy attachment reader" $deployRemote

Write-Step "[3/4] Ejecutando generacion acotada y publicacion"
$runRemote = @"
set -euo pipefail
cd $RemoteApp
set -a
. $RemoteApp/config/automation.env
set +a
export PYTHONPATH=${RemoteApp}:${RemoteApp}/.runtime/site-packages:`${PYTHONPATH:-}
export RESUMEN_ESCOLAR_CLASSROOM_TOPIC_INCLUDE="`${RESUMEN_ESCOLAR_CLASSROOM_TOPIC_INCLUDE:-Ciencias Naturales,CNat,sistema locomotor,Matematica,Mat,Lenguaje,Len,Religion,Rel,Ingles,Eng,Historia,CSoc,Ciencias Sociales}"
export RESUMEN_ESCOLAR_CLASSROOM_TOPIC_VIEWS="`${RESUMEN_ESCOLAR_CLASSROOM_TOPIC_VIEWS:-7}"
export RESUMEN_ESCOLAR_CLASSROOM_POSTS_TO_OPEN="`${RESUMEN_ESCOLAR_CLASSROOM_POSTS_TO_OPEN:-2}"
export RESUMEN_ESCOLAR_CLASSROOM_ATTACHMENTS_TO_OPEN="`${RESUMEN_ESCOLAR_CLASSROOM_ATTACHMENTS_TO_OPEN:-1}"
export RESUMEN_ESCOLAR_CLASSROOM_ATTACHMENT_POSTS_PER_SNAPSHOT="`${RESUMEN_ESCOLAR_CLASSROOM_ATTACHMENT_POSTS_PER_SNAPSHOT:-1}"
export RESUMEN_ESCOLAR_CLASSROOM_ATTACHMENT_TEXT_CHARS="`${RESUMEN_ESCOLAR_CLASSROOM_ATTACHMENT_TEXT_CHARS:-5000}"
echo REMOTE_RUN_START=`$(date -Is)
run_log=`$(mktemp)
set +e
RESUMEN_ESCOLAR_APP_DIR=$RemoteApp RESUMEN_ESCOLAR_LOG_DIR=$RemoteApp/logs timeout 30m $RemoteApp/.venv/bin/python -m resumen_escolar.automation run --publish >"`$run_log" 2>&1
run_status=`$?
set -e
cat "`$run_log" |
  sed -E 's/(ocid1\.[A-Za-z0-9._-]+)/[OCID_REDACTED]/g' |
  sed -E 's#(C:/Users/Martin|C:\\Users\\Martin)[^[:space:]]*#[PATH_REDACTED]#g'
rm -f "`$run_log"
if [ "`$run_status" -ne 0 ]; then
  echo REMOTE_RUN_FAILED=`$run_status
  exit "`$run_status"
fi
echo REMOTE_RUN_DONE=`$(date -Is)
prompt=`$(ls -td $RemoteApp/outbox/*/prompt_chatgpt.txt | head -n 1)
echo PROMPT_PATH=`$prompt
echo PROMPT_SIZE=`$(wc -c < "`$prompt")
echo PROMPT_HAS_MATERIAL_SECTION=`$(grep -c '^MATERIAL DE ESTUDIO EXTRAIDO DE CLASSROOM' "`$prompt" || true)
echo PROMPT_HAS_RECENT_MATERIALS=`$(grep -c '^MATERIALES CLASSROOM RECIENTES' "`$prompt" || true)
echo PROMPT_ATTACHMENT_CONTENT_BLOCKS=`$(grep -c '^CONTENIDO EXTRAIDO DEL ADJUNTO CLASSROOM:' "`$prompt" || true)
materials_dir=`$(dirname "`$prompt")/materials
echo MATERIALS_DIR=`$materials_dir
if [ -f "`$materials_dir/materials_index.json" ]; then
  echo MATERIALS_INDEX_SIZE=`$(wc -c < "`$materials_dir/materials_index.json")
  echo MATERIALS_SUMMARY_SIZE=`$(wc -c < "`$materials_dir/materials_summary.txt" 2>/dev/null || echo 0)
  echo MATERIALS_FILES_COUNT=`$(find "`$materials_dir/files" -type f 2>/dev/null | wc -l)
else
  echo MATERIALS_INDEX_MISSING=1
fi
echo PROMPT_ATTACHMENT_DIAG_START
grep -nEi 'adjuntos_vistos|adjuntos_intentados|adjuntos_con_texto|MATERIALES CLASSROOM RECIENTES|MATERIAL DE ESTUDIO EXTRAIDO|CONTENIDO EXTRAIDO DEL ADJUNTO|Ciencias Naturales|sistema locomotor|CNat' "`$prompt" | head -n 140 || true
echo PROMPT_ATTACHMENT_DIAG_END
"@
Invoke-RemoteScript "bounded scan and publish" $runRemote @("-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=45")

Write-Step "[4/4] Verificando objeto latest publicado en OCI"
$objectRemote = @"
set -euo pipefail
cd $RemoteApp
set -a
. $RemoteApp/config/automation.env
set +a
tmp=`$(mktemp)
rm -f "`$tmp"
oci_cmd=($RemoteApp/.venv/bin/oci --auth instance_principal --region ca-toronto-1 os object get --bucket-name $BucketName --name latest/prompt_chatgpt.txt --file "`$tmp")
if [ -n "`${RESUMEN_ESCOLAR_OCI_NAMESPACE:-}" ]; then
  oci_cmd+=("--namespace" "`$RESUMEN_ESCOLAR_OCI_NAMESPACE")
fi
err=`$(mktemp)
set +e
"`${oci_cmd[@]}" >/dev/null 2>"`$err"
object_status=`$?
set -e
if [ "`$object_status" -ne 0 ]; then
  echo OBJECT_VERIFY_FAILED=`$object_status
  sed -n '1,12p' "`$err" || true
  rm -f "`$tmp" "`$err"
  exit 0
fi
echo OBJECT_SIZE=`$(wc -c < "`$tmp")
echo OBJECT_HAS_MATERIAL_SECTION=`$(grep -c '^MATERIAL DE ESTUDIO EXTRAIDO DE CLASSROOM' "`$tmp" || true)
echo OBJECT_HAS_RECENT_MATERIALS=`$(grep -c '^MATERIALES CLASSROOM RECIENTES' "`$tmp" || true)
echo OBJECT_ATTACHMENT_CONTENT_BLOCKS=`$(grep -c '^CONTENIDO EXTRAIDO DEL ADJUNTO CLASSROOM:' "`$tmp" || true)
echo OBJECT_ATTACHMENT_DIAG_START
grep -nEi 'adjuntos_vistos|adjuntos_intentados|adjuntos_con_texto|MATERIALES CLASSROOM RECIENTES|MATERIAL DE ESTUDIO EXTRAIDO|CONTENIDO EXTRAIDO DEL ADJUNTO|Ciencias Naturales|sistema locomotor|CNat' "`$tmp" | head -n 140 || true
echo OBJECT_ATTACHMENT_DIAG_END
rm -f "`$tmp"
rm -f "`$err"
"@
Invoke-RemoteScript "verify latest object" $objectRemote

Write-Step "[4b/4] Verificando materiales latest en OCI"
$materialsRemote = @"
set -euo pipefail
cd $RemoteApp
set -a
. $RemoteApp/config/automation.env
set +a
tmp=`$(mktemp)
err=`$(mktemp)
oci_cmd=($RemoteApp/.venv/bin/oci --auth instance_principal --region ca-toronto-1 os object get --bucket-name $BucketName --name latest/materials/materials_summary.txt --file "`$tmp")
if [ -n "`${RESUMEN_ESCOLAR_OCI_NAMESPACE:-}" ]; then
  oci_cmd+=("--namespace" "`$RESUMEN_ESCOLAR_OCI_NAMESPACE")
fi
set +e
"`${oci_cmd[@]}" >/dev/null 2>"`$err"
status=`$?
set -e
if [ "`$status" -ne 0 ]; then
  echo MATERIALS_OBJECT_VERIFY_FAILED=`$status
  sed -n '1,12p' "`$err" || true
  rm -f "`$tmp" "`$err"
  exit 0
fi
echo MATERIALS_OBJECT_SIZE=`$(wc -c < "`$tmp")
echo MATERIALS_OBJECT_HAS_HEADER=`$(grep -c '^MATERIALES CLASSROOM RECIENTES' "`$tmp" || true)
rm -f "`$tmp" "`$err"
"@
Invoke-RemoteScript "verify latest materials" $materialsRemote

Write-Step "END $(Get-Date -Format o)"
