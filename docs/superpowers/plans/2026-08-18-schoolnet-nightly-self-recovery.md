# SchoolNet Nightly Self-Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Recover the nightly SchoolNet report from technical failures between 03:00 and 06:30 Chile time, and send a detailed, redacted OCI diagnostic email when recovery cannot finish.

**Architecture:** A pure Python policy chooses one state-changing recovery action from safe browser signals. BrowserController performs browser operations, automation.py persists events/publishes, and the Bash runner owns the single lock, heartbeat, and immutable deadline.

**Tech Stack:** Python 3.11, unittest, Playwright sync API over CDP, Bash, cron, OCI CLI instance principal, OCI Notifications, PowerShell.

**Spec:** docs/superpowers/specs/2026-08-18-schoolnet-nightly-self-recovery-design.md

## Global Constraints

- Work only in C:\Users\Martin\Documents\Codex\resumen_escolar.
- Preserve config/automation.env, .runtime/chrome-profile, cache, outbox, logs, and last valid report.
- The window is 03:00–06:30 America/Santiago. Success requires authentication, extraction, and publication.
- Never log credentials, input values, cookies, HTML, tokens, email addresses, personal data, OCIDs, IPs, or key paths.
- Stop immediately on password rejection, lockout, CAPTCHA, MFA, or manual verification.
- Restart Chromium only when no noVNC session is active; preserve profile and enforce a 1,200-second cooldown.
- Do not change OCI IAM, network, DNS, firewall, VPN, Security Lists, or NSGs.
- Use /opt/resumen-escolar/.venv/bin/python on the VM.
- Use scripts/deploy_to_oracle_form_vm.ps1 to deploy.
- Before each commit inspect git status --short --branch and stage only task files.

---

## File Structure

- Create: resumen_escolar/recovery.py — recovery policy, classifications, deadline, cooldown, decisions.
- Create: resumen_escolar/recovery_diagnostics.py — JSONL writer and OCI diagnostic renderer.
- Modify: resumen_escolar/app.py — safe page probe, page actions, CDP reconnect, policy integration.
- Modify: resumen_escolar/automation.py — deadline/event context, detailed terminal email, publish-only retry.
- Modify: scripts/ensure_chromium_cdp.sh — noVNC detection and explicit safe restart.
- Modify: scripts/run_daily_report.sh — immutable deadline and technical process retry.
- Modify: scripts/deploy_to_oracle_form_vm.ps1 and scripts/verify_vm_schoolnet_auto_recovery.ps1 — backup, package completeness, validation.
- Modify: README.md and AGENTS.md — operator workflow and PowerShell/SSH quote lesson.
- Create: tests/test_recovery.py, tests/test_recovery_diagnostics.py, tests/test_runner_contract.py.
- Modify: tests/test_automation.py.

## Stable Interfaces

~~~python
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
    now: datetime
    deadline: datetime
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
    last_browser_restart_at: datetime | None
    action_history: tuple[str, ...]

@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    category: str
    terminal: bool
    reason: str
    next_action: str

def classify_failure(message: str) -> str: ...
def is_manual_auth_category(category: str) -> bool: ...
def decide_next(signal: RecoverySignal) -> RecoveryDecision: ...
~~~

~~~python
@dataclass(frozen=True)
class RecoveryEvent:
    timestamp: str
    run_id: str
    cycle: int
    attempt: int
    stage: str
    action: str
    result: str
    duration_ms: int
    failure_category: str
    stop_reason: str
    next_action: str
    signals: dict[str, object]
    safe_message: str

def append_event(path: Path, event: RecoveryEvent) -> None: ...
def render_human_event(event: RecoveryEvent) -> str: ...
def render_oci_diagnostic(events: Sequence[RecoveryEvent], *, result: str, remote_log_path: str, max_bytes: int) -> str: ...
~~~

### Task 1: Pure recovery policy

**Files:**
- Create: resumen_escolar/recovery.py
- Create: tests/test_recovery.py

**Consumes:** Nothing external: no Playwright, environment, filesystem, OCI, or sleep.

**Produces:** RecoveryAction, RecoverySignal, RecoveryDecision, classification, and decisions for Tasks 2–5.

- [ ] **Step 1: Write failing tests**

