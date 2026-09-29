param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-vm-login-session-stop.log"
)

$ErrorActionPreference = "Continue"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$tunnelPidPath = Join-Path $projectRoot "codex-vm-login-tunnel.pid"
$knownHostsPath = Join-Path $env:TEMP "codex_known_hosts_resumen_escolar"

$sshArgs = @(
    "-i", $KeyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "UserKnownHostsFile=$knownHostsPath",
    "-o", "StrictHostKeyChecking=accept-new"
)

$remote = "${VmUser}@${VmHost}"

$remoteScript = @'
set -euo pipefail
SESSION_DIR=/opt/resumen-escolar/.runtime/login-session
if [ -d "$SESSION_DIR" ]; then
  for pidfile in "$SESSION_DIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  rm -f "$SESSION_DIR"/*.pid
fi
echo "REMOTE_LOGIN_SESSION_STOPPED=true"
'@

& {
    "START $(Get-Date -Format o)"
    if (Test-Path -LiteralPath $tunnelPidPath) {
        $pidText = (Get-Content -LiteralPath $tunnelPidPath -Raw).Trim()
        if ($pidText -match '^\d+$') {
            Stop-Process -Id ([int]$pidText) -Force -ErrorAction SilentlyContinue
            "LOCAL_TUNNEL_STOPPED=$pidText"
        }
        Remove-Item -LiteralPath $tunnelPidPath -Force -ErrorAction SilentlyContinue
    } else {
        "LOCAL_TUNNEL_STOPPED=none"
    }
    $remoteScript | & ssh @sshArgs $remote "bash -s"
    "END $(Get-Date -Format o)"
    "exit=$LASTEXITCODE"
} *> $LogPath

exit $LASTEXITCODE
