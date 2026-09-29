from __future__ import annotations

import argparse
import base64
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .app import (
    AppError,
    EVIDENCE_CACHE_DIR,
    OUTBOX_DIR,
    current_report_date,
    generate_prompt_once,
    schoolnet_existing_canonical_p1,
    single_line,
)
from .daily_report import DEFAULT_REPORT_MAX_BYTES, build_state, render_email_message, render_report


DEFAULT_LATEST_OBJECT = "latest/prompt_chatgpt.txt"
DEFAULT_ARCHIVE_TEMPLATE = "archive/{date}/prompt_chatgpt.txt"
DEFAULT_MATERIALS_LATEST_PREFIX = "latest/materials"
DEFAULT_MATERIALS_ARCHIVE_TEMPLATE = "archive/{date}/materials"
DEFAULT_INCREMENTAL_EMAIL_MAX_CHARS = 45000
DEFAULT_DAILY_REPORT_LATEST_OBJECT = "latest/daily_report.txt"
DEFAULT_DAILY_REPORT_ARCHIVE_TEMPLATE = "archive/{date}/daily_report.txt"
DEFAULT_DAILY_STATE_LATEST_OBJECT = "latest/daily_report_state.json"
DEFAULT_DAILY_STATE_ARCHIVE_TEMPLATE = "archive/{date}/daily_report_state.json"
DAILY_STATE_PATH = EVIDENCE_CACHE_DIR / "daily_report_state.json"
SCHOOLNET_USERNAME_ENV = "RESUMEN_ESCOLAR_SCHOOLNET_USERNAME"
SCHOOLNET_PASSWORD_ENV = "RESUMEN_ESCOLAR_SCHOOLNET_PASSWORD"
GOOGLE_USERNAME_ENV = "RESUMEN_ESCOLAR_GOOGLE_USERNAME"
GOOGLE_PASSWORD_ENV = "RESUMEN_ESCOLAR_GOOGLE_PASSWORD"
NOTIFICATION_TOPIC_ENV = "RESUMEN_ESCOLAR_NOTIFICATION_TOPIC_OCID"
LATEST_TXT_URL_ENV = "RESUMEN_ESCOLAR_LATEST_TXT_URL"
FAILURE_NOTIFICATIONS_ENV = "RESUMEN_ESCOLAR_NOTIFY_FAILURES"
LOGIN_SCRIPT_PATH = "C:\\Users\\Martin\\Documents\\Codex\\resumen_escolar\\scripts\\start_vm_login_session.ps1"
LOGIN_LOG_PATH = "C:\\Users\\Martin\\Documents\\Codex\\resumen_escolar\\codex-vm-login-session.log"


def login_command(start_mode: str) -> str:
    """Return the safe local recovery command shown in failure notifications."""
    return (
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
        f'"try {{ & \'{LOGIN_SCRIPT_PATH}\' -StartMode {start_mode} }} '
        'finally { [System.Media.SystemSounds]::Asterisk.Play() }"'
    )


CLASSROOM_LOGIN_COMMAND = login_command("Classroom")
SCHOOLNET_LOGIN_COMMAND = login_command("SchoolNet")
LOGIN_BOTH_COMMAND = login_command("Both")
SENSITIVE_PATTERNS = (
    (re.compile(r"ocid1\.[A-Za-z0-9._-]+"), "[OCID_REDACTED]"),
    (re.compile(r"[A-Fa-f0-9]{2}(:[A-Fa-f0-9]{2}){15,}"), "[FINGERPRINT_REDACTED]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL_REDACTED]"),
    (re.compile(r"C:\\Users\\Martin\\[^\s\r\n]+"), "[PATH_REDACTED]"),
)


def schoolnet_retry_delays() -> list[int]:
    """Return six retry slots spread across a one-hour night window."""
    return [0, 600, 600, 600, 600, 600]


def _safe_diagnostic_value(value: Any) -> str:
    text = single_line(str(value or ""))
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL_REDACTED]", text)
    # Conserva la etapa tecnica (por ejemplo, campos usuario/clave ausentes),
    # pero nunca reenvia un rechazo de clave ni valores tipo password=... .
    if re.search(
        r"(?:clave|password|contrasena|contraseña)\s*(?:=|:)|(?:clave|password|contrasena|contraseña)\s+(?:incorrecta|invalida|incorrecto|invalido)",
        text,
        re.IGNORECASE,
    ):
        return "[MESSAGE_REDACTED]"
    return redact(text)[:180]


