param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteApp = "/opt/resumen-escolar",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-deploy-classroom-login-alert.log"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$localAutomation = Join-Path $projectRoot "resumen_escolar\automation.py"
$remote = "${VmUser}@${VmHost}"
$remoteTemp = "/tmp/resumen_escolar_automation_login_alert.py"
$sshArgs = @(
    "-i", $KeyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "StrictHostKeyChecking=accept-new"
)

$exitCode = 0
try {
    & {
        "START $(Get-Date -Format o)"
        "REMOTE=$remote"
        "STAGE=upload_automation"
        & scp @sshArgs $localAutomation "${remote}:$remoteTemp"
        if ($LASTEXITCODE -ne 0) { throw "scp automation.py failed with exit $LASTEXITCODE" }

        "STAGE=install_and_validate"
        @"
set -euo pipefail
REMOTE_APP='$RemoteApp'
REMOTE_TEMP='$remoteTemp'
backup="`$REMOTE_APP/resumen_escolar/automation.py.bak-login-alert-`$(date -u +%Y%m%dT%H%M%SZ)"
cp "`$REMOTE_APP/resumen_escolar/automation.py" "`$backup"
mv "`$REMOTE_TEMP" "`$REMOTE_APP/resumen_escolar/automation.py"
cd "`$REMOTE_APP"
PYTHONPATH="`$REMOTE_APP" "`$REMOTE_APP/.venv/bin/python" -m py_compile resumen_escolar/automation.py
echo REMOTE_AUTOMATION_COMPILE_OK=true
echo REMOTE_BACKUP_CREATED=true
"@ | & ssh @sshArgs $remote "bash -s"
        if ($LASTEXITCODE -ne 0) { throw "remote validation failed with exit $LASTEXITCODE" }
        "END $(Get-Date -Format o)"
    } *> $LogPath
} catch {
    $exitCode = 1
} finally {
    if (Test-Path -LiteralPath $remoteTemp) { Remove-Item -LiteralPath $remoteTemp -Force -ErrorAction SilentlyContinue }
}

exit $exitCode