~~~python
def signal(**changes):
    base = dict(
        now=dt.datetime(2026, 8, 18, 3, 0, tzinfo=dt.timezone.utc),
        deadline=dt.datetime(2026, 8, 18, 6, 30, tzinfo=dt.timezone.utc),
        category="page_not_ready", authenticated=False, form_ready=False,
        username_field_present=False, password_field_present=False,
        submit_present=False, document_ready=True, cdp_ready=True,
        profile_ready=True, novnc_active=False, page_count=1, frame_count=1,
        visible_input_count=0, browser_restart_count=0,
        last_browser_restart_at=None, action_history=(),
    )
    base.update(changes)
    return RecoverySignal(**base)

def test_missing_form_escalates_from_wait_to_reload():
    decision = decide_next(signal(action_history=("wait_for_form",)))
    self.assertEqual(decision.action, RecoveryAction.RELOAD_PAGE)

def test_manual_auth_is_terminal():
    decision = decide_next(signal(category="manual_required"))
    self.assertEqual(decision.action, RecoveryAction.STOP)
    self.assertTrue(decision.terminal)

def test_novnc_blocks_restart():
    decision = decide_next(signal(
        action_history=("wait_for_form", "reload_page", "navigate_login", "replace_page", "reconnect_cdp"),
        novnc_active=True,
    ))
    self.assertEqual(decision.action, RecoveryAction.STOP)
    self.assertIn("noVNC", decision.reason)

def test_deadline_is_terminal():
    decision = decide_next(signal(now=dt.datetime(2026, 8, 18, 6, 30, tzinfo=dt.timezone.utc)))
    self.assertEqual(decision.category, "deadline_reached")
~~~

- [ ] **Step 2: Verify failure**

Run: python -m unittest tests.test_recovery -v

Expected: FAIL with ModuleNotFoundError for resumen_escolar.recovery.

- [ ] **Step 3: Implement the deterministic policy**

~~~python
RECOVERY_LADDER = (
    RecoveryAction.WAIT_FOR_FORM,
    RecoveryAction.RELOAD_PAGE,
    RecoveryAction.NAVIGATE_LOGIN,
    RecoveryAction.REPLACE_PAGE,
    RecoveryAction.RECONNECT_CDP,
    RecoveryAction.RESTART_BROWSER,
)
RESTART_COOLDOWN = dt.timedelta(seconds=1200)

def decide_next(signal: RecoverySignal) -> RecoveryDecision:
    if signal.now >= signal.deadline:
        return RecoveryDecision(RecoveryAction.STOP, "deadline_reached", True, "Se alcanzo la hora limite 06:30.", "none")
    if is_manual_auth_category(signal.category):
        return RecoveryDecision(RecoveryAction.STOP, signal.category, True, "SchoolNet requiere intervencion manual.", "none")
    if signal.authenticated:
        return RecoveryDecision(RecoveryAction.EXTRACT, "none", False, "Sesion autenticada confirmada.", "extract")
    if signal.form_ready:
        return RecoveryDecision(RecoveryAction.ATTEMPT_LOGIN, "none", False, "Formulario SchoolNet disponible.", "attempt_login")
    for action in RECOVERY_LADDER:
        if action.value not in signal.action_history:
            return RecoveryDecision(action, signal.category, False, "Escalamiento tecnico controlado.", action.value)
    return RecoveryDecision(RecoveryAction.STOP, "recovery_exhausted", True, "No quedan acciones tecnicas seguras.", "none")
~~~

Map captcha, mfa, verification, bloqueo, and explicit invalid-password messages to terminal categories. Map no-form, CDP, network, Vault, extraction, and publication failures to stable technical categories.

- [ ] **Step 4: Add tests for form_ready, authenticated, CDP, Vault, extraction, publish, unknown technical, and restart cooldown**

Use a recent last_browser_restart_at to assert browser_restart_cooldown rather than RESTART_BROWSER.

- [ ] **Step 5: Verify and commit**

Run: python -m unittest tests.test_recovery -v

Expected: PASS.

~~~powershell
git status --short --branch
git add -- resumen_escolar/recovery.py tests/test_recovery.py
git commit -m "feat: add SchoolNet recovery policy"
~~~

### Task 2: Redacted JSONL and detailed OCI diagnostic

**Files:**
- Create: resumen_escolar/recovery_diagnostics.py
- Create: tests/test_recovery_diagnostics.py