def format_login_diagnostics(
    *,
    run_id: str,
    attempts: list[dict[str, Any]],
    vault_status: str,
    cdp_status: str,
    profile_status: str,
    final_reason: str,
) -> str:
    """Create a copy/paste-safe operational block for OCI notifications."""
    lines = [
        f"RUN_ID: {_safe_diagnostic_value(run_id)}",
        "PLATFORM: SchoolNet",
        "RESULT: failed",
        f"ATTEMPTS: {len(attempts)}",
        f"VAULT: {_safe_diagnostic_value(vault_status)}",
        f"CDP: {_safe_diagnostic_value(cdp_status)}",
        f"PROFILE: {_safe_diagnostic_value(profile_status)}",
        f"FINAL_REASON: {_safe_diagnostic_value(final_reason)}",
    ]
    for item in attempts:
        attempt = _safe_diagnostic_value(item.get("attempt"))
        stage = _safe_diagnostic_value(item.get("stage"))
        result = _safe_diagnostic_value(item.get("result"))
        details = [f"{key}={_safe_diagnostic_value(value)}" for key, value in item.items() if key not in {"attempt", "stage", "result"}]
        suffix = " " + " ".join(details) if details else ""
        lines.append(f"ATTEMPT_{attempt}: stage={stage} result={result}{suffix}")
    return "\n".join(lines)


def redact(text: str) -> str:
    output = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        output = pattern.sub(replacement, output)
    return output


def prompt_path_for_today() -> Path:
    return OUTBOX_DIR / current_report_date().isoformat() / "prompt_chatgpt.txt"


def verify_prompt(path: Path, min_bytes: int = 1) -> dict[str, Any]:
    if not path.exists():
        raise AppError(f"No existe el prompt esperado: {path}")
    size = path.stat().st_size
    if size < min_bytes:
        raise AppError(f"El prompt existe, pero es demasiado pequeno: {size} bytes.")
    text = path.read_text(encoding="utf-8", errors="replace")
    canonical_p1 = schoolnet_existing_canonical_p1(text)
    canonical_p1_rows = [
        line
        for line in canonical_p1.splitlines()
        if " | " in line and not line.startswith(("Fuente ", "Regla ", "Si ", "Formato:"))
    ]
    min_p1_rows = int(os.environ.get("RESUMEN_ESCOLAR_MIN_P1_ROWS", "10"))
    if len(canonical_p1_rows) < min_p1_rows:
        raise AppError(
            "El prompt no tiene suficientes filas canonicas P1 de SchoolNet: "
            f"{len(canonical_p1_rows)} encontradas, {min_p1_rows} requeridas. "
            "No se publica para evitar calificaciones inventadas."
        )
    return {
        "path": str(path),
        "bytes": size,
        "canonical_p1_rows": len(canonical_p1_rows),
    }


def oci_global_args(args: argparse.Namespace) -> list[str]:
    command = [find_oci_cli()]
    if args.oci_auth:
        command.extend(["--auth", args.oci_auth])
    if args.oci_profile:
        command.extend(["--profile", args.oci_profile])
    if args.oci_config_file:
        command.extend(["--config-file", args.oci_config_file])
    if args.oci_region:
        command.extend(["--region", args.oci_region])
    return command


