#!/usr/bin/env bash
set -u

APP_DIR="/opt/resumen-escolar"
PROFILE_DIR="$APP_DIR/.runtime/chrome-profile"
BROWSER="/home/ubuntu/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome"
CDP_PORT="9222"

pid="$(pgrep -f -- "$BROWSER --user-data-dir=$PROFILE_DIR" | head -n 1 || true)"
display=""
if [[ -n "$pid" && -r "/proc/$pid/environ" ]]; then
  display="$(tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's/^DISPLAY=//p' | head -n 1)"
  kill -TERM "$pid" || true
  for _ in {1..15}; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
fi
export DISPLAY="${display:-:0}"

nohup "$BROWSER" \
  --user-data-dir="$PROFILE_DIR" \
  --headless=new \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$CDP_PORT" \
  --no-first-run --no-default-browser-check --disable-dev-shm-usage \
  --no-sandbox --password-store=basic --new-window \
  https://classroom.google.com/ >/tmp/resumen-escolar-chromium-cdp.log 2>&1 &

sleep 5
if ! ss -ltn 2>/dev/null | grep -q ":$CDP_PORT "; then
  echo "Chromium no abrio CDP en $CDP_PORT"
  tail -n 40 /tmp/resumen-escolar-chromium-cdp.log || true
  exit 2
fi

if ! grep -q '^RESUMEN_ESCOLAR_CDP_URL=' "$APP_DIR/config/automation.env"; then
  echo "RESUMEN_ESCOLAR_CDP_URL=http://127.0.0.1:$CDP_PORT" >> "$APP_DIR/config/automation.env"
fi
export RESUMEN_ESCOLAR_CDP_URL="http://127.0.0.1:$CDP_PORT"

cd "$APP_DIR"
if [[ -f "$APP_DIR/config/automation.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$APP_DIR/config/automation.env"
  set +a
fi
export RESUMEN_ESCOLAR_CDP_URL="http://127.0.0.1:$CDP_PORT"
set +e
./.venv/bin/python -m resumen_escolar.automation run --publish
code=$?
echo "---RUNNER_EXIT=$code---"
tail -n 120 "logs/daily-report-$(date +%F).log" || true
exit "$code"
