#!/usr/bin/env bash
# Ensure one safe Chromium owner exposes the persisted SchoolNet session over CDP.
set -euo pipefail

APP_DIR="${RESUMEN_ESCOLAR_APP_DIR:-/opt/resumen-escolar}"
CONFIG_FILE="${RESUMEN_ESCOLAR_CONFIG_FILE:-$APP_DIR/config/automation.env}"
if [[ -f "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  set +a
fi

PROFILE_DIR="${RESUMEN_ESCOLAR_PROFILE_DIR:-$APP_DIR/.runtime/chrome-profile}"
BROWSER="${RESUMEN_ESCOLAR_BROWSER_EXE:-/home/ubuntu/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome}"
CDP_URL="${RESUMEN_ESCOLAR_CDP_URL:-http://127.0.0.1:9222}"
CDP_PORT="${CDP_URL##*:}"
LOG_DIR="${RESUMEN_ESCOLAR_LOG_DIR:-$APP_DIR/logs}"
LOCK_FILE="${RESUMEN_ESCOLAR_BROWSER_LOCK_FILE:-$APP_DIR/.runtime/chromium-cdp.lock}"

mkdir -p "$PROFILE_DIR" "$LOG_DIR" "$(dirname "$LOCK_FILE")"
chmod 700 "$PROFILE_DIR"

cdp_ready() {
  curl -fsS --max-time 3 "$CDP_URL/json/version" >/dev/null 2>&1
}

ensure_once() {
  if cdp_ready; then
    echo "BROWSER_CDP_READY=true source=existing"
    return 0
  fi

  if ! [[ -x "$BROWSER" ]]; then
    echo "BROWSER_CDP_ERROR=browser_executable_not_found"
    return 20
  fi

  if pgrep -f -- "--user-data-dir=$PROFILE_DIR" >/dev/null 2>&1; then
    echo "BROWSER_CDP_ERROR=browser_profile_busy_without_cdp"
    echo "BROWSER_CDP_ACTION=do_not_kill_existing_browser"
    return 21
  fi

  echo "BROWSER_CDP_START=true mode=headless profile=reused"
  nohup "$BROWSER" \
    --user-data-dir="$PROFILE_DIR" \
    --headless=new \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="$CDP_PORT" \
    --no-first-run --no-default-browser-check --disable-dev-shm-usage \
    --no-sandbox --password-store=basic --new-window \
    https://schoolnet.colegium.com/webapp/es_CL/login \
    >"$LOG_DIR/chromium-cdp.log" 2>&1 &

  for _ in {1..12}; do
    if cdp_ready; then
      echo "BROWSER_CDP_READY=true source=started"
      return 0
    fi
    sleep 1
  done
  echo "BROWSER_CDP_ERROR=start_failed"
  tail -n 40 "$LOG_DIR/chromium-cdp.log" 2>/dev/null || true
  return 22
}

if command -v flock >/dev/null 2>&1; then
  (
    flock -n 9 || { echo "BROWSER_CDP_ERROR=guard_lock_busy"; exit 23; }
    ensure_once
  ) 9>"$LOCK_FILE"
else
  ensure_once
fi
