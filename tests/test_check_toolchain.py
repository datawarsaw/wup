#!/usr/bin/env python3
"""
test_check_toolchain.py - Deterministic unit tests for Daily Toolchain Watch V1.

Verifies semantic version comparison, production ToolchainAuditor checks,
offline/unknown handling, bundled vs system runtime distinctions, secret redaction,
Codex/OpenCodex coupling, and observer resilience without relying on live network access.
"""

from __future__ import annotations

import datetime
import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from check_toolchain import (
    NetworkFetcher,
    OpenCodexDiagnostics,
    SemVer,
    ToolchainAuditReport,
    ToolchainAuditor,
    ToolCheckResult,
    UpdateRecommendation,
    format_json_report,
    format_text_report,
    redact_secrets,
)


def make_mock_runner(overrides: Optional[Dict[str, Tuple[int, str, str]]] = None):
    defaults: Dict[str, Tuple[int, str, str]] = {
        "ocx --version": (0, "opencodex 2.31.0", ""),
        "ocx health": (0, "Proxy healthy (PID null, port 10100)", ""),
        "ocx status": (
            0,
            "✅ Proxy: running (PID 12345)\n"
            "   Health: http://127.0.0.1:10100/healthz ok (live)\n"
            "   Codex version: 0.149.0\n"
            "   Codex autostart shim: active opencodex shim\n"
            "   Runtime source: bundled\n"
            "   Catalog clamp: inactive\n"
            "   ✅ Codex bundled plugins: ok\n"
            "   OAuth logins:\n"
            "     openai ✗ not logged in",
            "",
        ),
        "codex --version": (0, "codex-cli 0.149.0", ""),
        "codex doctor --json": (
            0,
            json.dumps({
                "schemaVersion": 1,
                "overallStatus": "ok",
                "checks": {
                    "runtime.install_method": {"status": "ok"},
                    "install.consistent": {"status": "ok"},
                },
            }),
            "",
        ),
        "node -v": (0, "v24.19.0", ""),
        "npm -v": (0, "12.0.2", ""),
        "bun -v": (127, "", "Executable not found"),
        "python -V": (0, "Python 3.10.5", ""),
        "git --version": (0, "git version 2.45.1.windows.1", ""),
        "lms version": (0, "CLI commit: 71bd99c", ""),
        "wrangler --version": (0, "4.125.0", ""),
    }
    if overrides:
        defaults.update(overrides)

    def runner(args: List[str], timeout: float = 4.0) -> Tuple[int, str, str]:
        key = " ".join(args)
        if key in defaults:
            return defaults[key]
        for k, v in defaults.items():
            if k.split()[0] == args[0]:
                return v
        return 127, "", "Executable not found"

    return runner


class TestSemVerCompare(unittest.TestCase):
    def test_semantic_compare(self):
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

    def test_semver_with_prefixes_and_builds(self):
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


class TestProductionClassification(unittest.TestCase):
    def test_current_classification_production(self):
        mock_data = {
            "https://registry.npmjs.org/@openai/codex/latest": {"version": "0.149.0"},
        }
        fetcher = NetworkFetcher(offline=False, mock_data=mock_data)
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())

        healthy_ocx = OpenCodexDiagnostics(
            installed_version="2.31.0",
            health="HEALTHY",
            proxy_running=True,
            shim_aligned=True,
        )
        res = auditor.check_codex_cli(ocx_diag=healthy_ocx)
        self.assertEqual(res.status, "CURRENT")
        self.assertIsNone(res.update_recommendation)

    def test_update_classification_production(self):
        mock_data = {
            "https://registry.npmjs.org/@bitkyc08/opencodex/latest": {"version": "99.0.0"},
        }
        fetcher = NetworkFetcher(offline=False, mock_data=mock_data)
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())

        res, diag = auditor.check_opencodex()
        self.assertEqual(res.status, "UPDATE")
        self.assertIsNotNone(res.update_recommendation)
        self.assertIn("@bitkyc08/opencodex@99.0.0", res.update_recommendation.proposed_command)
        self.assertIn("ocx health", res.update_recommendation.validation_checks)
        self.assertIn("ocx status", res.update_recommendation.validation_checks[2])

    def test_codex_update_held_by_opencodex_coupling(self):
        mock_data = {
            "https://registry.npmjs.org/@openai/codex/latest": {"version": "99.0.0"},
        }
        fetcher = NetworkFetcher(offline=False, mock_data=mock_data)
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())

        # Case A: OpenCodex shim bypassed -> held as WATCH
        bypassed_ocx = OpenCodexDiagnostics(
            installed_version="2.31.0",
            health="HEALTHY",
            proxy_running=True,
            shim_aligned=False,
            shim_status="wrapper present but not an opencodex shim",
        )
        res_held = auditor.check_codex_cli(ocx_diag=bypassed_ocx)
        self.assertEqual(res_held.status, "WATCH")
        self.assertTrue(any("OpenCodex coupling risk" in n for n in res_held.attention_notes))

        # Case B: OpenCodex proxy down -> held as WATCH
        dead_proxy_ocx = OpenCodexDiagnostics(
            installed_version="2.31.0",
            health="DEGRADED",
            proxy_running=False,
            shim_aligned=True,
        )
        res_dead = auditor.check_codex_cli(ocx_diag=dead_proxy_ocx)
        self.assertEqual(res_dead.status, "WATCH")
        self.assertTrue(any("OpenCodex coupling risk" in n for n in res_dead.attention_notes))

        # Case C: Fully aligned and healthy -> UPDATE
        safe_ocx = OpenCodexDiagnostics(
            installed_version="2.31.0",
            health="HEALTHY",
            proxy_running=True,
            shim_aligned=True,
        )
        res_safe = auditor.check_codex_cli(ocx_diag=safe_ocx)
        self.assertEqual(res_safe.status, "UPDATE")


