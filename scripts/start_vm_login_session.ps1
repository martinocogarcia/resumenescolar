param(
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [int]$LocalPort = 6080,
    [ValidateSet("Both", "SchoolNet", "Classroom")]
    [string]$StartMode = "Both",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-vm-login-session.log"
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

function Write-Stage {
    param([int]$Number, [string]$Text)
    Write-Output ("[{0}/10] {1}" -f $Number, $Text)
}

$remoteScript = @'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export APT_LISTCHANGES_FRONTEND=none

APP_DIR=/opt/resumen-escolar
SESSION_DIR="$APP_DIR/.runtime/login-session"
LOG_DIR="$APP_DIR/logs/login-session"
PROFILE_DIR="$APP_DIR/.runtime/chrome-profile"
CDP_READY=false
NOVNC_READY=false
curl -fsS --max-time 3 http://127.0.0.1:9222/json/version >/dev/null 2>&1 && CDP_READY=true
curl -fsS --max-time 3 http://127.0.0.1:6080/vnc.html >/dev/null 2>&1 && NOVNC_READY=true
if [ "$CDP_READY" = true ] && [ "$NOVNC_READY" = true ]; then
  echo "REMOTE_LOGIN_SESSION_REUSED=true"
  exit 0
fi
if [ "$CDP_READY" = true ] || [ "$NOVNC_READY" = true ]; then
  echo "REMOTE_ERROR=partial_existing_session; preserve_browser_and_profile"
  exit 2
fi
if [ -e "$PROFILE_DIR/SingletonLock" ]; then
  echo "REMOTE_ERROR=profile_busy_without_cdp; preserve_browser_and_profile"
  exit 2
fi
DISPLAY_NUM=""
for candidate in $(seq 99 109); do
  if [ ! -e "/tmp/.X${candidate}-lock" ] && [ ! -S "/tmp/.X11-unix/X${candidate}" ]; then
    DISPLAY_NUM="$candidate"
    break
  fi
done
if [ -z "$DISPLAY_NUM" ]; then
  echo "REMOTE_ERROR=no_free_x_display"
  exit 2
fi
DISPLAY_VALUE=":$DISPLAY_NUM"
NOVNC_PORT=6080
VNC_PORT=5901
START_MODE="${START_MODE:-Both}"

mkdir -p "$SESSION_DIR" "$LOG_DIR" "$PROFILE_DIR"

echo "REMOTE_STAGE=check_packages"
if command -v x11vnc >/dev/null 2>&1 && command -v websockify >/dev/null 2>&1 && command -v fluxbox >/dev/null 2>&1 && [ -d /usr/share/novnc ]; then
  echo "REMOTE_PACKAGES_READY=true"
else
  echo "REMOTE_STAGE=install_packages"
  sudo -n apt-get -o Dpkg::Use-Pty=0 update
  sudo -n apt-get -o Dpkg::Use-Pty=0 install -y x11vnc novnc websockify fluxbox
fi

echo "REMOTE_STAGE=start_xvfb"
Xvfb "$DISPLAY_VALUE" -screen 0 1600x1000x24 -ac -nolisten tcp >"$LOG_DIR/xvfb.log" 2>&1 &
echo $! > "$SESSION_DIR/xvfb.pid"
sleep 1

echo "REMOTE_STAGE=start_window_manager"
DISPLAY="$DISPLAY_VALUE" fluxbox >"$LOG_DIR/fluxbox.log" 2>&1 &
echo $! > "$SESSION_DIR/fluxbox.pid"
sleep 1

echo "REMOTE_STAGE=start_x11vnc"
x11vnc -display "$DISPLAY_VALUE" \
  -localhost \
  -forever \
  -shared \
  -rfbport "$VNC_PORT" \
  -nopw \
  -xkb \
  -repeat \
  -noxrecord \
  -noxfixes \
  -noxdamage \
  -quiet \
  >"$LOG_DIR/x11vnc.log" 2>&1 &
echo $! > "$SESSION_DIR/x11vnc.pid"
sleep 1

echo "REMOTE_STAGE=start_novnc"
websockify --web=/usr/share/novnc "127.0.0.1:$NOVNC_PORT" "127.0.0.1:$VNC_PORT" >"$LOG_DIR/websockify.log" 2>&1 &
echo $! > "$SESSION_DIR/websockify.pid"
sleep 1

CHROME=/home/ubuntu/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome
if [ ! -x "$CHROME" ]; then
  echo "REMOTE_ERROR=chromium_not_found"
  exit 3
fi

echo "REMOTE_STAGE=start_chrome"
START_URLS=()
case "$START_MODE" in
  SchoolNet)
    START_URLS=("https://schoolnet.colegium.com/webapp/es_CL/login")
    ;;
  Classroom)
    START_URLS=("https://classroom.google.com/")
    ;;
  *)
    START_URLS=("https://schoolnet.colegium.com/webapp/es_CL/login" "https://classroom.google.com/")
    ;;
