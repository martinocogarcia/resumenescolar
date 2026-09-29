param(
    [string]$HostName = "129.153.51.108",
    [string]$User = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-vm-prereqs.log"
)

$ErrorActionPreference = "Continue"

$sshArgs = @(
    "-i", $KeyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=8",
    "-o", "UserKnownHostsFile=$env:TEMP\codex_known_hosts_resumen_escolar",
    "-o", "StrictHostKeyChecking=accept-new"
)

$remote = @'
set +e
echo "host=$(hostname)"
echo "user=$(id -un)"
echo "kernel=$(uname -srm)"
echo "os_release=$(grep -E '^(NAME|VERSION)=' /etc/os-release | tr '\n' ' ')"
echo "pwd=$(pwd)"
echo "disk_root=$(df -h / | tail -1)"
echo "disk_opt=$(df -h /opt 2>/dev/null | tail -1)"
echo "python3=$(python3 --version 2>&1)"
echo "python3_path=$(command -v python3 || true)"
echo "pip3=$(python3 -m pip --version 2>&1)"
echo "venv_module=$(python3 - <<'PY' 2>&1
import importlib.util
print('yes' if importlib.util.find_spec('venv') else 'no')
PY
)"
echo "chrome_path=$(command -v google-chrome || command -v google-chrome-stable || command -v chromium-browser || command -v chromium || true)"
echo "oci_cli=$(command -v oci || true)"
echo "docker=$(docker --version 2>&1 || true)"
echo "docker_compose=$(docker compose version 2>&1 || true)"
echo "cron=$(systemctl is-active cron 2>&1 || systemctl is-active crond 2>&1 || true)"
echo "oracle_form_dir=$(test -d /opt/oracle-form-app && echo yes || echo no)"
echo "resumen_dir=$(test -d /opt/resumen-escolar && echo yes || echo no)"
echo "metadata_region=$(curl -fsS -H 'Authorization: Bearer Oracle' --connect-timeout 2 --max-time 5 http://169.254.169.254/opc/v2/instance/region 2>&1 || true)"
echo "metadata_instance=$(curl -fsS -H 'Authorization: Bearer Oracle' --connect-timeout 2 --max-time 5 http://169.254.169.254/opc/v2/instance/displayName 2>&1 || true)"
echo "apt_chrome=$(apt-cache policy google-chrome-stable chromium-browser chromium 2>/dev/null | sed -n '1,40p')"
echo "apt_oci=$(apt-cache policy python3-pip python3-venv unzip rsync jq 2>/dev/null | sed -n '1,80p')"
'@

& {
    "START $(Get-Date -Format o)"
    "TARGET=$User@$HostName"
    "KEY_PATH_SELECTED=$KeyPath"
    ssh @sshArgs "$User@$HostName" $remote
    "END $(Get-Date -Format o)"
    "exit=$LASTEXITCODE"
} *> $LogPath

exit $LASTEXITCODE
