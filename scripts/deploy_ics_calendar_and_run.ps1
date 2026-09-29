param(
    [string]$BucketName = "resumen-escolar-gabitin"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511"
$Remote = "ubuntu@129.153.51.108"
$RemoteApp = "/opt/resumen-escolar"
$RemoteTmpApp = "/tmp/resumen_escolar_app_ics.py"
$RemoteTmpAutomation = "/tmp/resumen_escolar_automation_ics.py"
$RemoteTmpRunner = "/tmp/resumen_escolar_run_weekly_prompt_ics.sh"

function Step($Name) {
    Write-Output ""
    Write-Output "===== $Name ====="
}

function Invoke-RemoteScript($Name, $Script, $ExtraSshArgs = @()) {
    ($Script -replace "`r", "") | & ssh -i $KeyPath -o BatchMode=yes -o ConnectTimeout=12 @ExtraSshArgs -o StrictHostKeyChecking=accept-new $Remote "bash -s"
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit $LASTEXITCODE"
    }
}

Set-Location $ProjectRoot

Step "deploy app.py"
& scp -i $KeyPath -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new `
    "$ProjectRoot\resumen_escolar\app.py" "${Remote}:$RemoteTmpApp"
if ($LASTEXITCODE -ne 0) { throw "scp app.py failed with exit $LASTEXITCODE" }
& scp -i $KeyPath -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new `
    "$ProjectRoot\resumen_escolar\automation.py" "${Remote}:$RemoteTmpAutomation"
if ($LASTEXITCODE -ne 0) { throw "scp automation.py failed with exit $LASTEXITCODE" }
& scp -i $KeyPath -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new `
    "$ProjectRoot\scripts\run_weekly_prompt.sh" "${Remote}:$RemoteTmpRunner"
if ($LASTEXITCODE -ne 0) { throw "scp run_weekly_prompt.sh failed with exit $LASTEXITCODE" }

$deployRemote = @"
set -euo pipefail
cp $RemoteApp/resumen_escolar/app.py $RemoteApp/resumen_escolar/app.py.bak-calendar-ics-`$(date -u +%Y%m%dT%H%M%SZ)
cp $RemoteApp/resumen_escolar/automation.py $RemoteApp/resumen_escolar/automation.py.bak-calendar-ics-`$(date -u +%Y%m%dT%H%M%SZ)
cp $RemoteApp/scripts/run_weekly_prompt.sh $RemoteApp/scripts/run_weekly_prompt.sh.bak-calendar-ics-`$(date -u +%Y%m%dT%H%M%SZ)
mv $RemoteTmpApp $RemoteApp/resumen_escolar/app.py
mv $RemoteTmpAutomation $RemoteApp/resumen_escolar/automation.py
mv $RemoteTmpRunner $RemoteApp/scripts/run_weekly_prompt.sh
chmod +x $RemoteApp/scripts/run_weekly_prompt.sh
cd $RemoteApp
PYTHONPATH=$RemoteApp $RemoteApp/.venv/bin/python -m py_compile resumen_escolar/app.py resumen_escolar/automation.py
echo deploy-ok
"@
Invoke-RemoteScript "remote deploy" $deployRemote

Step "quick ics check"
$quickCheck = @'
set -euo pipefail
cd /opt/resumen-escolar
export PYTHONPATH=/opt/resumen-escolar:${PYTHONPATH:-}
/opt/resumen-escolar/.venv/bin/python -c 'from resumen_escolar.app import fetch_sscc_calendar_ics_4a_events; events, stats = fetch_sscc_calendar_ics_4a_events(); print("EVENTS_4A=" + str(len(events))); print("WINDOW=" + stats["calendar_window_start"] + ".." + stats["calendar_window_end"]); [print(event["dates"] + " | " + event["text"]) for event in events[:20]]'
'@
Invoke-RemoteScript "quick ics check" $quickCheck