**Consumes:** RecoveryEvent interface.

**Produces:** Safe JSONL persistence and bounded human/OCI rendering for Tasks 3–4.

- [ ] **Step 1: Write failing tests**

~~~python
def event(**changes):
    base = dict(
        timestamp="2026-08-18T03:00:01-04:00", run_id="schoolnet-abc",
        cycle=2, attempt=3, stage="schoolnet_probe", action="reload_page",
        result="failed", duration_ms=1432, failure_category="page_not_ready",
        stop_reason="", next_action="navigate_login",
        signals={"url_host": "schoolnet.colegium.com", "url_path": "/webapp/es_CL/login", "visible_input_count": 0},
        safe_message="Formulario no disponible.",
    )
    base.update(changes)
    return RecoveryEvent(**base)

def test_append_event_writes_one_redacted_json_object(tmp_path):
    path = tmp_path / "recovery.jsonl"
    append_event(path, event(safe_message="usuario martin@example.com password=secret"))
    text = path.read_text(encoding="utf-8")
    self.assertEqual(json.loads(text)["action"], "reload_page")
    self.assertNotIn("martin@example.com", text)
    self.assertNotIn("secret", text)

def test_oci_render_keeps_final_event_when_truncated():
    events = [event(attempt=n, safe_message="x" * 500) for n in range(1, 30)]
    rendered = render_oci_diagnostic(events, result="failed", remote_log_path="/opt/resumen-escolar/logs/recovery.jsonl", max_bytes=2200)
    self.assertIn("DIAGNOSTICO PARA CODEX", rendered)
    self.assertIn("ETAPA_FINAL", rendered)
    self.assertIn("ATTEMPT=29", rendered)
    self.assertLessEqual(len(rendered.encode("utf-8")), 2200)
~~~

- [ ] **Step 2: Verify failure**

Run: python -m unittest tests.test_recovery_diagnostics -v

Expected: FAIL with ModuleNotFoundError for resumen_escolar.recovery_diagnostics.

- [ ] **Step 3: Implement safe output**

Whitelist only url_host, url_path, document_ready, page_count, frame_count, visible_input_count, username_field_present, password_field_present, submit_present, vault_status, cdp_status, profile_status, novnc_active, browser_restart_count, report_generated, report_published, and notification_published.

Write one compact UTF-8 JSON object per line. Render summary, timing/deadline, final stage/action/category, signals, ordered events, remote log path, then DIAGNOSTICO PARA CODEX. When over budget retain summary, first two, terminal events, last eight, and an omitted-event count.

- [ ] **Step 4: Add redaction tests**

Test email address, token-shaped text, Windows private-key path, cookie-like value, HTML fragment, and a 5,000-character raw message. Assert all are absent. Assert human event output includes RUN_ID, STAGE, ACTION, RESULT, DURATION_MS, CATEGORY, and NEXT_ACTION.

- [ ] **Step 5: Verify and commit**

Run: python -m unittest tests.test_recovery_diagnostics -v

Expected: PASS.

~~~powershell
git status --short --branch
git add -- resumen_escolar/recovery_diagnostics.py tests/test_recovery_diagnostics.py
git commit -m "feat: add SchoolNet recovery diagnostics"
~~~

### Task 3: BrowserController page recovery

**Files:**
- Modify: resumen_escolar/app.py in BrowserController methods near lines 3857–4650 and _find_page near line 5334.
- Modify: tests/test_recovery.py

**Consumes:** Tasks 1–2.

**Produces:** recover_and_collect_schoolnet and structured events for Task 4.

- [ ] **Step 1: Write failing fake-browser tests**

Create fake page/context objects exposing url, reload, goto, evaluate, wait_for_timeout, close, new_page, and pages.

~~~python
def test_zero_input_page_reloads_before_second_login_attempt():
    controller, page = controller_with_fake_schoolnet([
        {"form_ready": False, "visible_input_count": 0},
        {"form_ready": True, "visible_input_count": 2},
    ])
    controller.recover_and_collect_schoolnet(deadline=deadline(), emit_event=events.append)
    self.assertEqual(page.reload_calls, 1)
    self.assertLess(page.reload_call_index, page.login_attempt_index)

