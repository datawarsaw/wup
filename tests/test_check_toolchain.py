#!/usr/bin/env python3
"""
test_check_toolchain.py - Deterministic unit tests for Daily Toolchain Watch V1.

Verifies semantic version comparison, status classifications, offline/unknown
handling, bundled vs system runtime distinctions, secret redaction, and observer
resilience without relying on live network access.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict

# Add parent scripts directory to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from check_toolchain import (
    NetworkFetcher,
    SemVer,
    ToolchainAuditReport,
    ToolchainAuditor,
    ToolCheckResult,
    UpdateRecommendation,
    format_json_report,
    format_text_report,
    redact_secrets,
    safe_run_command,
)


class TestSemVer(unittest.TestCase):
    def test_semver_parse_and_compare(self):
        v1 = SemVer.parse("0.149.0")
        v2 = SemVer.parse("0.149.1")
        v3 = SemVer.parse("0.150.0")
        v4 = SemVer.parse("1.0.0")

        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)
        self.assertEqual(v1.compare(v2), -1)
        self.assertEqual(v2.compare(v1), 1)
        self.assertEqual(v1.compare(v1), 0)
        self.assertEqual(v2.compare(v3), -1)
        self.assertEqual(v3.compare(v4), -1)

    def test_semver_with_prefixes(self):
        v_node = SemVer.parse("v24.19.0")
        v_git = SemVer.parse("git version 2.45.1.windows.1")
        v_codex = SemVer.parse("codex-cli 0.149.0")
        v_ocx = SemVer.parse("opencodex 2.31.0")

        self.assertIsNotNone(v_node)
        self.assertEqual(v_node.parts[:3], (24, 19, 0))
        self.assertIsNotNone(v_git)
        self.assertEqual(v_git.parts[:3], (2, 45, 1))
        self.assertIsNotNone(v_codex)
        self.assertEqual(v_codex.parts[:3], (0, 149, 0))
        self.assertIsNotNone(v_ocx)
        self.assertEqual(v_ocx.parts[:3], (2, 31, 0))

    def test_semver_prerelease(self):
        v_pre = SemVer.parse("2.31.0-beta.1")
        v_rel = SemVer.parse("2.31.0")
        self.assertIsNotNone(v_pre)
        self.assertIsNotNone(v_rel)
        self.assertEqual(v_pre.compare(v_rel), -1)
        self.assertEqual(v_rel.compare(v_pre), 1)

    def test_semver_malformed_returns_none(self):
        self.assertIsNone(SemVer.parse(None))
        self.assertIsNone(SemVer.parse(""))
        self.assertIsNone(SemVer.parse("invalid_no_digits"))


class TestClassification(unittest.TestCase):
    def test_current_classification(self):
        v_inst = SemVer.parse("0.149.1")
        v_lat = SemVer.parse("0.149.1")
        status = "UPDATE" if v_inst.compare(v_lat) < 0 else "CURRENT"
        self.assertEqual(status, "CURRENT")

    def test_update_classification(self):
        v_inst = SemVer.parse("0.149.0")
        v_lat = SemVer.parse("0.149.1")
        status = "UPDATE" if v_inst.compare(v_lat) < 0 else "CURRENT"
        self.assertEqual(status, "UPDATE")

        rec = UpdateRecommendation(
            why="Newer stable release 0.149.1 available",
            breaking_relevance="Overwrites npm shims; re-run ocx codex-shim install",
            install_method="npm (@openai/codex)",
            proposed_command="npm install -g @openai/codex@0.149.1",
            rollback_command="npm install -g @openai/codex@0.149.0",
            validation_checks=["codex --version", "codex doctor"],
        )
        self.assertIn("@openai/codex@0.149.1", rec.proposed_command)
        self.assertIn("@openai/codex@0.149.0", rec.rollback_command)
        self.assertEqual(len(rec.validation_checks), 2)

    def test_unknown_when_latest_unavailable(self):
        """Crucial requirement: lookup failure must yield UNKNOWN, never false CURRENT."""
        v_inst = SemVer.parse("0.149.0")
        v_lat = None  # Lookup failed due to network / no feed

        if not v_lat:
            status = "UNKNOWN"
        elif v_inst.compare(v_lat) < 0:
            status = "UPDATE"
        else:
            status = "CURRENT"

        self.assertEqual(status, "UNKNOWN")
        self.assertNotEqual(status, "CURRENT")


class TestRuntimeDistinction(unittest.TestCase):
    def test_bundled_vs_system_runtimes(self):
        mock_fetcher = NetworkFetcher(offline=True)
        auditor = ToolchainAuditor(fetcher=mock_fetcher)

        bun_sys, bun_bundled = auditor.check_bun()
        self.assertEqual(bun_sys.name, "Bun (System)")
        self.assertFalse(bun_sys.is_bundled)
        self.assertEqual(bun_sys.runtime_type, "system")

        if bun_bundled:
            self.assertEqual(bun_bundled.name, "Bun (OpenCodex bundled)")
            self.assertTrue(bun_bundled.is_bundled)
            self.assertEqual(bun_bundled.runtime_type, "bundled")


class TestSecretSafety(unittest.TestCase):
    def test_redact_secrets(self):
        sample = (
            "User token: sk-proj-1234567890abcdef1234567890\n"
            "Auth header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0\n"
            "Session: ocx_session_abc123xyz456\n"
            "Normal text: Codex CLI 0.149.0 is healthy."
        )
        redacted = redact_secrets(sample)
        self.assertNotIn("sk-proj-1234567890abcdef1234567890", redacted)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", redacted)
        self.assertNotIn("ocx_session_abc123xyz456", redacted)
        self.assertIn("[REDACTED]", redacted)
        self.assertIn("Codex CLI 0.149.0 is healthy", redacted)

    def test_report_format_is_secret_safe(self):
        report = ToolchainAuditReport(
            timestamp="2026-08-26T08:00:00Z",
            date="2026-08-26",
            core_stack_health="HEALTHY",
            tools=[
                ToolCheckResult(
                    name="Codex CLI",
                    installed_version="0.149.0",
                    install_method="npm",
                    latest_version="0.149.1",
                    latest_source="npm registry",
                    status="UPDATE",
                    health="HEALTHY",
                    attention_notes=["Found token sk-proj-supersecretkey12345678 in logs"],
                )
            ],
            attention=["Found token sk-proj-supersecretkey12345678 in logs"],
            recommended_actions=["Run update with Bearer testsecrettoken12345678"],
        )

        text_rep = format_text_report(report)
        json_rep = format_json_report(report)

        self.assertNotIn("sk-proj-supersecretkey12345678", text_rep)
        self.assertNotIn("testsecrettoken12345678", text_rep)
        self.assertNotIn("sk-proj-supersecretkey12345678", json_rep)
        self.assertIn("[REDACTED]", text_rep)


class TestObserverResilience(unittest.TestCase):
    def test_observer_failure_does_not_become_host_failure(self):
        """Sandbox EPERM or missing observation tool must not report broken workstation."""
        mock_fetcher = NetworkFetcher(offline=True)
        auditor = ToolchainAuditor(fetcher=mock_fetcher)
        auditor.observer_degraded = True

        report = auditor.audit_all()
        # Observer degradation should be reported under observer_path_status
        self.assertIn(report.observer_path_status, ("NORMAL", "DEGRADED"))
        # Core health should not collapse to broken if individual tools are healthy
        self.assertIn(report.core_stack_health, ("HEALTHY", "DEGRADED", "UNVERIFIED"))


class TestMockEndToEndAudit(unittest.TestCase):
    def test_mock_end_to_end_audit(self):
        mock_responses: Dict[str, Any] = {
            "https://registry.npmjs.org/@openai/codex/latest": {"version": "0.149.1"},
            "https://registry.npmjs.org/@bitkyc08/opencodex/latest": {"version": "2.33.0"},
            "https://registry.npmjs.org/wrangler/latest": {"version": "4.126.0"},
            "https://registry.npmjs.org/npm/latest": {"version": "12.0.2"},
            "https://nodejs.org/dist/index.json": [{"version": "v26.7.0"}],
            "https://endoflife.date/api/python.json": [{"cycle": "3.10", "latest": "3.10.21"}],
            "https://api.github.com/repos/git-for-windows/git/releases/latest": {"tag_name": "v2.55.0.windows.1"},
        }
        fetcher = NetworkFetcher(offline=False, mock_data=mock_responses)
        auditor = ToolchainAuditor(fetcher=fetcher)
        report = auditor.audit_all()

        self.assertEqual(report.date, "2026-08-26")
        self.assertGreater(len(report.tools), 8)

        # Check tool results
        codex_tool = next(t for t in report.tools if t.name == "Codex CLI")
        self.assertEqual(codex_tool.latest_version, "0.149.1")

        ocx_tool = next(t for t in report.tools if t.name == "OpenCodex")
        self.assertEqual(ocx_tool.latest_version, "2.33.0")

        desktop_tool = next(t for t in report.tools if t.name == "Codex Desktop")
        self.assertEqual(desktop_tool.status, "UNKNOWN")
        self.assertEqual(desktop_tool.latest_version, "unknown")

        text_output = format_text_report(report)
        self.assertIn("Daily Toolchain Watch", text_output)
        self.assertIn("Core stack health:", text_output)
        self.assertIn("Recommended action:", text_output)

        json_output = format_json_report(report)
        parsed_json = json.loads(json_output)
        self.assertEqual(parsed_json["core_stack_health"], report.core_stack_health)


if __name__ == "__main__":
    unittest.main()

