param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteDir = "/opt/resumen-escolar",
    [string]$BucketName = "resumen-escolar-gabitin",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-remote-upload-test.log"
)

$ErrorActionPreference = "Continue"

$sshArgs = @(
    "-i", $KeyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "UserKnownHostsFile=$env:TEMP\codex_known_hosts_resumen_escolar",
    "-o", "StrictHostKeyChecking=accept-new"
)

$remote = @"
set -euo pipefail
REMOTE_DIR='$RemoteDir'
BUCKET_NAME='$BucketName'
TEST_DIR="`$REMOTE_DIR/outbox/upload-test"
TEST_FILE="`$TEST_DIR/prompt_chatgpt.txt"
mkdir -p "`$TEST_DIR"
printf 'resumen escolar upload smoke test %s\n' "`$(date -Is)" > "`$TEST_FILE"
if [ ! -s "`$TEST_FILE" ]; then
  echo "test_file_empty"
  exit 2
fi
if [ -x "`$REMOTE_DIR/.venv/bin/python" ]; then
  PY="`$REMOTE_DIR/.venv/bin/python"
else
  PY=python3
fi
export RESUMEN_ESCOLAR_BUCKET="`$BUCKET_NAME"
export RESUMEN_ESCOLAR_OCI_AUTH=instance_principal
export PYTHONWARNINGS=ignore
export RESUMEN_ESCOLAR_LATEST_OBJECT=latest/upload-smoke-test.txt
export RESUMEN_ESCOLAR_ARCHIVE=0
cd "`$REMOTE_DIR"
"`$PY" -m resumen_escolar.automation publish --prompt-path "`$TEST_FILE" --no-archive
"@

& {
    "START $(Get-Date -Format o)"
    "TARGET=$VmUser@$VmHost"
    "REMOTE_DIR=$RemoteDir"
    "BUCKET_NAME=$BucketName"
    ssh @sshArgs "$VmUser@$VmHost" $remote
    "END $(Get-Date -Format o)"
    "exit=$LASTEXITCODE"
} *> $LogPath

exit $LASTEXITCODE
