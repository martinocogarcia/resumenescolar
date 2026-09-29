param(
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511"
$Remote = "ubuntu@129.153.51.108"
$RemoteApp = "/opt/resumen-escolar"

if (-not $LogPath) {
    $LogPath = Join-Path $ProjectRoot "codex-attachment-remote-diagnosis.log"
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue

function Step([string]$Text) {
    Write-Host $Text
    Add-Content -LiteralPath $LogPath -Value $Text
}

Step "START $(Get-Date -Format o)"
Step "[1/3] Revisando procesos remotos"

$remoteScript = @"
set -euo pipefail
echo REMOTE_NOW=`$(date -Is)
echo PROCESS_START
ps -eo pid,ppid,stat,pcpu,pmem,etime,cmd --sort=-pcpu |
  grep -Ei 'resumen_escolar|playwright|chrom|python|timeout 45m' |
  grep -v grep |
  head -n 60 || true
echo PROCESS_END
echo MEMORY_START
free -h || true
echo MEMORY_END
echo DISK_START
df -h / /tmp $RemoteApp 2>/dev/null || true
echo DISK_END
echo LOGS_START
ls -lt $RemoteApp/logs 2>/dev/null | head -n 12 || true
echo LOGS_END
echo WEEKLY_LOG_TAIL_START
latest_log=`$(ls -t $RemoteApp/logs/weekly-prompt-*.log 2>/dev/null | head -n 1 || true)
if [ -n "`$latest_log" ]; then
  echo WEEKLY_LOG=`$latest_log
  tail -n 80 "`$latest_log" |
    sed -E 's/(ocid1\.[A-Za-z0-9._-]+)/[OCID_REDACTED]/g' |
    sed -E 's#(C:/Users/Martin|C:\\Users\\Martin)[^[:space:]]*#[PATH_REDACTED]#g'
else
  echo WEEKLY_LOG=none
fi
echo WEEKLY_LOG_TAIL_END
echo PROMPT_MARKERS_START
prompt=`$(ls -td $RemoteApp/outbox/*/prompt_chatgpt.txt 2>/dev/null | head -n 1 || true)
if [ -n "`$prompt" ]; then
  echo PROMPT_PATH=`$prompt
  echo PROMPT_SIZE=`$(wc -c < "`$prompt")
  echo PROMPT_DATE=`$(basename "`$(dirname "`$prompt")")
  echo PROMPT_HAS_MATERIAL_SECTION=`$(grep -c '^MATERIAL DE ESTUDIO EXTRAIDO DE CLASSROOM' "`$prompt" || true)
  echo PROMPT_ATTACHMENT_CONTENT_BLOCKS=`$(grep -c '^CONTENIDO EXTRAIDO DEL ADJUNTO CLASSROOM:' "`$prompt" || true)
  echo PROMPT_GENERIC_GOOGLE_LINKS=`$(grep -ciE 'Drive Drive Drive|Documentos Documentos|Hojas de c.+lculo Hojas de c.+lculo|Presentaciones Presentaciones|fila [123] de 3' "`$prompt" || true)
  echo PROMPT_SCIENCE_MARKERS=`$(grep -ciE 'Ciencias Naturales|sistema locomotor|CNat' "`$prompt" || true)
  echo PROMPT_ATTACHMENT_DIAG_SAMPLE_START
  grep -nEi 'adjuntos_vistos|adjuntos_intentados|adjuntos_con_texto|MATERIAL DE ESTUDIO EXTRAIDO|CONTENIDO EXTRAIDO DEL ADJUNTO|Ciencias Naturales|sistema locomotor|CNat' "`$prompt" |
    head -n 80 || true
  echo PROMPT_ATTACHMENT_DIAG_SAMPLE_END
else
  echo PROMPT_PATH=none
fi
echo PROMPT_MARKERS_END
"@

Step "[2/3] Ejecutando diagnóstico remoto read-only"
($remoteScript -replace "`r", "") | & ssh -i $KeyPath -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new $Remote "bash -s" 2>&1 |
    ForEach-Object {
        Add-Content -LiteralPath $LogPath -Value $_
        Write-Host $_
    }
if ($LASTEXITCODE -ne 0) {
    throw "remote diagnosis failed with exit $LASTEXITCODE"
}

Step "[3/3] Diagnóstico terminado"
Step "END $(Get-Date -Format o)"