def test_stuck_page_is_replaced_after_reload_and_navigation():
    controller, old_page, context = controller_with_stuck_schoolnet()
    controller.recover_and_collect_schoolnet(deadline=deadline(), emit_event=events.append)
    self.assertTrue(old_page.closed)
    self.assertEqual(context.new_page_calls, 1)

def test_probe_does_not_return_values_or_html():
    probe = controller._probe_schoolnet_page(fake_page_with_sensitive_values())
    self.assertNotIn("secret", repr(probe))
    self.assertNotIn("<html", repr(probe).lower())
~~~

- [ ] **Step 2: Verify failure**

Run: python -m unittest tests.test_recovery.BrowserRecoveryAdapterTests -v

Expected: FAIL because browser recovery methods do not exist.

- [ ] **Step 3: Implement safe page probe**

One evaluate call may return only host, path, document-ready flag, frame count, visible-input count, username/password/submit flags, form_ready, and authenticated. It must not return body text, values, outerHTML, cookies, storage, or URL query strings.

- [ ] **Step 4: Implement one action per policy event**

Add _wait_for_schoolnet_form, _reload_schoolnet_page, _navigate_schoolnet_login, _replace_schoolnet_page, and _reconnect_cdp_context. Create the replacement page and wait for DOM content before closing the identified broken SchoolNet page. Reconnect by calling _discard_browser_state then ensure_started; do not terminate Chromium.

Refactor _collect_schoolnet_snapshots_or_login_message to call recover_and_collect_schoolnet. Probe after every action. Attempt login immediately when form_ready and extract immediately when authenticated. Remove schoolnet_retry_delays from actual recovery control.

- [ ] **Step 5: Separate extraction from login**

If login succeeds but grades remain unstructured after one reload, emit extraction_changed and stop login recovery. Never classify that as needs_login.

- [ ] **Step 6: Verify and commit**

Run: python -m unittest tests.test_recovery tests.test_automation -v

Expected: PASS. Historic no-form behavior must include wait_for_form then reload_page, never six unchanged automatic_login entries.

~~~powershell
git status --short --branch
git add -- resumen_escolar/app.py tests/test_recovery.py tests/test_automation.py
git commit -m "feat: recover stuck SchoolNet browser pages"
~~~

### Task 4: Automation, publication retry, and OCI notification

**Files:**
- Modify: resumen_escolar/automation.py around diagnostics, failure diagnosis, cmd_run, and main.
- Modify: tests/test_automation.py

**Consumes:** Browser events from Task 3 and renderer from Task 2.

**Produces:** Fixed recovery deadline, exact terminal email, publish-only retry for Task 5.

- [ ] **Step 1: Write failing tests**

~~~python
def test_failure_email_has_terminal_stage_and_history():
    body = render_failure_body([
        event(stage="schoolnet_probe", action="reload_page", result="failed"),
        event(stage="browser_guard", action="restart_browser", result="blocked",
              failure_category="novnc_active", stop_reason="Sesion manual activa"),
    ])
    self.assertIn("ETAPA_FINAL: browser_guard", body)
    self.assertIn("ACCION_FINAL: restart_browser", body)
    self.assertIn("DIAGNOSTICO PARA CODEX", body)

def test_publish_retry_does_not_generate_again():
    with patch("resumen_escolar.automation.generate_prompt_once", return_value=successful_result()) as generate:
        with patch("resumen_escolar.automation.publish_daily_report", side_effect=[AppError("timeout"), published()]) as publish:
            result = cmd_run(args_with_publish())
    self.assertEqual(generate.call_count, 1)
    self.assertEqual(publish.call_count, 2)
    self.assertTrue(result["published"])
~~~

- [ ] **Step 2: Verify failure**

Run: python -m unittest tests.test_automation -v

Expected: FAIL because detailed body and publish-only retry do not exist.

- [ ] **Step 3: Add recovery run context**

Create RecoveryRunContext with run_id, timezone-aware deadline, JSONL path, event list, and emit. Add --recovery-deadline to the run parser. The shell passes an ISO timestamp so every restarted Python process keeps the original 06:30 deadline. Pass deadline and emit into generate_prompt_once and BrowserController.

- [ ] **Step 4: Render precise final notifications**

Use render_oci_diagnostic in publish_failure_notification. Set title to:

~~~text
Resumen Escolar <resultado> <categoria> etapa=<stage> - YYYY-MM-DD
~~~

