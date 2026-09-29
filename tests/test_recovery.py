import datetime as dt
import unittest

from resumen_escolar.recovery import RecoveryAction, RecoverySignal, decide_next


def signal(**changes):
    base = dict(
        now=dt.datetime(2026, 8, 19, 3, 0, tzinfo=dt.timezone.utc),
        deadline=dt.datetime(2026, 8, 19, 6, 30, tzinfo=dt.timezone.utc),
        category="page_not_ready", authenticated=False, form_ready=False,
        username_field_present=False, password_field_present=False,
        submit_present=False, document_ready=True, cdp_ready=True,
        profile_ready=True, novnc_active=False, page_count=1, frame_count=1,
        visible_input_count=0, browser_restart_count=0,
        last_browser_restart_at=None, action_history=(),
    )
    base.update(changes)
    return RecoverySignal(**base)


class RecoveryPolicyTests(unittest.TestCase):
    def test_stuck_login_page_reloads_before_another_login_attempt(self):
        decision = decide_next(signal(action_history=("wait_for_form",)))
        self.assertEqual(decision.action, RecoveryAction.RELOAD_PAGE)

    def test_manual_session_prevents_browser_restart(self):
        decision = decide_next(signal(
            action_history=("wait_for_form", "reload_page", "navigate_login", "replace_page", "reconnect_cdp"),
            novnc_active=True,
        ))
        self.assertEqual(decision.action, RecoveryAction.STOP)
        self.assertEqual(decision.category, "browser_restart_blocked_novnc")

    def test_deadline_and_manual_auth_stop(self):
        self.assertTrue(decide_next(signal(category="captcha_required")).terminal)
        self.assertEqual(
            decide_next(signal(now=dt.datetime(2026, 8, 19, 6, 30, tzinfo=dt.timezone.utc))).category,
            "deadline_reached",
        )


if __name__ == "__main__":
    unittest.main()
