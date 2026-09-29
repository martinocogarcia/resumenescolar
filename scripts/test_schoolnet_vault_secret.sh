#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${RESUMEN_ESCOLAR_APP_DIR:-/opt/resumen-escolar}"
CONFIG_FILE="${RESUMEN_ESCOLAR_CONFIG_FILE:-$APP_DIR/config/automation.env}"
PYTHON_BIN="${RESUMEN_ESCOLAR_PYTHON:-python3}"

if [[ -f "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  set +a
fi

export PYTHONPATH="$APP_DIR:$APP_DIR/.runtime/site-packages:${PYTHONPATH:-}"
cd "$APP_DIR"

"$PYTHON_BIN" - <<'PY'
from argparse import Namespace
import os

from resumen_escolar.automation import load_schoolnet_secret

args = Namespace(
    schoolnet_secret_ocid=os.environ.get("RESUMEN_ESCOLAR_SCHOOLNET_SECRET_OCID", ""),
    oci_auth=os.environ.get("RESUMEN_ESCOLAR_OCI_AUTH", "instance_principal"),
    oci_profile=os.environ.get("RESUMEN_ESCOLAR_OCI_PROFILE", ""),
    oci_config_file=os.environ.get("RESUMEN_ESCOLAR_OCI_CONFIG_FILE", ""),
    oci_region=os.environ.get("RESUMEN_ESCOLAR_OCI_REGION", ""),
)

secret = load_schoolnet_secret(args)
if not secret:
    raise SystemExit("missing RESUMEN_ESCOLAR_SCHOOLNET_SECRET_OCID")
print("ok=true")
print("username_present=true")
print("password_present=true")
PY