esac
DISPLAY="$DISPLAY_VALUE" "$CHROME" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --no-sandbox \
  --password-store=basic \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --new-window \
  "${START_URLS[@]}" \
  >"$LOG_DIR/chrome.log" 2>&1 &
echo $! > "$SESSION_DIR/chrome.pid"

sleep 3
echo "REMOTE_STAGE=health"
for name in xvfb fluxbox x11vnc websockify chrome; do
  pid="$(cat "$SESSION_DIR/$name.pid" 2>/dev/null || true)"
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    echo "REMOTE_ERROR=${name}_not_running"
    tail -80 "$LOG_DIR/$name.log" 2>/dev/null || true
    exit 4
  fi
done

echo "REMOTE_LOGIN_SESSION_READY=true"
echo "REMOTE_NOVNC=127.0.0.1:$NOVNC_PORT"
echo "REMOTE_PROFILE_DIR=$PROFILE_DIR"
'@

$startedAt = Get-Date
$exitCode = 1
$transcriptStarted = $false

try {
    Start-Transcript -LiteralPath $LogPath -Append | Out-Null
    $transcriptStarted = $true
    Write-Stage 1 "Inicio: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
        throw "No se encontro la llave SSH configurada. Revisa la ruta en KeyPath."
    }

    Write-Stage 2 "Validando llave SSH local"
    Write-Stage 3 "Conectando a la VM $remote (timeout: 15 s)"
    $remoteScript | & ssh @sshArgs $remote "START_MODE='$StartMode' bash -s 2>&1"
    if ($LASTEXITCODE -ne 0) {
        throw "La preparacion remota fallo (ssh/VM). Revisa el log: $LogPath"
    }

    Write-Stage 4 "Sesion grafica remota disponible"
    Write-Stage 5 "Chromium conserva el perfil persistente"
    Write-Stage 6 "Servicio noVNC remoto listo"
    $localReady = $false
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -fsS --max-time 3 -o NUL "http://127.0.0.1:$LocalPort/vnc.html" 2>$null
        $localReady = ($LASTEXITCODE -eq 0)
    }
    if ($localReady) {
        Write-Stage 7 "Tunel local existente reutilizado"
        Write-Stage 8 "No se reemplazo ningun proceso local"
        Write-Stage 9 "Tunel activo"
    } else {
        Write-Stage 7 "No hay tunel local activo"
        Write-Stage 8 "Abriendo tunel local hacia noVNC"
        $startArgs = @(
            "-i", ('"{0}"' -f $KeyPath),
            "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15",
            "-o", ('UserKnownHostsFile="{0}"' -f $knownHostsPath),
            "-o", "StrictHostKeyChecking=accept-new",
            "-L", "${LocalPort}:127.0.0.1:6080",
            "-N",
            $remote
        )
        $proc = Start-Process -FilePath "ssh.exe" -ArgumentList $startArgs -WindowStyle Hidden -PassThru
        Set-Content -LiteralPath $tunnelPidPath -Value $proc.Id -Encoding ascii
        Start-Sleep -Seconds 2
        if ($proc.HasExited) {
            throw "El tunel SSH local se cerro de inmediato. Revisa el log: $LogPath"
        }
        Write-Stage 9 "Tunel activo (PID $($proc.Id))"
    }
    $noVncUrl = "http://127.0.0.1:$LocalPort/vnc.html?host=127.0.0.1&port=$LocalPort"
    Write-Stage 9 "Tunel activo (PID $($proc.Id))"
    Write-Stage 10 "Abre esta URL en Chrome o Edge: $noVncUrl"
    Write-Output "RESULTADO: OK - Inicia sesion en SchoolNet y deja abierta la ventana hasta que termine el cron."
    $exitCode = 0
}
catch {
    Write-Output "RESULTADO: FALLO - $($_.Exception.Message)"
    Write-Output "LOG: $LogPath"
}
finally {
    $elapsed = [Math]::Round(((Get-Date) - $startedAt).TotalSeconds, 1)
    Write-Output "END $(Get-Date -Format o) duration_seconds=$elapsed exit=$exitCode"
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}

exit $exitCode
