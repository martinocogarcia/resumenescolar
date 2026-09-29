param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteDir = "/opt/resumen-escolar",
    [string]$BucketName,
    [string]$SchoolNetSecretOcid = "",
    [string]$Region = "ca-toronto-1",
    [switch]$BootstrapSystemPackages,
    [switch]$InstallDependencies,
    [switch]$InstallCron,
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-resumen-deploy.log"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StageRoot = Join-Path $env:TEMP ("resumen-escolar-stage-" + [guid]::NewGuid().ToString("N"))
$ArchivePath = Join-Path $env:TEMP ("resumen-escolar-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".tar.gz")
$RemoteArchive = "/tmp/resumen-escolar.tar.gz"

$sshCommon = @(
    "-i", $KeyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "UserKnownHostsFile=$env:TEMP\codex_known_hosts_resumen_escolar",
    "-o", "StrictHostKeyChecking=accept-new"
)
$remote = "${VmUser}@${VmHost}"

function Invoke-Logged {
    param([scriptblock]$Block)
    & {
        "START $(Get-Date -Format o)"
        "PROJECT_ROOT=$ProjectRoot"
        "REMOTE=$remote"
        "REMOTE_DIR=$RemoteDir"
        "INSTALL_CRON=$InstallCron"
        & $Block
        "END $(Get-Date -Format o)"
        "exit=$LASTEXITCODE"
    } *> $LogPath
}

function Copy-ProjectFiles {
    New-Item -ItemType Directory -Force $StageRoot | Out-Null
    Push-Location $ProjectRoot
    try {
        $files = git ls-files --cached --others --exclude-standard
        foreach ($file in $files) {
            if ([string]::IsNullOrWhiteSpace($file)) { continue }
            if ($file -ne 'requirements.txt' -and
                $file -notmatch '^resumen_escolar/[^/]+\.py$' -and
                $file -notmatch '^scripts/[^/]+\.sh$') { continue }
            $src = Join-Path $ProjectRoot $file
            $dst = Join-Path $StageRoot $file
            New-Item -ItemType Directory -Force (Split-Path $dst -Parent) | Out-Null
            Copy-Item -LiteralPath $src -Destination $dst -Force
        }
        $requiredFiles = @(
            "requirements.txt",
            "resumen_escolar\app.py",
            "resumen_escolar\automation.py",
            "resumen_escolar\google_login.py",
            "resumen_escolar\daily_report.py",
            "resumen_escolar\recovery.py",
            "scripts\run_daily_report.sh",
            "scripts\run_weekly_prompt.sh",
            "scripts\create_readonly_par.sh",
            "scripts\test_schoolnet_vault_secret.sh",
            "scripts\ensure_chromium_cdp.sh"
        )
        foreach ($requiredFile in $requiredFiles) {
            $requiredPath = Join-Path $StageRoot $requiredFile
            if (-not (Test-Path -LiteralPath $requiredPath)) {
                throw "Falta archivo requerido en paquete de despliegue: $requiredFile"
            }
        }
    } finally {
        Pop-Location
    }
}

function New-DeployZip {
    if (Test-Path $ArchivePath) {
        Remove-Item $ArchivePath -Force
    }
    $tar = Get-Command tar -ErrorAction SilentlyContinue
    if (-not $tar) {
        throw "No encontre tar en Windows para crear el paquete de despliegue."
    }
    Push-Location $StageRoot
    try {
        & tar -czf $ArchivePath .
        if ($LASTEXITCODE -ne 0) {
            throw "tar fallo creando el paquete de despliegue."
        }
    } finally {
        Pop-Location
    }
}

$remoteScript = @"
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export APT_LISTCHANGES_FRONTEND=none
REMOTE_DIR='$RemoteDir'
REMOTE_ARCHIVE='$RemoteArchive'
BUCKET_NAME='$BucketName'
SCHOOLNET_SECRET_OCID='$SchoolNetSecretOcid'
REGION='$Region'
INSTALL_CRON='$([bool]$InstallCron)'
BOOTSTRAP_SYSTEM_PACKAGES='$([bool]$BootstrapSystemPackages)'
INSTALL_DEPENDENCIES='$([bool]$InstallDependencies)'

stage() { echo "==== STAGE: `$1 ===="; date -Is; }

stage "prepare directories"
sudo -n mkdir -p "`$REMOTE_DIR"
sudo -n chown ubuntu:ubuntu "`$REMOTE_DIR"
mkdir -p "`$REMOTE_DIR/config" "`$REMOTE_DIR/logs" "`$REMOTE_DIR/.runtime"

if [ -f "`$REMOTE_DIR/config/automation.env" ]; then
  cp "`$REMOTE_DIR/config/automation.env" "`$REMOTE_DIR/config/automation.env.bak-`$(date +%Y%m%d-%H%M%S)"
  cp "`$REMOTE_DIR/config/automation.env" "`$REMOTE_DIR/config/automation.env.bak-latest"
fi

stage "extract archive"
ROLLBACK_DIR="`$REMOTE_DIR/.runtime/deploy-backups"
mkdir -p "`$ROLLBACK_DIR"
ROLLBACK_ARCHIVE="`$ROLLBACK_DIR/code-`$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
(cd "`$REMOTE_DIR" && tar -czf "`$ROLLBACK_ARCHIVE" resumen_escolar scripts requirements.txt)
echo "rollback-created=true"
restore_previous_code() {
  local result=`$?
  if [ "`$result" -ne 0 ]; then
    (cd "`$REMOTE_DIR" && tar -xzf "`$ROLLBACK_ARCHIVE") && echo 'rollback-restored=true'
  fi
}
trap restore_previous_code EXIT
tar -xzf "`$REMOTE_ARCHIVE" -C "`$REMOTE_DIR"
test -f "`$REMOTE_DIR/resumen_escolar/automation.py" || {
  echo "No se encontro `$REMOTE_DIR/resumen_escolar/automation.py despues de extraer."
  find "`$REMOTE_DIR" -maxdepth 3 -type f | sed -n '1,120p'
  exit 3
}
chmod +x "`$REMOTE_DIR/scripts/run_daily_report.sh" "`$REMOTE_DIR/scripts/run_weekly_prompt.sh" "`$REMOTE_DIR/scripts/create_readonly_par.sh" "`$REMOTE_DIR/scripts/test_schoolnet_vault_secret.sh" "`$REMOTE_DIR/scripts/ensure_chromium_cdp.sh"

stage "verify system prerequisites"
missing_packages=()
for command in python3 tar curl unzip; do
  command -v "`$command" >/dev/null 2>&1 || missing_packages+=("`$command")
done
python3 -m venv --help >/dev/null 2>&1 || missing_packages+=("python3-venv")
if [ "`${#missing_packages[@]}" -gt 0 ]; then
  if [ "`$BOOTSTRAP_SYSTEM_PACKAGES" != "True" ]; then
    printf 'Faltan prerrequisitos del sistema: %s\n' "`${missing_packages[*]}"
    echo 'Reintenta solo la instalacion inicial con -BootstrapSystemPackages.'
    exit 4
  fi
  stage "apt install prerequisites (bootstrap)"
  sudo -n apt-get -o Dpkg::Use-Pty=0 -o Acquire::Retries=1 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update
  sudo -n apt-get -o Dpkg::Use-Pty=0 -o Acquire::Retries=1 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 install -y python3-pip python3-venv unzip ca-certificates curl
else
  echo "system-prerequisites-ok"
fi

if [ "`$INSTALL_DEPENDENCIES" = "True" ]; then
  stage "python venv and approved dependencies"
  python3 -m venv "`$REMOTE_DIR/.venv"
  "`$REMOTE_DIR/.venv/bin/python" -m pip install --progress-bar off --upgrade pip
  "`$REMOTE_DIR/.venv/bin/python" -m pip install --progress-bar off -r "`$REMOTE_DIR/requirements.txt" oci-cli
  "`$REMOTE_DIR/.venv/bin/python" -m playwright install chromium chromium-headless-shell
else
  stage "verify existing dependencies without installation"
  test -x "`$REMOTE_DIR/.venv/bin/python"
  test -x "`$REMOTE_DIR/.venv/bin/oci"
  "`$REMOTE_DIR/.venv/bin/python" -c 'import playwright'
fi

if [ "`$BOOTSTRAP_SYSTEM_PACKAGES" = "True" ]; then
  stage "playwright install system deps (bootstrap)"
  sudo -n "`$REMOTE_DIR/.venv/bin/python" -m playwright install-deps chromium chromium-headless-shell
else
  stage "verify playwright system deps"
  echo "playwright-system-deps-preserved"
fi

stage "write config"
get_existing_env() {
  local key="`$1"
  if [ -f "`$REMOTE_DIR/config/automation.env.bak-latest" ]; then
    grep -E "^`$key=" "`$REMOTE_DIR/config/automation.env.bak-latest" | tail -n 1 | cut -d= -f2- || true
  fi
}
EXISTING_SCHOOLNET_SECRET_OCID=""
if [ -z "`$SCHOOLNET_SECRET_OCID" ] && [ -f "`$REMOTE_DIR/config/automation.env.bak-latest" ]; then
  EXISTING_SCHOOLNET_SECRET_OCID="`$(get_existing_env RESUMEN_ESCOLAR_SCHOOLNET_SECRET_OCID)"
fi
if [ -z "`$SCHOOLNET_SECRET_OCID" ]; then
  SCHOOLNET_SECRET_OCID="`$EXISTING_SCHOOLNET_SECRET_OCID"
fi
EXISTING_OCI_NAMESPACE="`$(get_existing_env RESUMEN_ESCOLAR_OCI_NAMESPACE)"
EXISTING_NOTIFICATION_TOPIC_OCID="`$(get_existing_env RESUMEN_ESCOLAR_NOTIFICATION_TOPIC_OCID)"
EXISTING_BUCKET_NAME="`$(get_existing_env RESUMEN_ESCOLAR_BUCKET)"
if [ -z "`$BUCKET_NAME" ]; then
  BUCKET_NAME="`$EXISTING_BUCKET_NAME"
fi
if [ -f "`$REMOTE_DIR/config/automation.env" ]; then
  if [ -n "`$BUCKET_NAME" ] && [ "`$BUCKET_NAME" != "`$EXISTING_BUCKET_NAME" ]; then
    echo 'existing-config-differs-from-bucket-parameter; edit private config separately'
    exit 5
  fi
  if [ -n "`$SCHOOLNET_SECRET_OCID" ] && [ "`$SCHOOLNET_SECRET_OCID" != "`$EXISTING_SCHOOLNET_SECRET_OCID" ]; then
    echo 'existing-config-differs-from-schoolnet-secret-parameter; edit private config separately'
    exit 5
  fi
  echo 'config-preserved=true reason=bucket_not_provided_or_existing_config'
elif [ -z "`$BUCKET_NAME" ]; then
  echo 'config-preserved=true reason=bucket_not_provided'
  echo 'new deployment requires -BucketName and private configuration'
  exit 5
else
cat > "`$REMOTE_DIR/config/automation.env" <<EOF
RESUMEN_ESCOLAR_APP_DIR=$RemoteDir
RESUMEN_ESCOLAR_LOG_DIR=$RemoteDir/logs
RESUMEN_ESCOLAR_PYTHON=$RemoteDir/.venv/bin/python
RESUMEN_ESCOLAR_BROWSER_EXE=/home/ubuntu/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome
RESUMEN_ESCOLAR_PROFILE_DIR=$RemoteDir/.runtime/chrome-profile
RESUMEN_ESCOLAR_CDP_URL=http://127.0.0.1:9222
RESUMEN_ESCOLAR_HEADLESS=1
PLAYWRIGHT_BROWSERS_PATH=/home/ubuntu/.cache/ms-playwright
RESUMEN_ESCOLAR_BUCKET=$BucketName
RESUMEN_ESCOLAR_OCI_REGION=$Region
RESUMEN_ESCOLAR_OCI_NAMESPACE=`$EXISTING_OCI_NAMESPACE
RESUMEN_ESCOLAR_OCI_AUTH=instance_principal
RESUMEN_ESCOLAR_OCI_CLI=$RemoteDir/.venv/bin/oci
RESUMEN_ESCOLAR_DAILY_REPORT_LATEST_OBJECT=latest/daily_report.txt
RESUMEN_ESCOLAR_DAILY_REPORT_ARCHIVE_TEMPLATE=archive/{date}/daily_report.txt
RESUMEN_ESCOLAR_DAILY_STATE_LATEST_OBJECT=latest/daily_report_state.json
RESUMEN_ESCOLAR_DAILY_STATE_ARCHIVE_TEMPLATE=archive/{date}/daily_report_state.json
RESUMEN_ESCOLAR_ARCHIVE=1
RESUMEN_ESCOLAR_PAR_NAME=resumen-escolar-latest-readonly
RESUMEN_ESCOLAR_NOTIFICATION_TOPIC_OCID=`$EXISTING_NOTIFICATION_TOPIC_OCID
RESUMEN_ESCOLAR_SCHOOLNET_SECRET_OCID=`$SCHOOLNET_SECRET_OCID
RESUMEN_ESCOLAR_GOOGLE_SECRET_OCID=
EOF
chmod 600 "`$REMOTE_DIR/config/automation.env"
fi

if [ "`$INSTALL_CRON" = "True" ]; then
  stage "install cron"
  CRON_LINE="0 6,7 * * * RESUMEN_ESCOLAR_APP_DIR=$RemoteDir RESUMEN_ESCOLAR_CRON_GATE=1 RESUMEN_ESCOLAR_CRON_HOUR=03 $RemoteDir/scripts/run_daily_report.sh"
  TMP_CRON="`$(mktemp)"
  (crontab -l 2>/dev/null | grep -v 'run_weekly_prompt.sh' | grep -v 'run_daily_report.sh' | grep -v '^CRON_TZ=America/Santiago$' > "`$TMP_CRON") || true
  printf '%s\n' "`$CRON_LINE" >> "`$TMP_CRON"
  crontab "`$TMP_CRON"
  rm -f "`$TMP_CRON"
fi

stage "verify automation module"
cd "`$REMOTE_DIR"
export PYTHONPATH="`$REMOTE_DIR:`${PYTHONPATH:-}"
"`$REMOTE_DIR/.venv/bin/python" -m compileall -q "`$REMOTE_DIR/resumen_escolar"
"`$REMOTE_DIR/.venv/bin/python" -m resumen_escolar.automation --help >/dev/null

stage "verify Chromium/CDP configuration"
grep -qx 'RESUMEN_ESCOLAR_CDP_URL=http://127.0.0.1:9222' "`$REMOTE_DIR/config/automation.env"
grep -qx "RESUMEN_ESCOLAR_PROFILE_DIR=`$REMOTE_DIR/.runtime/chrome-profile" "`$REMOTE_DIR/config/automation.env"

stage "verify SchoolNet Vault access"
if [ -n "`$SCHOOLNET_SECRET_OCID" ]; then
  "`$REMOTE_DIR/scripts/test_schoolnet_vault_secret.sh"
else
  echo "schoolnet-vault-secret-configured=false"
  echo "schoolnet-auto-login-ready=false"
fi
echo "deploy-ok"
"@

Invoke-Logged {
    Copy-ProjectFiles
    New-DeployZip
    "ARCHIVE=$ArchivePath"
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & ssh @sshCommon $remote "mkdir -p /tmp && rm -f '$RemoteArchive'"
        if ($LASTEXITCODE -ne 0) { throw "ssh pre-clean failed" }
        & scp @sshCommon $ArchivePath "${remote}:$RemoteArchive"
        if ($LASTEXITCODE -ne 0) { throw "scp failed" }
        $remoteScript | & ssh @sshCommon $remote "bash -s 2>&1"
        if ($LASTEXITCODE -ne 0) { throw "remote deploy failed" }
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
}

$tempRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
if ([System.IO.Path]::GetFullPath($StageRoot).StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
if ([System.IO.Path]::GetFullPath($ArchivePath).StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $ArchivePath -Force -ErrorAction SilentlyContinue
}
