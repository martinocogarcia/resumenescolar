"""Politica pura para recuperar SchoolNet sin repetir un estado atascado."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum


class RecoveryAction(str, Enum):
    WAIT_FOR_FORM = "wait_for_form"
    RELOAD_PAGE = "reload_page"
    NAVIGATE_LOGIN = "navigate_login"
    REPLACE_PAGE = "replace_page"
    RECONNECT_CDP = "reconnect_cdp"
    RESTART_BROWSER = "restart_browser"
    ATTEMPT_LOGIN = "attempt_login"
    EXTRACT = "extract"
    PUBLISH = "publish"
    STOP = "stop"


@dataclass(frozen=True)
class RecoverySignal:
    now: dt.datetime
    deadline: dt.datetime
    category: str
    authenticated: bool
    form_ready: bool
    username_field_present: bool
    password_field_present: bool
    submit_present: bool
    document_ready: bool
    cdp_ready: bool
    profile_ready: bool
    novnc_active: bool
    page_count: int
    frame_count: int
    visible_input_count: int
    browser_restart_count: int
    last_browser_restart_at: dt.datetime | None
    action_history: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    category: str
    terminal: bool
    reason: str
    next_action: str


RECOVERY_LADDER = (
    RecoveryAction.WAIT_FOR_FORM,
    RecoveryAction.RELOAD_PAGE,
    RecoveryAction.NAVIGATE_LOGIN,
    RecoveryAction.REPLACE_PAGE,
    RecoveryAction.RECONNECT_CDP,
    RecoveryAction.RESTART_BROWSER,
)
RESTART_COOLDOWN = dt.timedelta(minutes=20)
MANUAL_AUTH_CATEGORIES = frozenset({
    "captcha_required", "mfa_required", "manual_required", "account_blocked", "invalid_password",
})


def classify_failure(message: str) -> str:
    text = (message or "").casefold()
    for marker, category in (
        ("captcha", "captcha_required"), ("doble factor", "mfa_required"), ("mfa", "mfa_required"),
        ("verificacion", "manual_required"), ("verification", "manual_required"),
        ("cuenta bloqueada", "account_blocked"), ("credenciales invalid", "invalid_password"),
        ("contrasena incorrect", "invalid_password"), ("cdp", "cdp_unavailable"),
        ("vault", "vault_unavailable"), ("timeout", "network_unavailable"),
        ("no pude detectar campos", "page_not_ready"), ("formulario", "page_not_ready"),
        ("extract", "extraction_failed"), ("public", "publication_failed"),
    ):
        if marker in text:
            return category
    return "technical_unknown"


def is_manual_auth_category(category: str) -> bool:
    return category in MANUAL_AUTH_CATEGORIES


def _stop(category: str, reason: str) -> RecoveryDecision:
    return RecoveryDecision(RecoveryAction.STOP, category, True, reason, "none")


def decide_next(signal: RecoverySignal) -> RecoveryDecision:
    if signal.now >= signal.deadline:
        return _stop("deadline_reached", "Se alcanzo la hora limite de recuperacion.")
    if is_manual_auth_category(signal.category):
        return _stop(signal.category, "SchoolNet requiere intervencion manual; se detiene para no agravar el bloqueo.")
    if signal.authenticated:
        return RecoveryDecision(RecoveryAction.EXTRACT, "none", False, "Sesion autenticada confirmada.", "extract")
    if signal.form_ready:
        return RecoveryDecision(RecoveryAction.ATTEMPT_LOGIN, "none", False, "Formulario SchoolNet disponible.", "attempt_login")
    for action in RECOVERY_LADDER:
        if action.value in signal.action_history:
            continue
        if action is RecoveryAction.RESTART_BROWSER:
            if signal.novnc_active:
                return _stop("browser_restart_blocked_novnc", "noVNC esta activo; se preserva la sesion manual.")
            if signal.last_browser_restart_at and signal.now - signal.last_browser_restart_at < RESTART_COOLDOWN:
                return _stop("browser_restart_cooldown", "El reinicio de Chromium sigue en enfriamiento de 20 minutos.")
        return RecoveryDecision(action, signal.category, False, "Escalamiento tecnico controlado.", action.value)
    return _stop("recovery_exhausted", "No quedan acciones tecnicas seguras dentro de la ventana nocturna.")