Include final stage/action/category, elapsed time, deadline, report-preserved status, ordered history, and remote JSONL path. Keep login_recovery only for manual-auth categories; technical page/browser categories must not recommend manual login.

- [ ] **Step 5: Retry only publication**

Write daily_report.txt and daily_report_state.json once after browser verification. Retry only publish_daily_report, publish_notification, and baseline save using delays (0, 60, 180, 300). Emit publish_transient events. Do not call generate_prompt_once or load_schoolnet_secret_into_env again.

- [ ] **Step 6: Verify and commit**

Run: python -m unittest tests.test_automation tests.test_recovery_diagnostics tests.test_daily_report -v

Expected: PASS.

~~~powershell
git status --short --branch
git add -- resumen_escolar/automation.py tests/test_automation.py
git commit -m "feat: publish detailed recovery diagnostics"
~~~

### Task 5: Safe Chromium restart and recovery runner

**Files:**
- Modify: scripts/ensure_chromium_cdp.sh
- Modify: scripts/run_daily_report.sh
- Create: tests/test_runner_contract.py

**Consumes:** Deadline and terminal marker from Task 4.

**Produces:** Guarded restart, immutable deadline, retry, lock, and heartbeats.

- [ ] **Step 1: Write failing script-contract tests**

~~~python
def test_default_guard_is_non_destructive_and_restart_is_explicit():
    source = read("scripts/ensure_chromium_cdp.sh")
    self.assertIn("RESTART_SAFE=", source)
    self.assertIn("login-session", source)
    default_part = source.split("--restart-safe", 1)[0]
    self.assertNotIn("kill -TERM", default_part)

def test_runner_passes_immutable_deadline():
    source = read("scripts/run_daily_report.sh")
    self.assertIn("RESUMEN_ESCOLAR_RECOVERY_DEADLINE_AT", source)
    self.assertIn("--recovery-deadline", source)
    self.assertIn("06:30", source)
~~~

- [ ] **Step 2: Verify failure**

Run: python -m unittest tests.test_runner_contract -v

Expected: FAIL because restart mode and deadline are absent.

- [ ] **Step 3: Implement noVNC guard and restart mode**

Read PID files under $APP_DIR/.runtime/login-session. A live stored PID means noVNC is active. Default guard stays non-destructive. With --restart-safe return BROWSER_CDP_ERROR=novnc_active and exit 24 if active; otherwise target only the configured Chromium/profile process, send TERM, wait 15 seconds, return error if alive, and use existing startup. Never use kill -9 or remove locks/profile data.

- [ ] **Step 4: Implement immutable deadline**

At runner startup calculate one America/Santiago 06:30 ISO timestamp in RESUMEN_ESCOLAR_RECOVERY_DEADLINE_AT. Pass it to every automation run. After an unexpected technical process exit, sleep 30 seconds and retry only while before that same timestamp. Require RECOVERY_TERMINAL=true before suppressing retry. Preserve flock, one-minute heartbeat, and 25-minute warning.

- [ ] **Step 5: Verify and commit**

~~~powershell
python -m unittest tests.test_runner_contract -v
bash -n scripts/run_daily_report.sh
bash -n scripts/ensure_chromium_cdp.sh
~~~

Expected: PASS and Bash syntax exit 0.

~~~powershell
git status --short --branch
git add -- scripts/ensure_chromium_cdp.sh scripts/run_daily_report.sh tests/test_runner_contract.py
git commit -m "feat: add safe nightly browser recovery runner"
~~~

### Task 6: Deploy safely and verify without publishing

**Files:**
- Modify: scripts/deploy_to_oracle_form_vm.ps1
- Modify: scripts/verify_vm_schoolnet_auto_recovery.ps1
- Modify: README.md
- Modify: AGENTS.md
- Modify: tests/test_runner_contract.py

**Consumes:** Tasks 1–5.

**Produces:** Backup/rollback manifest, complete package, non-publish verification, and operator documentation.

- [ ] **Step 1: Write failing deployment-contract tests**

~~~python
def test_deploy_packages_recovery_modules_and_manifest():
    source = read("scripts/deploy_to_oracle_form_vm.ps1")
    self.assertIn("resumen_escolar\\recovery.py", source)
    self.assertIn("resumen_escolar\\recovery_diagnostics.py", source)
    self.assertIn("rollback-manifest", source)

