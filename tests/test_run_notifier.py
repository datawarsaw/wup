#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path: sys.path.insert(0, str(SCRIPTS_DIR))
from run_notifier import actionable_findings, execute, format_email, format_telegram_result

def report(latest_opencodex: str = "2.34.0", release_url: str = "https://www.npmjs.com/package/@bitkyc08/opencodex/v/2.34.0"):
    return {"tools": [
        {"name": "OpenCodex", "status": "UPDATE", "installed_version": "2.31.0", "latest_version": latest_opencodex, "health": "HEALTHY", "release_url": release_url, "update_recommendation": {"proposed_command": "npm install -g @bitkyc08/opencodex@2.34.0"}},
        {"name": "Wrangler", "status": "UPDATE", "installed_version": "4.30.0", "latest_version": "4.32.0", "health": "HEALTHY", "release_url": "https://www.npmjs.com/package/wrangler/v/4.32.0"},
        {"name": "Node.js", "status": "WATCH", "installed_version": "22.0.0", "latest_version": "22.1.0", "health": "HEALTHY", "release_url": "https://nodejs.org/dist/v22.1.0/"},
        {"name": "Python", "status": "WATCH", "installed_version": "3.13.0", "latest_version": "3.13.1", "health": "HEALTHY", "release_url": "https://docs.python.org/3.13/whatsnew/"},
        {"name": "Git", "status": "WATCH", "installed_version": "2.51.0", "latest_version": "2.52.0", "health": "HEALTHY", "release_url": "https://github.com/git-for-windows/git/releases"},
        {"name": "Codex CLI", "status": "CURRENT", "installed_version": "1", "latest_version": "1", "health": "HEALTHY"},
        {"name": "LM Studio", "status": "UNKNOWN", "installed_version": "x", "latest_version": "unknown", "health": "UNVERIFIED"}]}

