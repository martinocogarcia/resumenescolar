#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${RESUMEN_ESCOLAR_APP_DIR:-/opt/resumen-escolar}"
CONFIG_FILE="${RESUMEN_ESCOLAR_CONFIG_FILE:-$APP_DIR/config/automation.env}"
if [[ -f "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  set +a
fi
APP_DIR="${RESUMEN_ESCOLAR_APP_DIR:-$APP_DIR}"
LOG_DIR="${RESUMEN_ESCOLAR_LOG_DIR:-$APP_DIR/logs}"
LOCK_FILE="${RESUMEN_ESCOLAR_LOCK_FILE:-$APP_DIR/.runtime/daily-report.lock}"
PYTHON_BIN="${RESUMEN_ESCOLAR_PYTHON:-$APP_DIR/.venv/bin/python}"
REPORT_TZ="${RESUMEN_ESCOLAR_TIMEZONE:-America/Santiago}"
CRON_GATE="${RESUMEN_ESCOLAR_CRON_GATE:-0}"
CRON_HOUR="${RESUMEN_ESCOLAR_CRON_HOUR:-03}"
BROWSER_GUARD="${RESUMEN_ESCOLAR_BROWSER_GUARD:-$APP_DIR/scripts/ensure_chromium_cdp.sh}"
mkdir -p "$LOG_DIR" "$(dirname "$LOCK_FILE")"
export TZ="$REPORT_TZ"
if [[ "$CRON_GATE" = "1" && "$(date +%H)" != "$CRON_HOUR" ]]; then
  echo "[$(date -Is)] cron omitido: hora local $(date +%H), programada $CRON_HOUR ($REPORT_TZ)"
  exit 0
fi
LOG_FILE="$LOG_DIR/daily-report-$(date +%F).log"
exec >>"$LOG_FILE" 2>&1
echo "[$(date -Is)] [1/10] inicio reporte diario"
echo "[$(date -Is)] [2/10] app_dir=$APP_DIR"
echo "[$(date -Is)] [3/10] python=$PYTHON_BIN"
export PYTHONPATH="$APP_DIR:$APP_DIR/.runtime/site-packages:${PYTHONPATH:-}"
cd "$APP_DIR"

run_once() {
  echo "[$(date -Is)] [4/10] verificando Chromium/CDP"
  local guard_output
  if guard_output="$("$BROWSER_GUARD" 2>&1)"; then
    [[ -z "$guard_output" ]] || echo "$guard_output"
  else
    echo "[$(date -Is)] Chromium/CDP guard fallo: $guard_output"
    echo "[$(date -Is)] publicando alerta clasificada de navegador"
    "$PYTHON_BIN" -u -m resumen_escolar.automation notify-failure \
      --error "Chromium/CDP guard failed: ${guard_output:0:500}" \
      || echo "[$(date -Is)] no se pudo publicar alerta OCI para el fallo de navegador"
    return 70
  fi
  echo "[$(date -Is)] [5/10] Chromium/CDP listo"
  echo "[$(date -Is)] [6/10] iniciando extraccion, recuperacion SchoolNet y publicacion"
  "$PYTHON_BIN" -u -m resumen_escolar.automation run --publish
}

run_with_heartbeat() {
  local started_at elapsed_seconds exit_code warned_at_25m=false
  started_at="$(date +%s)"
  run_once &
  local runner_pid=$!
  echo "[$(date -Is)] [7/10] runner_pid=$runner_pid"

  while kill -0 "$runner_pid" 2>/dev/null; do
    sleep 15
    if ! kill -0 "$runner_pid" 2>/dev/null; then
      break
    fi
    elapsed_seconds=$(( $(date +%s) - started_at ))
    if (( elapsed_seconds % 60 < 15 )); then
      echo "[$(date -Is)] [8/10] en progreso: runner activo, elapsed_seconds=$elapsed_seconds"
    fi
    if [[ "$warned_at_25m" = false && "$elapsed_seconds" -ge 1500 ]]; then
      echo "[$(date -Is)] [9/10] AVISO 25 MINUTOS: la ejecucion sigue activa. ACCION: NO cierres Chrome/noVNC y NO interrumpas el cron; continua esperando los siguientes latidos. Solo intervenir si dejan de aparecer latidos durante 2 minutos."
      warned_at_25m=true
    fi
  done

  if wait "$runner_pid"; then
    exit_code=0
  else
    exit_code=$?
  fi
  elapsed_seconds=$(( $(date +%s) - started_at ))
  echo "[$(date -Is)] [10/10] runner terminado: exit=$exit_code elapsed_seconds=$elapsed_seconds"
  return "$exit_code"
}

if command -v flock >/dev/null 2>&1; then
  ( flock -n 9 || { echo "[$(date -Is)] ya hay una ejecucion activa"; exit 75; }; run_with_heartbeat ) 9>"$LOCK_FILE"
else
  run_with_heartbeat
fi
echo "[$(date -Is)] terminado reporte diario"
