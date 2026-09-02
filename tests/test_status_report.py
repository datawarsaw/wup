from __future__ import annotations

import copy
import html
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from status_report import render_status_html
from status_snapshot import build_status_snapshot


class StatusReportTest(unittest.TestCase):
    def report(self):
        return {
            "audit_report_timestamp": "2026-09-02T12:00:00Z",
            "tools": [
                {
                    "tool_name": "Codex CLI",
                    "installed_version": "1.2.3",
                    "latest_version": "1.2.4",
                    "status": "UPDATE",
                    "health": "HEALTHY",
                    "local_observed_at": None,
                    "remote_observed_at": None,
                    "installed_version_provenance": "LOCAL",
                    "latest_version_provenance": "REMOTE",
                    "release_or_docs_url": "https://example.test/release",
                }
            ],
        }

    def test_complete_report_shows_truthful_fields_and_safe_link(self):
        rendered = render_status_html(self.report())
        for value in ("Codex CLI", "1.2.3", "1.2.4", "UPDATE", "HEALTHY", "LOCAL", "REMOTE", "2026-09-02T12:00:00Z"):
            self.assertIn(value, rendered)
        self.assertIn('href="https://example.test/release"', rendered)
        self.assertIn("Local observed</span><code>unknown", rendered)
        self.assertIn("Remote observed</span><code>unknown", rendered)

    def test_missing_values_are_unknown_and_unsafe_urls_are_not_links(self):
        for unsafe_url in ("javascript:alert(1)", "data:text/html,<script>alert(1)</script>"):
            report = {"tools": [{"tool_name": "Tool", "status": "UNKNOWN", "release_or_docs_url": unsafe_url}]}
            rendered = render_status_html(report)
            self.assertIn("unknown", rendered)
            self.assertIn(html.escape(unsafe_url, quote=True), rendered)
            self.assertNotIn("href=\"javascript:", rendered)
            self.assertNotIn("href=\"data:", rendered)
            self.assertIn("not opened", rendered)

    def test_hostile_values_are_escaped_and_diagnostics_not_leaked(self):
        report = self.report()
        report["tools"][0].update({
            "tool_name": '<script>alert(1)</script>',
            "installed_version": '"><img src=x onerror=alert(1)>',
            "status": '<script>BAD</script>',
            "attention_notes": ["SECRET_SENTINEL"],
            "raw_provider_payload": {"token": "RAW_SECRET"},
        })
        rendered = render_status_html(report)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertIn("&quot;&gt;&lt;img src=x onerror=alert(1)&gt;", rendered)
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertNotIn("SECRET_SENTINEL", rendered)
        self.assertNotIn("RAW_SECRET", rendered)

    def test_rendering_is_deterministic_and_does_not_mutate_or_execute(self):
        report = self.report()
        original = copy.deepcopy(report)
        with (
            patch("subprocess.run", side_effect=AssertionError("execution")) as run,
            patch("subprocess.Popen", side_effect=AssertionError("execution")) as popen,
            patch("socket.socket", side_effect=AssertionError("network")) as network,
            patch.object(Path, "write_text", side_effect=AssertionError("write")) as write_text,
            patch.object(Path, "write_bytes", side_effect=AssertionError("write")) as write_bytes,
        ):
            first = render_status_html(report)
            second = render_status_html(report)
        self.assertEqual(first, second)
        self.assertEqual(report, original)
        run.assert_not_called(); popen.assert_not_called(); network.assert_not_called()
        write_text.assert_not_called(); write_bytes.assert_not_called()

    def test_snapshot_object_is_accepted_without_reinterpreting_timestamp(self):
        snapshot = build_status_snapshot({
            "timestamp": "2026-09-02T12:00:00Z",
            "tools": [{"name": "Git", "installed_version": "2.0", "latest_version": "2.1", "status": "CURRENT", "health": "HEALTHY"}],
        })
        rendered = render_status_html(snapshot)
        self.assertIn("2026-09-02T12:00:00Z", rendered)
        self.assertIn("Local observed</span><code>unknown", rendered)
        self.assertNotIn("None", rendered)


if __name__ == "__main__":
    unittest.main()
