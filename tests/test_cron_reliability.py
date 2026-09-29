"""Regresiones del cron y de las alertas operativas."""

import argparse
import base64
import json
import os
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

from resumen_escolar.app import BrowserController, PageSnapshot, login_status_note
from resumen_escolar.automation import (
    failure_diagnosis,
    login_recovery,
    load_google_secret_into_env,
    publish_failure_notification,
    run_oci_capture,
)
from resumen_escolar.app import AppError
from resumen_escolar.google_login import login_google_page


class CronReliabilityTests(unittest.TestCase):
    def test_oci_cli_timeout_has_bounded_safe_error(self):
        with patch("resumen_escolar.automation.subprocess.run", side_effect=subprocess.TimeoutExpired(["oci"], 120)):
            with self.assertRaisesRegex(AppError, "tiempo de espera"):
                run_oci_capture(["oci", "ons", "message", "publish"])

    def test_login_session_launcher_reuses_chrome_without_killing_existing_profile(self):
        script = (Path(__file__).resolve().parents[1] / "scripts" / "start_vm_login_session.ps1").read_text(encoding="utf-8")
        self.assertIn("REMOTE_LOGIN_SESSION_REUSED=true", script)
        self.assertNotIn("pkill", script)
        self.assertNotIn('kill "$pid"', script)
        self.assertNotIn("Stop-Process -Id", script)

    def test_google_vault_credentials_are_loaded_only_into_process_environment(self):
        secret = base64.b64encode(json.dumps({"username": "person@example.test", "password": "private-pass"}).encode()).decode()
        payload = json.dumps({"data": {"secret-bundle-content": {"content": secret}}})
        args = argparse.Namespace(google_secret_ocid="ocid1.secret.synthetic", oci_auth="", oci_profile="", oci_config_file="", oci_region="")
        with patch.dict(os.environ, {}, clear=True), patch("resumen_escolar.automation.run_oci_capture", return_value=payload):
            load_google_secret_into_env(args)
            self.assertEqual(os.environ["RESUMEN_ESCOLAR_GOOGLE_USERNAME"], "person@example.test")
            self.assertEqual(os.environ["RESUMEN_ESCOLAR_GOOGLE_PASSWORD"], "private-pass")

    def test_classroom_collection_retries_after_google_ui_login(self):
        class Page:
            url = ""
            visits = 0

            def bring_to_front(self):
                pass

            def goto(self, *_args, **_kwargs):
                self.visits += 1
                self.url = (
                    "https://accounts.google.com/v3/signin/identifier"
                    if self.visits == 1 else "https://classroom.google.com/w/course/t/all"
                )

        controller = object.__new__(BrowserController)
        controller._progress = lambda *_args: 0
        controller._progress_done = lambda *_args: None
        controller._wait_after_classroom_interaction = lambda *_args: None
        controller._login_classroom_if_needed = lambda page: (
            setattr(page, "url", "https://classroom.google.com/w/course/t/all")
            or {"ok": True, "attempted": True}
        )
        controller._is_classroom_4a_listing_page = lambda page: page.url.startswith("https://classroom.google.com/w/")
        controller._read_classroom_topic_links = lambda _page: [{"label": "Tema", "url": "https://classroom.google.com/w/course/t/topic"}]
        controller._collect_classroom_topic_snapshots_by_url = lambda *_args: []
        page = Page()
        snapshots = controller._collect_classroom_snapshots(page)
        self.assertEqual(snapshots[0].status, "ok")
        self.assertEqual(page.visits, 2)

    def test_missing_google_vault_secret_has_specific_notification(self):
        category, subject, step = failure_diagnosis(
            "Google Classroom=needs_login (Falta configurar la credencial de Google en OCI Vault de la VM.)"
        )
        self.assertEqual(category, "google_vault")
        self.assertIn("Google", subject)
        self.assertIn("Vault", step)

    def test_google_mfa_has_verification_notification_without_login_restart_command(self):
        error = "Google Classroom=needs_login (Google login mfa_required: Google exige verificación adicional.)"
        category, _, step = failure_diagnosis(error)
        self.assertEqual(category, "google_verification")
        self.assertIn("Chrome", step)

    def test_google_rejected_password_points_to_vault_credential(self):
        category, _, step = failure_diagnosis("Google Classroom=needs_login (Google login password_rejected)")
        self.assertEqual(category, "google_credentials")
        self.assertIn("Vault", step)

    def test_google_form_change_is_not_reported_as_expired_session(self):
        category, _, step = failure_diagnosis("Google Classroom=needs_login (Google login login_ui_error)")
        self.assertEqual(category, "google_ui")
        self.assertIn("formulario", step)

    def test_google_login_uses_visible_email_and_password_fields_without_returning_secrets(self):
        class Locator:
            def __init__(self, page, selector):
                self.page, self.selector = page, selector
                self.first = self

            def is_visible(self):
                return (self.selector.startswith('input[type="email"]') and self.page.stage == "email") or (
                    self.selector.startswith('input[type="password"]') and self.page.stage == "password"
                )

            def fill(self, value):
                self.page.filled.append(value)

            def press(self, key):
                if key == "Enter":
                    if self.page.stage == "email":
                        self.page.stage = "password"
                    else:
                        self.page.stage = "classroom"
                        self.page.url = "https://classroom.google.com/w/course/t/all"

        class Page:
            url = "https://accounts.google.com/v3/signin/identifier"
            stage = "email"

            def __init__(self):
                self.filled = []

            def locator(self, selector):
                return Locator(self, selector)

            def wait_for_timeout(self, _milliseconds):
                pass

        page = Page()
        result = login_google_page(page, "person@example.test", "private-pass")
        self.assertTrue(result["ok"])
        self.assertEqual(page.filled, ["person@example.test", "private-pass"])
        self.assertNotIn("private-pass", str(result))
        self.assertNotIn("person@example.test", str(result))

    def test_google_login_without_vm_credentials_reports_missing_credentials(self):
        class Page:
            url = "https://accounts.google.com/v3/signin/confirmidentifier"

        result = login_google_page(Page(), "", "")
        self.assertEqual(result["status"], "missing_credentials")

    def test_schoolnet_retries_grades_when_first_dom_read_has_no_canonical_rows(self):
        controller = object.__new__(BrowserController)
        controller._progress = lambda *_args: 0
        grade_calls = []

        def navigate(_page, platform, *_args):
            if platform == "SchoolNet - Conducta":
                return PageSnapshot(platform, platform, "", "conducta", "ok", [], {})
            grade_calls.append(platform)
            text = (
                ""
                if len(grade_calls) == 1
                else "CALIFICACIONES P1/P2 CANONICAS SCHOOLNET\nAsignatura | P1 | P2\nMatematica | 6,0 | 6,5"
            )
            return PageSnapshot(platform, platform, "", text, "ok", [], {})

        controller._navigate_and_snapshot = navigate

        class Page:
            def bring_to_front(self):
                pass

            def wait_for_timeout(self, _milliseconds):
                pass

        snapshots = controller._collect_schoolnet_snapshots(Page())

        self.assertEqual(len(grade_calls), 2)
        self.assertEqual(snapshots[1].status, "ok")
        self.assertIn("Matematica | 6,0 | 6,5", snapshots[1].text)

    def test_schoolnet_marks_grades_as_error_after_three_empty_dom_reads(self):
        controller = object.__new__(BrowserController)
        controller._progress = lambda *_args: 0
        grade_calls = []

        def navigate(_page, platform, *_args):
            if platform == "SchoolNet - Conducta":
                return PageSnapshot(platform, platform, "", "conducta", "ok", [], {})
            grade_calls.append(platform)
            return PageSnapshot(platform, platform, "", "", "ok", [], {})

        controller._navigate_and_snapshot = navigate

        class Page:
            def __init__(self):
                self.reload_calls = 0

            def bring_to_front(self):
                pass

            def wait_for_timeout(self, _milliseconds):
                pass

            def reload(self, **_kwargs):
                self.reload_calls += 1

        page = Page()
        snapshots = controller._collect_schoolnet_snapshots(page)

        self.assertEqual(len(grade_calls), 3)
        self.assertEqual(page.reload_calls, 1)
        self.assertEqual(snapshots[1].status, "error")
        self.assertTrue(any("3 intentos" in note for note in snapshots[1].notes))

    def test_schoolnet_relogs_when_grades_redirect_to_login(self):
        controller = object.__new__(BrowserController)
        controller._progress = lambda *_args: 0
        login_calls = []
        grade_calls = []

        def login(_page):
            login_calls.append(1)
            return {"ok": True, "attempted": len(login_calls) > 1}

        def navigate(_page, platform, *_args):
            if platform == "SchoolNet - Conducta":
                return PageSnapshot(platform, platform, "", "conducta", "ok", [], {})
            grade_calls.append(1)
            if len(grade_calls) == 1:
                return PageSnapshot(platform, platform, "https://schoolnet.colegium.com/login", "Usuario Clave", "needs_login", [], {})
            return PageSnapshot(
                platform, platform, "https://schoolnet.colegium.com/grades",
                "CALIFICACIONES P1/P2 CANONICAS SCHOOLNET\nAsignatura | P1 | P2\nMatematica | 6,0 | 6,5",
                "ok", [], {},
            )

        class Page:
            def bring_to_front(self):
                pass

        controller._login_schoolnet_if_needed = login
        controller._navigate_and_snapshot = navigate
        with patch("resumen_escolar.automation.schoolnet_retry_delays", return_value=[0, 600]), \
             patch("resumen_escolar.app.time.sleep", side_effect=AssertionError("retry should be immediate")):
            snapshots = controller._collect_schoolnet_snapshots_or_login_message(Page())

        self.assertEqual(len(login_calls), 2)
        self.assertEqual(len(grade_calls), 2)
        self.assertEqual(snapshots[1].status, "ok")
        self.assertIn("Matematica | 6,0 | 6,5", snapshots[1].text)

    def test_schoolnet_does_not_claim_recovery_while_internal_view_still_needs_login(self):
        controller = object.__new__(BrowserController)
        controller._progress = lambda *_args: 0
        controller._login_schoolnet_if_needed = lambda _page: {"ok": True, "attempted": True}
        controller._collect_schoolnet_snapshots = lambda _page: [
            PageSnapshot(
                platform="SchoolNet - Calificaciones",
                title="SchoolNet",
                url="https://schoolnet.colegium.com/webapp/es_CL/login",
                text="",
                status="needs_login",
                notes=[],
                stats={},
            )
        ]
        with patch("resumen_escolar.automation.schoolnet_retry_delays", return_value=[0, 600]), \
             patch("resumen_escolar.app.time.sleep", side_effect=AssertionError("retry should be immediate")):
            result = controller._collect_schoolnet_snapshots_or_login_message(object())
        self.assertEqual(result[0].status, "needs_login")
        self.assertNotIn("se recupero la sesion", " ".join(result[0].notes))

    def test_google_redirect_is_reported_as_login_instead_of_route_error(self):
        class RedirectPage:
            url = ""

            def bring_to_front(self):
                pass

            def goto(self, *_args, **_kwargs):
                self.url = "https://accounts.google.com/v3/signin/confirmidentifier"

        controller = object.__new__(BrowserController)
        controller._progress = lambda *_args: 0
        controller._progress_done = lambda *_args: None
        controller._wait_after_classroom_interaction = lambda *_args: None
        snapshots = controller._collect_classroom_snapshots(RedirectPage())
        self.assertEqual(snapshots[0].status, "needs_login")

    def test_classroom_content_mentioning_sign_in_is_not_a_login_page(self):
        self.assertIsNone(
            login_status_note(
                "Google Classroom - Trabajo de clase",
                "https://classroom.google.com/w/course/t/all",
                "Tarea de inglés: sign in to the reading portal",
            )
        )

    def test_classroom_route_error_does_not_request_manual_login(self):
        error = "Google Classroom - Trabajo de clase=error (ruta global incorrecta)"
        self.assertIsNone(login_recovery(error))
        category, _, step = failure_diagnosis(error)
        self.assertEqual(category, "classroom_navigation")
        self.assertIn("ruta", step.lower())

    def test_missing_bucket_is_configuration_error(self):
        category, _, step = failure_diagnosis("Falta --bucket-name o RESUMEN_ESCOLAR_BUCKET.")
        self.assertEqual(category, "configuration")
        self.assertIn("RESUMEN_ESCOLAR_BUCKET", step)

    def test_failure_notification_does_not_send_raw_school_data(self):
        args = argparse.Namespace(notification_topic_id="topic", oci_auth="", oci_profile="", oci_config_file="", oci_region="")
        commands = []
        with patch("resumen_escolar.automation.run_oci", side_effect=commands.append):
            result = publish_failure_notification(
                args,
                "Generacion incompleta; no se publica. Estados bloqueados: SchoolNet - Calificaciones=error (Alumno Secreto: nota 2,0)",
            )
        self.assertIsNotNone(result)
        command = commands[0]
        body = command[command.index("--body") + 1]
        self.assertNotIn("Alumno Secreto", body)
        self.assertIn("SchoolNet", body)


if __name__ == "__main__":
    unittest.main()