def test_verifier_checks_guard_without_publish():
    source = read("scripts/verify_vm_schoolnet_auto_recovery.ps1")
    self.assertIn("--restart-safe", source)
    self.assertIn("recovery-", source)
    self.assertNotIn("automation run --publish", source)
~~~

- [ ] **Step 2: Verify failure**

Run: python -m unittest tests.test_runner_contract -v

Expected: FAIL because packaging, manifest, and new readiness checks are absent.

- [ ] **Step 3: Package and back up replaceable code only**

Add recovery.py and recovery_diagnostics.py to required package files. Before extraction create $REMOTE_DIR/backups/recovery-YYYYMMDD-HHMMSS. Copy only app.py, automation.py, recovery.py, recovery_diagnostics.py, run_daily_report.sh, and ensure_chromium_cdp.sh when present. Write that path to $REMOTE_DIR/backups/rollback-manifest-latest. Keep automation.env backup. Never copy profile, runtime, outbox, cache, logs, env files, keys, or secrets. Add remote compileall and bash -n checks.

- [ ] **Step 4: Extend verifier**

Verify recovery modules, cron target, deadline config, Vault presence flags, CDP guard, JSONL path capability, and --restart-safe classification. Treat novnc_active as a successful safety observation. Never run automation run --publish. Launch any no-publish functional run with nohup and poll short SSH sessions.

- [ ] **Step 5: Update documents**

README.md must describe recovery window, action ladder, noVNC behavior, manual-auth stops, JSONL path, and email copy/paste contract.

AGENTS.md must record the fixed PowerShell/SSH lesson: encode a complete redacted Bash script as Base64 or upload a temporary script whenever pipes/regexes are needed; do not pass raw quoted pipelines in a PowerShell SSH argument.

- [ ] **Step 6: Verify locally**

~~~powershell
python -m unittest tests.test_recovery tests.test_recovery_diagnostics tests.test_automation tests.test_daily_report tests.test_runner_contract -v
python -m compileall -q resumen_escolar
bash -n scripts/run_daily_report.sh
bash -n scripts/ensure_chromium_cdp.sh
git diff --check
~~~

Expected: all tests and syntax checks pass; diff check has no whitespace error.

- [ ] **Step 7: Commit**

~~~powershell
git status --short --branch
git add -- scripts/deploy_to_oracle_form_vm.ps1 scripts/verify_vm_schoolnet_auto_recovery.ps1 README.md AGENTS.md tests/test_runner_contract.py
git commit -m "docs: document SchoolNet recovery operations"
~~~

- [ ] **Step 8: Controlled OCI rollout**

Use scripts/deploy_to_oracle_form_vm.ps1. Report package, backup, resolution, connection, authentication, upload, extraction, syntax, Vault, CDP, cron, no-publish run, controlled publish run, and result separately.

If Codex cannot read the SSH key or write its log, provide exactly one external PowerShell command with powershell.exe -NoProfile -ExecutionPolicy Bypass -File, disposable log, visible progress markers, and completion sound in finally. Do not retry an SSH timeout.

- [ ] **Step 9: Deployment verification**

Verify module help using /opt/resumen-escolar/.venv/bin/python, both Bash syntax checks, cron target, redacted Vault result, CDP/noVNC result, JSONL events from no-publish run, and rollback-manifest-latest. Do not declare durable success until the next 03:00 execution authenticates, extracts, publishes, or emits the new structured terminal diagnostic.

## Plan Self-Review

### Spec coverage

- Ladder/no-op retry removal: Tasks 1 and 3.
- Deadline, lock, heartbeats, crash retry: Task 5.
- noVNC-safe restart and cooldown: Tasks 1 and 5.
- JSONL, human/OCI diagnostics, redaction: Tasks 2 and 4.
- Extraction/publication separation: Task 4.
- Backup, rollback, deployment, remote verification: Task 6.
- Test-first implementation and next-night validation: Tasks 1–6.

### Placeholder scan

No incomplete implementation markers or unspecified functions/tests are present.

### Type consistency

RecoverySignal, RecoveryDecision, RecoveryAction, and RecoveryEvent are defined before use. BrowserController emits events; automation persists/renders them; Bash passes only the ISO deadline.
