#!/usr/bin/env bash
# Wrapper compatible con despliegues antiguos. El flujo ahora es diario.
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$SCRIPT_DIR/run_daily_report.sh" "$@"
