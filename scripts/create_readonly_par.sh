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

: "${RESUMEN_ESCOLAR_BUCKET:?Falta RESUMEN_ESCOLAR_BUCKET}"
: "${RESUMEN_ESCOLAR_OCI_REGION:?Falta RESUMEN_ESCOLAR_OCI_REGION para construir la URL final}"

AUTH="${RESUMEN_ESCOLAR_OCI_AUTH:-instance_principal}"
OCI_CLI="${RESUMEN_ESCOLAR_OCI_CLI:-oci}"
OBJECT_NAME="${RESUMEN_ESCOLAR_LATEST_OBJECT:-latest/prompt_chatgpt.txt}"
PAR_NAME="${RESUMEN_ESCOLAR_PAR_NAME:-resumen-escolar-latest-readonly}"
EXPIRES="${RESUMEN_ESCOLAR_PAR_EXPIRES:-$(date -u -d '+365 days' '+%Y-%m-%dT%H:%M:%SZ')}"

cmd=("$OCI_CLI" --auth "$AUTH" os preauth-request create
  --bucket-name "$RESUMEN_ESCOLAR_BUCKET"
  --name "$PAR_NAME"
  --access-type ObjectRead
  --object-name "$OBJECT_NAME"
  --time-expires "$EXPIRES")

if [[ -n "${RESUMEN_ESCOLAR_OCI_NAMESPACE:-}" ]]; then
  cmd+=(--namespace "$RESUMEN_ESCOLAR_OCI_NAMESPACE")
fi

response="$("${cmd[@]}")"
access_uri="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["access-uri"])' <<<"$response")"
printf 'https://objectstorage.%s.oraclecloud.com%s\n' "$RESUMEN_ESCOLAR_OCI_REGION" "$access_uri"