def find_oci_cli() -> str:
    configured = os.environ.get("RESUMEN_ESCOLAR_OCI_CLI", "").strip()
    candidates = [
        configured,
        str(Path(sys.executable).with_name("oci")),
        shutil.which("oci") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return configured or "oci"


def run_oci_capture(command: list[str]) -> str:
    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore")
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=env,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise AppError(f"No se encontro OCI CLI ejecutable: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AppError("OCI CLI excedio el tiempo de espera de 120 segundos; revisa conectividad y permisos de la VM.") from exc
    if completed.returncode != 0:
        details = "\n".join(
            line
            for line in redact((completed.stderr or completed.stdout).strip()).splitlines()[:12]
            if line.strip()
        )
        raise AppError(f"OCI CLI fallo con exit={completed.returncode}.\n{details}")
    return completed.stdout or ""


def run_oci(command: list[str]) -> None:
    run_oci_capture(command)


def _load_vault_credentials(args: argparse.Namespace, secret_ocid: str, platform: str) -> dict[str, str]:
    if not secret_ocid:
        return {}
    command = oci_global_args(args) + [
        "secrets",
        "secret-bundle",
        "get",
        "--secret-id",
        secret_ocid,
    ]
    try:
        output = run_oci_capture(command)
    except AppError as exc:
        raise AppError(f"No pude obtener el secreto {platform} desde OCI Vault: {exc}") from exc
    try:
        payload = json.loads(output)
        encoded = payload["data"]["secret-bundle-content"]["content"]
        decoded = base64.b64decode(str(encoded)).decode("utf-8")
        secret = json.loads(decoded)
    except Exception as exc:
        raise AppError(f"No pude leer el secreto {platform} desde OCI Vault en formato JSON.") from exc

    username = str(
        secret.get("username")
        or secret.get("user")
        or secret.get(f"{platform.upper()}_USERNAME")
        or secret.get(f"RESUMEN_ESCOLAR_{platform.upper()}_USERNAME")
        or ""
    ).strip()
    password = str(
        secret.get("password")
        or secret.get("pass")
        or secret.get(f"{platform.upper()}_PASSWORD")
        or secret.get(f"RESUMEN_ESCOLAR_{platform.upper()}_PASSWORD")
        or ""
    )
    if not username or not password:
        raise AppError(f"El secreto {platform} de OCI Vault no contiene username/password validos.")
    return {"username": username, "password": password}


def load_schoolnet_secret(args: argparse.Namespace) -> dict[str, str]:
    return _load_vault_credentials(args, str(getattr(args, "schoolnet_secret_ocid", "") or "").strip(), "SchoolNet")


def load_schoolnet_secret_into_env(args: argparse.Namespace) -> None:
    if os.environ.get(SCHOOLNET_USERNAME_ENV) and os.environ.get(SCHOOLNET_PASSWORD_ENV):
        return
    secret = load_schoolnet_secret(args)
    if not secret:
        return
    os.environ[SCHOOLNET_USERNAME_ENV] = secret["username"]
    os.environ[SCHOOLNET_PASSWORD_ENV] = secret["password"]


def load_google_secret_into_env(args: argparse.Namespace) -> None:
    if os.environ.get(GOOGLE_USERNAME_ENV) and os.environ.get(GOOGLE_PASSWORD_ENV):
        return
    secret_ocid = str(getattr(args, "google_secret_ocid", "") or "").strip()
    if not secret_ocid:
        return
    secret = _load_vault_credentials(args, secret_ocid, "Google")
    os.environ[GOOGLE_USERNAME_ENV] = secret["username"]
    os.environ[GOOGLE_PASSWORD_ENV] = secret["password"]


def content_type_for_path(path: Path) -> str:
    if path.suffix.lower() in {".txt", ".md", ".csv", ".log"}:
        return "text/plain; charset=utf-8"
    if path.suffix.lower() == ".json":
        return "application/json; charset=utf-8"
    guessed = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(path.suffix.lower())
    return guessed or "application/octet-stream"


def upload_object(
    args: argparse.Namespace,
    prompt_path: Path,
    object_name: str,
    content_type: str | None = None,
) -> dict[str, Any]:
    command = oci_global_args(args) + [
        "os",
        "object",
        "put",
        "--bucket-name",
        args.bucket_name,
        "--name",
        object_name,
        "--file",
        str(prompt_path),
        "--force",
        "--content-type",
        content_type or content_type_for_path(prompt_path),
    ]
    if args.namespace:
        command.extend(["--namespace", args.namespace])
    run_oci(command)
    return {"bucket": args.bucket_name, "object": object_name, "bytes": prompt_path.stat().st_size}


def material_files_for_prompt(prompt_path: Path) -> list[Path]:
    materials_dir = prompt_path.parent / "materials"
    if not materials_dir.exists():
        return []
    preferred = [
        materials_dir / "materials_summary.txt",
        materials_dir / "materials_index.json",
    ]
    files: list[Path] = [path for path in preferred if path.exists() and path.is_file()]
    files.extend(
        sorted(
            path
            for path in (materials_dir / "files").glob("*")
            if path.is_file()
        )
        if (materials_dir / "files").exists()
        else []
    )
    return files


def publish_materials(args: argparse.Namespace, prompt_path: Path) -> dict[str, Any]:
    files = material_files_for_prompt(prompt_path)
    if not files:
        return {"present": False, "files": 0, "uploads": []}
    materials_dir = prompt_path.parent / "materials"
    latest_prefix = str(args.materials_latest_prefix or DEFAULT_MATERIALS_LATEST_PREFIX).strip("/")
    archive_prefix = str(args.materials_archive_template or DEFAULT_MATERIALS_ARCHIVE_TEMPLATE).format(
        date=current_report_date().isoformat()
    ).strip("/")
    uploads: list[dict[str, Any]] = []
    for path in files:
        rel = path.relative_to(materials_dir).as_posix()
        uploads.append(upload_object(args, path, f"{latest_prefix}/{rel}"))
        if args.archive:
            uploads.append(upload_object(args, path, f"{archive_prefix}/{rel}"))
    return {"present": True, "files": len(files), "uploads": uploads}


def publish_prompt(args: argparse.Namespace, prompt_path: Path) -> dict[str, Any]:
    if not args.bucket_name:
        raise AppError("Falta --bucket-name o RESUMEN_ESCOLAR_BUCKET.")
    verification = verify_prompt(prompt_path, min_bytes=args.min_bytes)
    uploads = [upload_object(args, prompt_path, args.latest_object)]
    if args.archive:
        archive_object = args.archive_template.format(date=current_report_date().isoformat())
        uploads.append(upload_object(args, prompt_path, archive_object))
    materials = publish_materials(args, prompt_path)
    return {"prompt": verification, "uploads": uploads, "materials": materials}


def archive_object_names(args: argparse.Namespace) -> list[str]:
    """List prior prompt archives without exposing Object Storage details in notifications."""
    prefix = "archive/"
    command = oci_global_args(args) + [
        "os",
        "object",
        "list",
        "--bucket-name",
        args.bucket_name,
        "--prefix",
        prefix,
        "--all",
    ]
    if args.namespace:
        command.extend(["--namespace", args.namespace])
    try:
        payload = json.loads(run_oci_capture(command))
    except (AppError, json.JSONDecodeError):
        return []
    data = payload.get("data", [])
    objects = data.get("objects", []) if isinstance(data, dict) else data
    if not isinstance(objects, list):
        return []
    names = []
    for item in objects:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if re.fullmatch(r"archive/\d{4}-\d{2}-\d{2}/prompt_chatgpt\.txt", name):
            names.append(name)
    return sorted(names, reverse=True)


def download_object_text(args: argparse.Namespace, object_name: str) -> str:
    temp_path = Path(os.environ.get("TMPDIR") or "/tmp") / f"resumen-escolar-{os.getpid()}-previous.txt"
    try:
        command = oci_global_args(args) + [
            "os",
            "object",
            "get",
            "--bucket-name",
            args.bucket_name,
            "--name",
            object_name,
            "--file",
            str(temp_path),
        ]
        if args.namespace:
            command.extend(["--namespace", args.namespace])
        run_oci(command)
        return temp_path.read_text(encoding="utf-8", errors="replace")
    finally:
        temp_path.unlink(missing_ok=True)


def incremental_email_text(args: argparse.Namespace, current_prompt_path: Path) -> dict[str, Any]:
    """Build a bounded, copy-ready delta against the latest archived prompt."""
    current = current_prompt_path.read_text(encoding="utf-8", errors="replace")
    today_archive = str(args.archive_template).format(date=current_report_date().isoformat())
    previous_names = [name for name in archive_object_names(args) if name != today_archive]
    if not previous_names:
        return {
            "baseline": "No detectada",
            "text": (
                "BLOQUE INCREMENTAL PARA GABITIN\n"
                "Estado: no existe una version archivada anterior para comparar.\n"
                "Accion: para esta primera carga, pega el TXT completo publicado por el sistema; "
                "las siguientes notificaciones incluiran solo novedades."
            ),
            "truncated": False,
        }

    previous_name = previous_names[0]
    try:
        previous = download_object_text(args, previous_name)
    except AppError:
        return {
            "baseline": previous_name,
            "text": (
                "BLOQUE INCREMENTAL PARA GABITIN\n"
                f"Base anterior: {previous_name}\n"
                "Estado: no se pudo leer la base anterior para calcular novedades. "
                "El TXT completo sigue publicado y no fue modificado por este diagnostico."
            ),
            "truncated": False,
        }

    diff = list(
        difflib.unified_diff(
            previous.splitlines(),
            current.splitlines(),
            fromfile="reporte_anterior",
            tofile="reporte_actual",
            lineterm="",
            n=1,
        )
    )
    changed = [line for line in diff if line and not line.startswith(("---", "+++", "@@"))]
    if not changed:
        content = "Sin cambios detectados respecto a la version archivada anterior."
    else:
        content = "\n".join(changed)

    max_chars = max(2000, int(getattr(args, "incremental_email_max_chars", DEFAULT_INCREMENTAL_EMAIL_MAX_CHARS)))
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars].rstrip() + "\n[TRUNCADO: hay mas cambios en el TXT completo publicado.]"
    return {
        "baseline": previous_name,
        "text": "\n".join(
            [
                "BLOQUE INCREMENTAL PARA GABITIN",
                f"Base comparada: {previous_name}",
                f"Fecha de corte actual: {current_report_date().isoformat()}",
                "Convencion: + agregado/actualizado; - eliminado o reemplazado.",
                "Pega este bloque como evidencia nueva en la conversacion vigente.",
                "--- CAMBIOS DETECTADOS ---",
                content,
                "--- FIN CAMBIOS ---",
            ]
        ),
        "truncated": truncated,
    }


def publish_notification(args: argparse.Namespace, run_output: dict[str, Any]) -> dict[str, Any] | None:
    topic_id = str(getattr(args, "notification_topic_id", "") or "").strip()
    if not topic_id:
        return None

    report_date = current_report_date().isoformat()
    report_info = run_output.get("daily_report") or {}
    report_path = Path(str(report_info.get("path") or ""))
    if not report_path.exists():
        raise AppError("No existe el reporte diario para notificar.")
    state_path = Path(str(report_info.get("state_path") or ""))
    if not state_path.exists():
        raise AppError("No existe el estado JSON diario para notificar.")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError("El estado JSON diario no es válido para notificar.") from exc
    max_bytes = max(8_000, int(getattr(args, "email_max_bytes", DEFAULT_REPORT_MAX_BYTES)))
    try:
        body = render_email_message(state, load_daily_baseline(), max_bytes=max_bytes)
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    title = f"Resumen escolar diario | Gabito | {report_date}"
    command = oci_global_args(args) + [
        "ons",
        "message",
        "publish",
        "--topic-id",
        topic_id,
        "--title",
        title,
        "--body",
        body,
    ]
    run_oci(command)
    return {
        "topic_configured": True,
        "title": title,
        "bytes": len(body.encode("utf-8")),
    }


def failure_notifications_enabled() -> bool:
    return os.environ.get(FAILURE_NOTIFICATIONS_ENV, "1").strip().lower() not in {"0", "false", "no"}


def is_classroom_session_error(error: str) -> bool:
    normalized = error.lower()
    return "google classroom" in normalized and any(
        marker in normalized
        for marker in (
            "needs_login",
            "login",
            "accounts.google",
        )
    )


def is_schoolnet_session_error(error: str) -> bool:
    normalized = error.lower()
    return "schoolnet" in normalized and any(
        marker in normalized
        for marker in (
            "parece estar en pantalla de login",
            "siguio mostrando login",
            "requiere login",
            "re-login automatico tras redireccion interna",
            "campos de usuario/clave",
        )
    )


def is_schoolnet_extraction_error(error: str) -> bool:
    normalized = error.lower()
    return "schoolnet" in normalized and any(
        marker in normalized
        for marker in (
            "no pude construir una lectura estructurada",
            "tabla de calificaciones p1/p2",
            "error de extraccion schoolnet",
        )
    )


def is_browser_session_error(error: str) -> bool:
    normalized = error.lower()
    return any(
        marker in normalized
        for marker in (
            "connect_over_cdp",
            "econnrefused",
            "9222",
            "processsingleton",
            "singletonlock",
            "profile directory",
            "browser_profile_busy_without_cdp",
            "chromium_cdp",
        )
    )


def is_schoolnet_vault_error(error: str) -> bool:
    normalized = error.lower()
    return "schoolnet" in normalized and any(
        marker in normalized
        for marker in (
            "no hay credenciales",
            "oci vault",
            "secreto schoolnet",
            "secret-bundle",
            "schoolnet_secret_ocid",
        )
    )


def login_recovery(error: str) -> tuple[str, str, str] | None:
    """Return platform, command and next step for a session-related failure."""
    if is_browser_session_error(error) or is_schoolnet_vault_error(error):
        return None
    classroom = is_classroom_session_error(error)
    schoolnet = is_schoolnet_session_error(error)
    if classroom and schoolnet:
        return (
            "SchoolNet y Classroom",
            LOGIN_BOTH_COMMAND,
            "Pasos: 1) desconecta temporalmente la VPN corporativa; 2) ejecuta el comando; "
            "3) verifica RESULTADO: OK; 4) abre la URL noVNC mostrada en la etapa 10 e inicia sesion en ambas plataformas. "
            f"Si aparece RESULTADO: FALLO, conserva el log: {LOGIN_LOG_PATH}",
        )
    if schoolnet:
        return (
            "SchoolNet",
            SCHOOLNET_LOGIN_COMMAND,
            "Pasos: 1) desconecta temporalmente la VPN corporativa; 2) ejecuta el comando; "
            "3) verifica RESULTADO: OK; 4) abre la URL noVNC mostrada en la etapa 10 e inicia sesion en SchoolNet. "
            f"Si aparece RESULTADO: FALLO, conserva el log: {LOGIN_LOG_PATH}",
        )
    if classroom:
        return (
            "Classroom",
            CLASSROOM_LOGIN_COMMAND,
            "Pasos: 1) desconecta temporalmente la VPN corporativa; 2) ejecuta el comando; "
            "3) verifica RESULTADO: OK; 4) abre la URL noVNC mostrada en la etapa 10 e inicia sesion con Google. "
            f"Si aparece RESULTADO: FALLO, conserva el log: {LOGIN_LOG_PATH}",
        )
    return None


def failure_diagnosis(error: str) -> tuple[str, str, str]:
    """Return a stable notification category, title fragment and safe next step."""
    normalized = error.lower()
    if "resumen_escolar_bucket" in normalized or "--bucket-name" in normalized:
        return (
            "configuration",
            "configuracion de Object Storage",
            "Revisa que RESUMEN_ESCOLAR_BUCKET tenga valor en automation.env de la VM; conserva el archivo al desplegar.",
        )
    if is_browser_session_error(error):
        return (
            "browser",
            "navegador de la VM",
            "El recuperador automatico de Chromium/CDP no pudo tomar un perfil seguro. "
            "No inicies login manual ni borres locks; revisa el log remoto de Chromium.",
        )
    if is_schoolnet_vault_error(error):
        return (
            "vault",
            "credencial SchoolNet",
            "La VM no pudo obtener la credencial desde OCI Vault. Revisa el OCID configurado, "
            "la dynamic group y la politica de lectura del secreto; no copies la clave a archivos.",
        )
    if any(
        marker in normalized for marker in ("credencial de google en oci vault", "google_secret_ocid", "google login missing_credentials", "secreto google", "google desde oci vault")
    ):
        return (
            "google_vault",
            "credencial Google",
            "Configura RESUMEN_ESCOLAR_GOOGLE_SECRET_OCID en la VM y verifica el permiso de lectura del secreto en OCI Vault.",
        )
    if "google login password_rejected" in normalized:
        return (
            "google_credentials",
            "contraseña Google",
            "Google rechazó la contraseña. Corrige el secreto en OCI Vault; no envíes la clave por correo ni por chat.",
        )
    if "google login login_ui_error" in normalized:
        return (
            "google_ui",
            "formulario Google",
            "El formulario de acceso de Google cambió o no terminó de cargar. Revisa la pantalla en Chrome de la VM y el flujo de login.",
        )
    if any(marker in normalized for marker in ("google login mfa_required", "google login captcha_required", "google login google_blocked", "google login manual_required")):
        return (
            "google_verification",
            "verificación Google",
            "Abre Chrome de la VM por la sesión noVNC existente y completa la verificación solicitada por Google.",
        )
    if is_schoolnet_extraction_error(error) and not is_schoolnet_session_error(error):
        return (
            "extraction",
            "lectura de Calificaciones SchoolNet",
            "La tabla de SchoolNet no pudo interpretarse. No reinicies el login; revisa el cambio de DOM o la carga de la vista.",
        )
    if "google classroom" in normalized and any(
        marker in normalized for marker in ("ruta global", "ruta incorrecta", "trabajo de clase=error")
    ):
        return (
            "classroom_navigation",
            "ruta de Classroom",
            "Classroom abrió una ruta fuera de Trabajo de clase. Revisa la URL y la carga de la vista en Chrome de la VM.",
        )
    recovery = login_recovery(error)
    if recovery:
        return ("login", recovery[0], recovery[2])
    return (
        "generic",
        "ejecucion diaria",
        "Revisa el log de la VM y ejecuta nuevamente el cron despues de identificar la causa.",
    )


def publish_failure_notification(args: argparse.Namespace, error: str) -> dict[str, Any] | None:
    topic_id = str(getattr(args, "notification_topic_id", "") or "").strip()
    if not topic_id or not failure_notifications_enabled():
        return None

    report_date = current_report_date().isoformat()
    category, subject, next_step = failure_diagnosis(error)
    recovery = login_recovery(error) if category == "login" else None
    title = (
        f"Resumen Escolar requiere login {subject} - {report_date}"
        if recovery
        else f"Resumen Escolar fallo: {subject} - {report_date}"
    )
    diagnosis = {
        "browser": "Diagnostico: Chromium/CDP de la VM no estaba disponible o el perfil estaba ocupado.",
        "vault": "Diagnostico: no fue posible obtener la credencial SchoolNet desde OCI Vault.",
        "google_vault": "Diagnostico: la VM no tiene disponible la credencial Google en OCI Vault.",
        "google_credentials": "Diagnostico: Google rechazo la contraseña recuperada de OCI Vault.",
        "google_ui": "Diagnostico: el formulario de acceso de Google no se pudo completar de forma automatica.",
        "google_verification": "Diagnostico: Google exige una verificacion humana o rechazo el navegador automatizado.",
        "login": f"Diagnostico probable: la sesion de {subject} expiro y el re-login automatico no la recupero.",
        "extraction": "Diagnostico: SchoolNet entrego una vista que no se pudo estructurar como tabla P1/P2.",
        "classroom_navigation": "Diagnostico: Classroom no permanecio en la vista esperada del curso.",
        "configuration": "Diagnostico: falta una configuracion necesaria para publicar en Object Storage.",
        "generic": "Diagnostico: la generacion automatica fallo antes de publicar el reporte diario.",
    }[category]
    body_lines = [
        "El cron diario de Resumen Escolar fallo; se conserva el ultimo reporte valido.",
        f"Fecha reporte: {report_date}",
        diagnosis,
        f"Codigo de diagnostico: {category}",
        "Detalle tecnico: disponible en el log privado de la VM; no se envia contenido escolar por OCI Notifications.",
    ]
    affected_platforms = [
        label for marker, label in (("schoolnet", "SchoolNet"), ("google classroom", "Classroom"))
        if marker in error.lower()
    ]
    if affected_platforms:
        body_lines.append("Plataformas afectadas: " + ", ".join(affected_platforms))
    if recovery:
        body_lines.extend(
            [
                "Accion requerida: desconectarse temporalmente de la VPN corporativa antes de abrir la sesion de la VM.",
                "En PowerShell, ejecutar:",
                recovery[1],
                recovery[2],
            ]
        )
    else:
        body_lines.append(f"Accion sugerida: {next_step}")
    body = "\n".join(body_lines)
    command = oci_global_args(args) + [
        "ons",
        "message",
        "publish",
        "--topic-id",
        topic_id,
        "--title",
        title,
        "--body",
        body,
    ]
    run_oci(command)
    return {"topic_configured": True, "title": title}


def snapshot_statuses(result: dict[str, Any]) -> list[dict[str, str]]:
    statuses: list[dict[str, str]] = []
    for snapshot in result.get("live_snapshots", []):
        statuses.append(
            {
                "platform": str(getattr(snapshot, "platform", "")),
                "status": str(getattr(snapshot, "status", "")),
            }
        )
    return statuses


def fail_if_blocked(result: dict[str, Any]) -> None:
    blocked = [
        snapshot
        for snapshot in result.get("live_snapshots", [])
        if str(getattr(snapshot, "status", "")) not in {"ok", "empty"}
    ]
    if blocked:
        compact_items = []
        for snapshot in blocked:
            platform = str(getattr(snapshot, "platform", ""))
            status = str(getattr(snapshot, "status", ""))
            notes = [single_line(str(note))[:3500] for note in (getattr(snapshot, "notes", []) or []) if note]
            login_notes = [note for note in notes if is_schoolnet_session_error(f"{platform} {note}")]
            selected_notes = (login_notes + [note for note in notes if note not in login_notes])[:2]
            note = " | ".join(selected_notes)
            compact_items.append(f"{platform}={status}" + (f" ({note})" if note else ""))
        compact = ", ".join(compact_items)
        raise AppError(f"Generacion incompleta; no se publica. Estados bloqueados: {compact}")


def validate_generated_output(
    result: dict[str, Any],
    prompt_path: Path,
    *,
    min_bytes: int,
    allow_blocked: bool,
) -> dict[str, Any]:
    if not allow_blocked:
        fail_if_blocked(result)
    return verify_prompt(prompt_path, min_bytes=min_bytes)


def load_daily_baseline() -> dict[str, Any] | None:
    try:
        if DAILY_STATE_PATH.exists():
            payload = json.loads(DAILY_STATE_PATH.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
    return None


def save_daily_baseline(state: dict[str, Any]) -> None:
    DAILY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DAILY_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(DAILY_STATE_PATH)


def daily_report_from_result(result: dict[str, Any], previous: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    snapshots = result.get("prompt_snapshots") or result.get("live_snapshots") or []
    state = build_state(snapshots)
    return state, render_report(state, previous)


def publish_daily_report(args: argparse.Namespace, report_path: Path, state_path: Path) -> dict[str, Any]:
    if not args.bucket_name:
        raise AppError("Falta --bucket-name o RESUMEN_ESCOLAR_BUCKET.")
    uploads = [
        upload_object(args, report_path, args.daily_report_latest_object),
        upload_object(args, state_path, args.daily_state_latest_object),
    ]
    if args.archive:
        day = current_report_date().isoformat()
        uploads.extend([
            upload_object(args, report_path, args.daily_report_archive_template.format(date=day)),
            upload_object(args, state_path, args.daily_state_archive_template.format(date=day)),
        ])
    return {"uploads": uploads, "report": str(report_path), "state": str(state_path)}


def cmd_run(args: argparse.Namespace) -> dict[str, Any]:
    load_schoolnet_secret_into_env(args)
    load_google_secret_into_env(args)
    result = generate_prompt_once(
        manual_notes=args.manual_notes or "",
        force_full_scan=args.force_full_scan,
        headless=not args.headed,
    )
    prompt_path = Path(result["prompt_path"])
    verification = validate_generated_output(
        result,
        prompt_path,
        min_bytes=args.min_bytes,
        allow_blocked=args.allow_blocked_snapshots,
    )
    previous_state = load_daily_baseline()
    daily_state, daily_text = daily_report_from_result(result, previous_state)
    daily_dir = OUTBOX_DIR / daily_state["report_date"]
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_report_path = daily_dir / "daily_report.txt"
    daily_state_path = daily_dir / "daily_report_state.json"
    daily_report_path.write_text(daily_text, encoding="utf-8")
    daily_state_path.write_text(json.dumps(daily_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output: dict[str, Any] = {
        "ok": True,
        "prompt": verification,
        "materials": {
            "dir": str(result.get("materials_dir", "")),
            "index": str(result.get("materials_index_path", "")),
            "summary": str(result.get("materials_summary_path", "")),
            "files": len(material_files_for_prompt(prompt_path)),
        },
        "live_statuses": snapshot_statuses(result),
        "cache_update": result.get("cache_update", {}),
        "published": False,
        "daily_report": {"path": str(daily_report_path), "state_path": str(daily_state_path), "bytes": len(daily_text.encode('utf-8'))},
    }
    if args.publish:
        output["publish"] = publish_daily_report(args, daily_report_path, daily_state_path)
        output["published"] = True
        notification = publish_notification(args, output)
        if notification:
            output["notification"] = notification
        save_daily_baseline(daily_state)
    return output


def cmd_publish(args: argparse.Namespace) -> dict[str, Any]:
    prompt_path = Path(args.prompt_path) if args.prompt_path else prompt_path_for_today()
    return {"ok": True, "published": True, "publish": publish_prompt(args, prompt_path)}


def cmd_notify_failure(args: argparse.Namespace) -> dict[str, Any]:
    """Publish a classified operational failure without attempting a report run."""
    notification = publish_failure_notification(args, args.error)
    return {"ok": True, "notification": notification}


def add_oci_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bucket-name", default=os.environ.get("RESUMEN_ESCOLAR_BUCKET", ""))
    parser.add_argument("--namespace", default=os.environ.get("RESUMEN_ESCOLAR_OCI_NAMESPACE", ""))
    parser.add_argument("--oci-auth", default=os.environ.get("RESUMEN_ESCOLAR_OCI_AUTH", "instance_principal"))
    parser.add_argument("--oci-profile", default=os.environ.get("RESUMEN_ESCOLAR_OCI_PROFILE", ""))
    parser.add_argument("--oci-config-file", default=os.environ.get("RESUMEN_ESCOLAR_OCI_CONFIG_FILE", ""))
    parser.add_argument("--oci-region", default=os.environ.get("RESUMEN_ESCOLAR_OCI_REGION", ""))
    parser.add_argument(
        "--schoolnet-secret-ocid",
        default=os.environ.get("RESUMEN_ESCOLAR_SCHOOLNET_SECRET_OCID", ""),
        help="OCID de OCI Vault Secret con JSON {'username': '...', 'password': '...'} para SchoolNet.",
    )
    parser.add_argument("--google-secret-ocid", default=os.environ.get("RESUMEN_ESCOLAR_GOOGLE_SECRET_OCID", ""),
                        help="OCID de OCI Vault Secret con username/password para login web de Google.")
    parser.add_argument("--latest-object", default=os.environ.get("RESUMEN_ESCOLAR_LATEST_OBJECT", DEFAULT_LATEST_OBJECT))
    parser.add_argument("--latest-txt-url", default=os.environ.get(LATEST_TXT_URL_ENV, ""))
    parser.add_argument(
        "--materials-latest-prefix",
        default=os.environ.get("RESUMEN_ESCOLAR_MATERIALS_LATEST_PREFIX", DEFAULT_MATERIALS_LATEST_PREFIX),
    )
    parser.add_argument(
        "--materials-archive-template",
        default=os.environ.get("RESUMEN_ESCOLAR_MATERIALS_ARCHIVE_TEMPLATE", DEFAULT_MATERIALS_ARCHIVE_TEMPLATE),
    )
    parser.add_argument("--notification-topic-id", default=os.environ.get(NOTIFICATION_TOPIC_ENV, ""))
    parser.add_argument("--daily-report-latest-object", default=os.environ.get("RESUMEN_ESCOLAR_DAILY_REPORT_LATEST_OBJECT", DEFAULT_DAILY_REPORT_LATEST_OBJECT))
    parser.add_argument("--daily-report-archive-template", default=os.environ.get("RESUMEN_ESCOLAR_DAILY_REPORT_ARCHIVE_TEMPLATE", DEFAULT_DAILY_REPORT_ARCHIVE_TEMPLATE))
    parser.add_argument("--daily-state-latest-object", default=os.environ.get("RESUMEN_ESCOLAR_DAILY_STATE_LATEST_OBJECT", DEFAULT_DAILY_STATE_LATEST_OBJECT))
    parser.add_argument("--daily-state-archive-template", default=os.environ.get("RESUMEN_ESCOLAR_DAILY_STATE_ARCHIVE_TEMPLATE", DEFAULT_DAILY_STATE_ARCHIVE_TEMPLATE))
    parser.add_argument("--email-max-bytes", type=int, default=int(os.environ.get("RESUMEN_ESCOLAR_EMAIL_MAX_BYTES", str(DEFAULT_REPORT_MAX_BYTES))))
    parser.add_argument(
        "--incremental-email-max-chars",
        type=int,
        default=int(os.environ.get("RESUMEN_ESCOLAR_INCREMENTAL_EMAIL_MAX_CHARS", DEFAULT_INCREMENTAL_EMAIL_MAX_CHARS)),
    )
    parser.add_argument(
        "--archive-template",
        default=os.environ.get("RESUMEN_ESCOLAR_ARCHIVE_TEMPLATE", DEFAULT_ARCHIVE_TEMPLATE),
    )
    parser.add_argument("--no-archive", dest="archive", action="store_false")
    parser.set_defaults(archive=os.environ.get("RESUMEN_ESCOLAR_ARCHIVE", "1").strip().lower() not in {"0", "false", "no"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automatizacion semanal de Resumen Escolar.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Genera el prompt y opcionalmente lo publica.")
    run_parser.add_argument("--publish", action="store_true")
    run_parser.add_argument("--headed", action="store_true", help="Usar navegador visible en vez de headless.")
    run_parser.add_argument("--force-full-scan", action="store_true")
    run_parser.add_argument("--manual-notes", default="")
    run_parser.add_argument("--min-bytes", type=int, default=int(os.environ.get("RESUMEN_ESCOLAR_MIN_BYTES", "1")))
    run_parser.add_argument("--allow-blocked-snapshots", action="store_true")
    add_oci_args(run_parser)
    run_parser.set_defaults(func=cmd_run)

    publish_parser = subparsers.add_parser("publish", help="Publica un prompt existente en OCI Object Storage.")
    publish_parser.add_argument("--prompt-path", default="")
    publish_parser.add_argument("--min-bytes", type=int, default=int(os.environ.get("RESUMEN_ESCOLAR_MIN_BYTES", "1")))
    add_oci_args(publish_parser)
    publish_parser.set_defaults(func=cmd_publish)

    notify_failure_parser = subparsers.add_parser(
        "notify-failure", help="Publica una alerta operacional clasificada."
    )
    notify_failure_parser.add_argument("--error", required=True)
    add_oci_args(notify_failure_parser)
    notify_failure_parser.set_defaults(func=cmd_notify_failure)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except AppError as exc:
        payload: dict[str, Any] = {"ok": False, "error": redact(str(exc))}
        if getattr(args, "command", "") == "run" and getattr(args, "publish", False):
            try:
                notification = publish_failure_notification(args, str(exc))
            except AppError as notification_exc:
                payload["notification_error"] = redact(str(notification_exc))
            else:
                if notification:
                    payload["notification"] = notification
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