class RunnerTests(unittest.TestCase):
    def test_filters_and_renders_both_channels(self):
        findings = actionable_findings(report())
        self.assertEqual([item["name"] for item in findings], ["OpenCodex", "Wrangler", "Node.js", "Python", "Git"])
        telegram = format_telegram_result(findings)
        subject, text, html = format_email(findings, date(2026, 8, 28))
        self.assertIn("UPDATE: OpenCodex", telegram); self.assertIn("WATCH: Node.js", telegram)
        self.assertEqual(subject, "WUP Toolchain Update Watch  UPDATE  2026-08-28")
        self.assertIn("URGENT", text) if "URGENT" in text else None
        self.assertIn("npm install", text); self.assertIn("WUP", html)
        self.assertIn("What changed:", text); self.assertIn("https://www.npmjs.com/package/@bitkyc08/opencodex/v/2.34.0", text)
        self.assertIn('href="https://www.npmjs.com/package/@bitkyc08/opencodex/v/2.34.0"', html)
        self.assertNotIn("CURRENT", text); self.assertNotIn("LM Studio", text)

    def test_email_html_renders_all_severities_and_optional_sections(self):
        findings = [
            {"name": "Critical Tool", "status": "URGENT", "installed_version": "1.0.0", "latest_version": "2.0.0", "health": "DEGRADED", "release_url": "https://example.test/urgent", "proposed_action": "tool update --force"},
            {"name": "Update Tool", "status": "UPDATE", "installed_version": "3.0.0", "latest_version": "3.1.0", "health": "HEALTHY", "proposed_action": "tool update"},
            {"name": "Watch Tool", "status": "WATCH", "installed_version": "4.0.0", "latest_version": "4.0.1", "health": "UNVERIFIED", "release_url": "https://example.test/watch"},
        ]
        _, text, rendered = format_email(findings, date(2026, 8, 29))
        for status in ("URGENT", "UPDATE", "WATCH"):
            self.assertIn(f"{status} &nbsp; 1 finding(s)", rendered)
            self.assertIn(status, text)
        self.assertIn("3 actionable finding(s)", rendered)
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1.0">', rendered)
        self.assertIn('<table role="presentation" width="640" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:640px;', rendered)
        self.assertIn('href="https://example.test/urgent"', rendered)
        self.assertIn('href="https://example.test/watch"', rendered)
        self.assertNotIn("What changed</div><div", rendered.split("Update Tool", 1)[1].split("Watch Tool", 1)[0])
        self.assertIn("tool update --force", text)
        self.assertIn("tool update", rendered)
        self.assertIn("Automated read-only workstation audit", rendered)

    def test_email_html_escapes_dynamic_content_and_keeps_plain_text_complete(self):
        finding = {"name": "Tool <unsafe>", "status": "UPDATE", "installed_version": "1<&", "latest_version": "2>&", "health": "HEALTHY <ok>", "release_url": 'https://example.test/?q="quoted"', "proposed_action": "tool <update> && echo 'ok'"}
        _, text, rendered = format_email([finding], date(2026, 8, 29))
        self.assertIn("Tool &lt;unsafe&gt;", rendered)
        self.assertIn("1&lt;&amp;", rendered)
        self.assertIn("HEALTHY &lt;ok&gt;", rendered)
        self.assertIn('href="https://example.test/?q=&quot;quoted&quot;"', rendered)
        self.assertIn("tool &lt;update&gt; &amp;&amp; echo &#x27;ok&#x27;", rendered)
        self.assertIn("Tool <unsafe>", text)
        self.assertIn("tool <update> && echo 'ok'", text)

    def test_email_escapes_a_trustworthy_url_safely(self):
        findings = actionable_findings(report(release_url='https://example.test/?q="quoted"'))
        _, _, html = format_email(findings)
        self.assertIn('href="https://example.test/?q=&quot;quoted&quot;"', html)

    def test_first_run_no_change_and_material_change(self):
        with tempfile.TemporaryDirectory() as temp:
            state, telegram, email = Path(temp) / "state.json", [], []
            ts = lambda result: telegram.append(result) or True
            es = lambda subject, text, body: email.append((subject, text, body)) or True
            self.assertEqual(execute(state, audit_runner=report, telegram_sender=ts, email_sender=es), 0)
            self.assertTrue(state.exists()); self.assertEqual((len(telegram), len(email)), (1, 1))
            self.assertEqual(execute(state, audit_runner=report, telegram_sender=ts, email_sender=es), 0)
            self.assertEqual((len(telegram), len(email)), (1, 1))
            self.assertEqual(execute(state, audit_runner=lambda: report("2.35.0"), telegram_sender=ts, email_sender=es), 0)
            self.assertIn("OpenCodex 2.31.0->2.35.0", telegram[-1]); self.assertNotIn("Wrangler", telegram[-1])

    def test_failed_email_preserves_good_state_and_avoids_duplicate_telegram(self):
        with tempfile.TemporaryDirectory() as temp:
            state, telegram = Path(temp) / "state.json", []
            self.assertEqual(execute(state, audit_runner=report, telegram_sender=lambda text: telegram.append(text) or True, email_sender=lambda *_: True), 0)
            previous = json.loads(state.read_text(encoding="utf-8"))["last_alerted"]
            self.assertEqual(execute(state, audit_runner=lambda: report("2.35.0"), telegram_sender=lambda text: telegram.append(text) or True, email_sender=lambda *_: False), 1)
            pending = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(pending["last_alerted"], previous)
            self.assertEqual(len(telegram), 2)
            self.assertEqual(execute(state, audit_runner=lambda: report("2.35.0"), telegram_sender=lambda text: telegram.append(text) or True, email_sender=lambda *_: True), 0)
            self.assertEqual(len(telegram), 2)

    def test_dry_run_never_notifies_or_writes_state(self):
        with tempfile.TemporaryDirectory() as temp:
            state, called = Path(temp) / "state.json", []
            self.assertEqual(execute(state, dry_run=True, audit_runner=report, telegram_sender=lambda *_: called.append(True), email_sender=lambda *_: called.append(True)), 0)
            self.assertFalse(state.exists()); self.assertEqual(called, [])

    def test_release_url_is_informational_and_does_not_trigger_duplicate_alert(self):
        with tempfile.TemporaryDirectory() as temp:
            state, telegram, email = Path(temp) / "state.json", [], []
            sender = lambda result: telegram.append(result) or True
            mailer = lambda *args: email.append(args) or True
            self.assertEqual(execute(state, audit_runner=report, telegram_sender=sender, email_sender=mailer), 0)
            self.assertEqual(execute(state, audit_runner=lambda: report(release_url="https://example.invalid/corrected"), telegram_sender=sender, email_sender=mailer), 0)
            self.assertEqual((len(telegram), len(email)), (1, 1))

    def test_failed_audit_preserves_state_and_attempts_failed_telegram(self):
        with tempfile.TemporaryDirectory() as temp:
            state, failed = Path(temp) / "state.json", []
            original = '{"version": 2, "last_alerted": {"keep": {}}, "pending": {}}\n'
            state.write_text(original, encoding="utf-8")
            self.assertEqual(execute(state, audit_runner=lambda: (_ for _ in ()).throw(RuntimeError("invalid JSON")), failed_telegram_sender=lambda message: failed.append(message) or True), 1)
            self.assertEqual(state.read_text(encoding="utf-8"), original)
            self.assertEqual(len(failed), 1)
            self.assertIn("invalid JSON", failed[0])

if __name__ == "__main__": unittest.main()
