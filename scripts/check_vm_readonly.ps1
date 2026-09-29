param(
    [string]$HostName = "129.153.51.108",
    [string]$User = "ubuntu",
    [string]$KeyPath = "",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-vm-readonly-check.log"
)

$ErrorActionPreference = "Continue"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot

if (-not $KeyPath) {
    if ($HostName -eq "129.153.51.108") {
        $KeyPath = Join-Path $HOME ".ssh\oci_original.key"
    } elseif ($HostName -eq "40.233.127.100") {
        $projectDeployKey = Join-Path $ProjectRoot ".oci-deploy\worldcup2026_ssh"
        if (Test-Path -Path $projectDeployKey) {
            $KeyPath = $projectDeployKey
        } else {
            $KeyPath = Join-Path $HOME ".ssh\id_rsa"
        }
    } else {
        $KeyPath = Join-Path $HOME ".ssh\id_rsa"
    }
}

$sshArgs = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=8",
    "-o", "UserKnownHostsFile=$env:TEMP\codex_known_hosts_resumen_escolar",
    "-o", "StrictHostKeyChecking=accept-new"
)

if ($KeyPath) {
    $sshArgs += @("-i", $KeyPath)
}

$remote = @'
printf "host="; hostname
printf "user="; id -un
printf "pwd="; pwd
printf "os="; uname -srm
printf "python="; python3 --version 2>&1 || true
printf "chrome="; command -v google-chrome || command -v chromium-browser || command -v chromium || true
printf "oci="; oci --version 2>&1 || true
printf "docker="; docker --version 2>&1 || true
printf "cron="; systemctl is-active cron 2>&1 || systemctl is-active crond 2>&1 || true
'@

& {
    "START $(Get-Date -Format o)"
    "TARGET=$User@$HostName"
    "KEY_PATH_PROVIDED=$([bool]$KeyPath)"
    "KEY_PATH_SELECTED=$KeyPath"
    ssh @sshArgs "$User@$HostName" $remote
    "END $(Get-Date -Format o)"
    "exit=$LASTEXITCODE"
} *> $LogPath

exit $LASTEXITCODE
