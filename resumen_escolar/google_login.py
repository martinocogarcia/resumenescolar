"""Login convencional de Google en el Chrome de la VM, sin eludir verificaciones."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def _host(page: Any) -> str:
    return (urlparse(str(getattr(page, "url", "") or "")).hostname or "").lower()


def _visible(page: Any, selector: str) -> Any | None:
    try:
        candidate = page.locator(selector).first
        return candidate if candidate.is_visible() else None
    except Exception:
        return None


def _wait(page: Any) -> None:
    try:
        page.wait_for_timeout(1000)
    except Exception:
        pass


def _challenge_result(page: Any) -> dict[str, Any]:
    try:
        text = page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        text = ""
    if any(marker in text for marker in ("captcha", "recaptcha")):
        return {"ok": False, "status": "captcha_required", "note": "Google exige CAPTCHA; completar en Chrome de la VM."}
    if any(marker in text for marker in ("wrong password", "contraseña incorrecta", "contrasena incorrecta")):
        return {"ok": False, "status": "password_rejected", "note": "Google rechazo la contraseña; revisar el secreto en OCI Vault."}
    if any(marker in text for marker in ("2-step verification", "verificación en dos pasos", "verificacion en dos pasos", "check your phone", "revisa tu teléfono", "revisa tu telefono")):
        return {"ok": False, "status": "mfa_required", "note": "Google exige verificación adicional; completar en Chrome de la VM."}
    if any(marker in text for marker in ("couldn't sign you in", "no se ha podido iniciar sesión", "browser is not secure", "navegador no es seguro")):
        return {"ok": False, "status": "google_blocked", "note": "Google no permitió el inicio automático; revisar en Chrome de la VM."}
    return {"ok": False, "status": "manual_required", "note": "Google sigue solicitando una acción en Chrome de la VM."}


def login_google_page(page: Any, username: str, password: str) -> dict[str, Any]:
    """Intenta un login normal una vez; nunca responde desafíos de seguridad."""
    if _host(page) == "classroom.google.com":
        return {"ok": True, "attempted": False}
    if _host(page) != "accounts.google.com":
        return {"ok": False, "status": "unexpected_route", "note": "Google abrió una ruta inesperada."}
    if not username or not password:
        return {"ok": False, "status": "missing_credentials", "note": "Falta configurar la credencial de Google en OCI Vault de la VM."}

    try:
        email_field = _visible(page, 'input[type="email"], input[name="identifier"]')
        if email_field is not None:
            email_field.fill(username)
            email_field.press("Enter")
        elif "confirmidentifier" in str(page.url).lower():
            next_button = _visible(page, "#identifierNext")
            if next_button is not None:
                next_button.click()
        else:
            try:
                account = page.get_by_text(username, exact=True).first
                if account.is_visible():
                    account.click()
            except Exception:
                pass

        password_field = None
        for _ in range(12):
            if _host(page) == "classroom.google.com":
                return {"ok": True, "attempted": True}
            password_field = _visible(page, 'input[type="password"]')
            if password_field is not None:
                break
            _wait(page)
        if password_field is None:
            return _challenge_result(page)

        password_field.fill(password)
        password_field.press("Enter")
        for _ in range(18):
            if _host(page) == "classroom.google.com":
                return {"ok": True, "attempted": True}
            _wait(page)
        return _challenge_result(page)
    except Exception:
        return {"ok": False, "status": "login_ui_error", "note": "El formulario de Google cambió o no respondió en Chrome de la VM."}
