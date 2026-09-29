import tempfile
import unittest
from pathlib import Path

from resumen_escolar.automation import (
    failure_diagnosis,
    login_recovery,
    format_login_diagnostics,
    schoolnet_retry_delays,
    validate_generated_output,
)
from resumen_escolar.app import AppError, BrowserController, PageSnapshot


class LoginRecoveryTests(unittest.TestCase):
    def test_closing_cdp_controller_detaches_without_closing_shared_context(self):
        class FakeContext:
            closed = False

            def close(self):
                self.closed = True

        class FakePlaywright:
            stopped = False

            def stop(self):
                self.stopped = True

        controller = object.__new__(BrowserController)
        context = FakeContext()
        playwright = FakePlaywright()
        controller._context = context
        controller._playwright = playwright
        controller._browser_started_at = 1.0
        controller._cdp_attached = True

        controller._close_on_browser_thread()

        self.assertFalse(context.closed)
        self.assertTrue(playwright.stopped)
        self.assertIsNone(controller._context)

    def test_schoolnet_needs_login_includes_schoolnet_command(self):
        recovery = login_recovery(
            "Generacion incompleta; Estados bloqueados: SchoolNet - Calificaciones=needs_login (SchoolNet parece estar en pantalla de login.)"
        )
        self.assertIsNotNone(recovery)
        platform, command, next_step = recovery
        self.assertEqual(platform, "SchoolNet")
        self.assertIn("-StartMode SchoolNet", command)
        self.assertIn("Asterisk.Play", command)
        self.assertIn("RESULTADO: FALLO", next_step)
        self.assertIn("codex-vm-login-session.log", next_step)

    def test_both_platforms_return_single_combined_command(self):
        recovery = login_recovery(
            "SchoolNet - Calificaciones=needs_login (SchoolNet parece estar en pantalla de login), Google Classroom=needs_login"
        )
        self.assertIsNotNone(recovery)
        platform, command, next_step = recovery
        self.assertEqual(platform, "SchoolNet y Classroom")
        self.assertIn("-StartMode Both", command)
        self.assertIn("ambas plataformas", next_step)
        self.assertIn("etapa 10", next_step)

    def test_non_login_error_has_no_recovery_command(self):
        self.assertIsNone(login_recovery("OCI CLI fallo con exit=1"))

    def test_cdp_refused_is_browser_not_login_recovery(self):
        error = "BrowserType.connect_over_cdp: connect ECONNREFUSED 127.0.0.1:9222"
        self.assertIsNone(login_recovery(error))
        category, subject, next_step = failure_diagnosis(error)
        self.assertEqual(category, "browser")
        self.assertIn("navegador", subject)
        self.assertIn("No inicies login manual", next_step)

    def test_vault_error_is_not_manual_login_recovery(self):
        error = "SchoolNet requiere login y no hay credenciales configuradas. Configura OCI Vault."
        self.assertIsNone(login_recovery(error))
        category, subject, next_step = failure_diagnosis(error)
        self.assertEqual(category, "vault")
        self.assertIn("SchoolNet", subject)
        self.assertIn("dynamic group", next_step)

    def test_blocked_snapshot_is_reported_before_legacy_prompt_grade_guard(self):
        result = {
            "live_snapshots": [
                PageSnapshot(
                    "SchoolNet - Calificaciones",
                    "SchoolNet",
                    "https://schoolnet.example/grades",
                    "",
                    "error",
                    ["No pude construir la tabla canonica P1/P2 despues de 3 intentos."],
                    {},
                )
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            prompt = Path(directory) / "prompt.txt"
            prompt.write_text("prompt sin tabla", encoding="utf-8")
            with self.assertRaisesRegex(AppError, "Estados bloqueados"):
                validate_generated_output(result, prompt, min_bytes=1, allow_blocked=False)

    def test_structured_grade_error_is_not_a_manual_login_recovery(self):
        error = "SchoolNet - Calificaciones=needs_login (No pude construir una lectura estructurada de la tabla de calificaciones P1/P2.)"
        self.assertIsNone(login_recovery(error))
        category, subject, next_step = failure_diagnosis(error)
        self.assertEqual(category, "extraction")
        self.assertIn("Calificaciones", subject)
        self.assertIn("No reinicies", next_step)

    def test_cron_uses_the_cdp_guard_before_playwright(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "run_daily_report.sh").read_text(encoding="utf-8")
        self.assertIn("ensure_chromium_cdp.sh", script)
        self.assertLess(script.index('"$BROWSER_GUARD"'), script.index("-m resumen_escolar.automation run"))

    def test_cdp_guard_does_not_kill_an_occupied_profile(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "ensure_chromium_cdp.sh").read_text(encoding="utf-8")
        self.assertIn("browser_profile_busy_without_cdp", script)
        self.assertIn("do_not_kill_existing_browser", script)
        self.assertNotIn("kill -TERM", script)

    def test_code_only_deploy_preserves_existing_config_when_bucket_is_not_supplied(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "deploy_to_oracle_form_vm.ps1").read_text(encoding="utf-8")
        self.assertIn("config-preserved=true reason=bucket_not_provided", script)

    def test_schoolnet_login_uses_native_form_submit_fallback(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "resumen_escolar" / "app.py").read_text(encoding="utf-8")
        self.assertIn("requestSubmit", source)
        self.assertIn("closest('form')", source)
        self.assertIn("Mensaje visible:", source)

    def test_schoolnet_retry_budget_has_six_attempts_over_one_hour(self):
        delays = schoolnet_retry_delays()
        self.assertEqual(len(delays), 6)
        self.assertEqual(sum(delays), 3000)

    def test_login_diagnostics_is_copyable_and_redacted(self):
        text = format_login_diagnostics(
            run_id="20260811T030001Z-abc123",
            attempts=[
                {"attempt": 1, "stage": "detect_form", "result": "not_found", "url": "https://schoolnet.colegium.com/webapp/es_CL/login", "visible_inputs": 0},
                {"attempt": 2, "stage": "submit", "result": "rejected", "visible_inputs": 2, "message": "Clave incorrecta usuario martin@example.com"},
            ],
            vault_status="available",
            cdp_status="ready",
            profile_status="reused",
            final_reason="login_form_not_detected",
        )
        self.assertIn("RUN_ID: 20260811T030001Z-abc123", text)
        self.assertIn("ATTEMPTS: 2", text)
        self.assertIn("VAULT: available", text)
        self.assertNotIn("martin@example.com", text)
        self.assertNotIn("Clave incorrecta", text)

    def test_login_diagnostics_keeps_non_sensitive_failure_stage(self):
        text = format_login_diagnostics(
            run_id="run-1",
            attempts=[{"attempt": 1, "stage": "automatic_login", "result": "login_failed", "note": "No pude detectar campos de usuario/clave en la pantalla de login SchoolNet.", "recovery_action": "reload_page"}],
            vault_status="available", cdp_status="ready", profile_status="reused", final_reason="page_not_ready",
        )
        self.assertIn("No pude detectar campos de usuario/clave", text)
        self.assertIn("recovery_action=reload_page", text)


if __name__ == "__main__":
    unittest.main()