class TestOfflineUnknownHandling(unittest.TestCase):
    def test_network_unavailable_yields_unknown_on_actual_checker(self):
        fetcher = NetworkFetcher(offline=True)
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())

        codex_res = auditor.check_codex_cli()
        self.assertEqual(codex_res.status, "UNKNOWN")
        self.assertEqual(codex_res.latest_version, "unknown")

        ocx_res, _ = auditor.check_opencodex()
        self.assertEqual(ocx_res.status, "UNKNOWN")
        self.assertEqual(ocx_res.latest_version, "unknown")

        node_res = auditor.check_system_node()
        self.assertEqual(node_res.status, "UNKNOWN")
        self.assertEqual(node_res.latest_version, "unknown")

        wrangler_res = auditor.check_wrangler()
        self.assertEqual(wrangler_res.status, "UNKNOWN")
        self.assertEqual(wrangler_res.latest_version, "unknown")

        py_res = auditor.check_system_python()
        self.assertEqual(py_res.status, "UNKNOWN")
        self.assertEqual(py_res.latest_version, "unknown")


class TestRuntimeDistinction(unittest.TestCase):
    def test_bundled_vs_system_runtimes(self):
        fetcher = NetworkFetcher(offline=True)
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())

        bun_sys, bun_bundled = auditor.check_bun()
        self.assertEqual(bun_sys.name, "Bun (System)")
        self.assertFalse(bun_sys.is_bundled)
        self.assertEqual(bun_sys.runtime_type, "system")

        if bun_bundled:
            self.assertEqual(bun_bundled.name, "Bun (OpenCodex bundled)")
            self.assertTrue(bun_bundled.is_bundled)
            self.assertEqual(bun_bundled.runtime_type, "bundled")


class TestMalformedOutputResilience(unittest.TestCase):
    def test_malformed_command_output(self):
        self.assertIsNone(SemVer.parse("corrupted random text without digits"))
        self.assertIsNone(SemVer.parse("!@#$%^&*()"))

        # Test malformed doctor output sets health to UNVERIFIED, not HEALTHY
        mock_broken_doc = make_mock_runner({
            "codex doctor --json": (0, "{invalid json content", ""),
        })
        fetcher = NetworkFetcher(offline=True)
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=mock_broken_doc)
        codex_res = auditor.check_codex_cli()
        self.assertEqual(codex_res.health, "UNVERIFIED")
        self.assertTrue(auditor.observer_degraded)


class TestSecretSafety(unittest.TestCase):
    def test_secret_safe_opencodex_warning_and_output(self):
        sample = (
            "User token: sk-proj-1234567890abcdef1234567890\n"
            "Auth header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0\n"
            "Session: ocx_session_abc123xyz456\n"
            "EPERM: operation not permitted, open 'C:\\Users\\micha\\.opencodex\\auth.json'\n"
            "Normal text: Codex CLI 0.149.0 is operational."
        )
        redacted = redact_secrets(sample)
        self.assertNotIn("sk-proj-1234567890abcdef1234567890", redacted)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", redacted)
        self.assertNotIn("ocx_session_abc123xyz456", redacted)
        self.assertIn("[REDACTED]", redacted)
        self.assertIn("Codex CLI 0.149.0 is operational", redacted)

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
        fetcher = NetworkFetcher(offline=True)
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())
        auditor.observer_degraded = True

        report = auditor.audit_all()
        self.assertEqual(report.observer_path_status, "DEGRADED")
        self.assertIn(report.core_stack_health, ("HEALTHY", "DEGRADED", "UNVERIFIED"))


class TestNoHardcodedDate(unittest.TestCase):
    def test_no_hardcoded_date(self):
        fetcher = NetworkFetcher(offline=True)
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())
        report = auditor.audit_all()

        expected_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual(report.date, expected_date)
        self.assertTrue(report.timestamp.startswith(expected_date))


if __name__ == "__main__":
    unittest.main()