Step "run weekly prompt"
$runRemote = @"
set -euo pipefail
RESUMEN_ESCOLAR_APP_DIR=$RemoteApp RESUMEN_ESCOLAR_LOG_DIR=$RemoteApp/logs RESUMEN_ESCOLAR_LOCK_FILE=$RemoteApp/.runtime/manual-calendar-ics.lock $RemoteApp/scripts/run_weekly_prompt.sh
prompt=`$(ls -td $RemoteApp/outbox/*/prompt_chatgpt.txt | head -n 1)
echo PROMPT_PATH=`$prompt
echo PROMPT_SIZE=`$(wc -c < "`$prompt")
echo PROMPT_HAS_CANONICAL_P1P2=`$(grep -c 'CALIFICACIONES P1/P2 CANONICAS SCHOOLNET' "`$prompt" || true)
echo PROMPT_CANONICAL_P1P2_ROWS=`$(awk 'BEGIN{show=0;c=0} /^CALIFICACIONES P1\/P2 CANONICAS SCHOOLNET`$/{show=1;next} show && /^RESUMEN CANONICO/{show=0} show && / \| / && !/^Fuente / && !/^Regla / && !/^Si / && !/^Formato:/{c++} END{print c+0}' "`$prompt")
echo PROMPT_HAS_RAW_GRADE_DETAIL=`$(grep -c 'DETALLE CRUDO ESTRUCTURADO SCHOOLNET CALIFICACIONES' "`$prompt" || true)
echo PROMPT_ICAL_EVENTS=`$(grep -c 'Evento [0-9].*iCal SSCC Evaluaciones 4A' "`$prompt" || true)
echo PROMPT_P1P2_BLOCK_START
awk '/^CALIFICACIONES P1\/P2 CANONICAS SCHOOLNET`$/{show=1;count=0} show{print; count++; if (count >= 20 || /^RESUMEN CANONICO/) show=0}' "`$prompt" | head -n 24 || true
echo PROMPT_P1P2_BLOCK_END
echo CALENDAR_MARKERS_START
grep -nEi 'Calendario SSCC|fuente=ical|Evento [0-9]+|EVENTS_4A|EVADOC|Prueba|Lectura com|2026-06' "`$prompt" | head -n 80 || true
echo CALENDAR_MARKERS_END
cd $RemoteApp
set -a
. $RemoteApp/config/automation.env
set +a
tmp=`$(mktemp)
rm -f "`$tmp"
if [ -n "`${RESUMEN_ESCOLAR_OCI_NAMESPACE:-}" ]; then
  $RemoteApp/.venv/bin/oci --auth instance_principal --region ca-toronto-1 os object get --bucket-name $BucketName --namespace "`$RESUMEN_ESCOLAR_OCI_NAMESPACE" --name latest/prompt_chatgpt.txt --file "`$tmp" >/dev/null
else
  $RemoteApp/.venv/bin/oci --auth instance_principal --region ca-toronto-1 os object get --bucket-name $BucketName --name latest/prompt_chatgpt.txt --file "`$tmp" >/dev/null
fi
echo OBJECT_PROMPT_SIZE=`$(wc -c < "`$tmp")
echo OBJECT_HAS_CANONICAL_P1P2=`$(grep -c 'CALIFICACIONES P1/P2 CANONICAS SCHOOLNET' "`$tmp" || true)
echo OBJECT_CANONICAL_P1P2_ROWS=`$(awk 'BEGIN{show=0;c=0} /^CALIFICACIONES P1\/P2 CANONICAS SCHOOLNET`$/{show=1;next} show && /^RESUMEN CANONICO/{show=0} show && / \| / && !/^Fuente / && !/^Regla / && !/^Si / && !/^Formato:/{c++} END{print c+0}' "`$tmp")
echo OBJECT_HAS_RAW_GRADE_DETAIL=`$(grep -c 'DETALLE CRUDO ESTRUCTURADO SCHOOLNET CALIFICACIONES' "`$tmp" || true)
echo OBJECT_ICAL_EVENTS=`$(grep -c 'Evento [0-9].*iCal SSCC Evaluaciones 4A' "`$tmp" || true)
echo OBJECT_P1P2_BLOCK_START
awk '/^CALIFICACIONES P1\/P2 CANONICAS SCHOOLNET`$/{show=1;count=0} show{print; count++; if (count >= 20 || /^RESUMEN CANONICO/) show=0}' "`$tmp" | head -n 24 || true
echo OBJECT_P1P2_BLOCK_END
rm -f "`$tmp"
"@
Invoke-RemoteScript "run weekly prompt" $runRemote @("-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=60")

Step "done"
