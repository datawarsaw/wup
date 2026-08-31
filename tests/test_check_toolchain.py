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
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from check_toolchain import (
    LOCAL_PROBE_HANDLERS,
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
    run_local_probe,
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
        executable = Path(args[0]).stem.lower()
        if executable == "python" and args[1:] == ["-V"]:
            return defaults["python -V"]
        for k, v in defaults.items():
            if k.split()[0] == args[0]:
                return v
        return 127, "", "Executable not found"

    return runner


def setUpModule():
    """Prevent every deterministic test from making even a loopback HTTP call."""
    global _urlopen_patcher
    _urlopen_patcher = patch("check_toolchain.urllib.request.urlopen", side_effect=OSError("network disabled in tests"))
    _urlopen_patcher.start()


def tearDownModule():
    _urlopen_patcher.stop()


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


class TestLocalProbeRegistry(unittest.TestCase):
    def test_static_registry_uses_existing_independent_probe_implementations(self):
        self.assertEqual(
            LOCAL_PROBE_HANDLERS,
            {
                "system_node": ToolchainAuditor.check_system_node,
                "system_npm": ToolchainAuditor.check_system_npm,
                "system_python": ToolchainAuditor.check_system_python,
                "git": ToolchainAuditor.check_git,
                "lm_studio": ToolchainAuditor.check_lm_studio,
                "wrangler": ToolchainAuditor.check_wrangler,
            },
        )

    def test_registry_dispatch_preserves_installed_version_probe(self):
        auditor = ToolchainAuditor(
            fetcher=NetworkFetcher(offline=True),
            command_runner=make_mock_runner({"node -v": (0, "v24.20.0", "")}),
        )

        result = run_local_probe(auditor, "system_node")

        self.assertEqual(result.name, "Node.js")
        self.assertEqual(result.installed_version, "24.20.0")

    def test_audit_uses_registry_for_migrated_installed_version_probe(self):
        auditor = ToolchainAuditor(fetcher=NetworkFetcher(offline=True), command_runner=make_mock_runner())

        def fixed_node_probe(_auditor: ToolchainAuditor) -> ToolCheckResult:
            return ToolCheckResult(
                name="Node.js",
                installed_version="24.20.0",
                install_method="registry test",
                latest_version="24.20.0",
                latest_source="registry test",
                status="CURRENT",
                health="HEALTHY",
            )

        with patch.dict(LOCAL_PROBE_HANDLERS, {"system_node": fixed_node_probe}):
            report = auditor.audit_all()

        node = next(result for result in report.tools if result.name == "Node.js")
        self.assertEqual(node.installed_version, "24.20.0")
        self.assertEqual(node.install_method, "registry test")

    def test_unknown_registry_probe_is_rejected_deterministically(self):
        auditor = ToolchainAuditor(fetcher=NetworkFetcher(offline=True), command_runner=make_mock_runner())

        with self.assertRaisesRegex(ValueError, "^unsupported local probe: missing$"):
            run_local_probe(auditor, "missing")


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
            proxy_state="HEALTHY",
            shim_aligned=True,
            detected_codex="0.149.0",
        )
        with patch("check_toolchain.safe_read_json", return_value=None):
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
            proxy_state="HEALTHY",
            shim_aligned=False,
            shim_status="wrapper present but not an opencodex shim",
            detected_codex="0.149.0",
        )
        with patch("check_toolchain.safe_read_json", return_value=None):
            res_held = auditor.check_codex_cli(ocx_diag=bypassed_ocx)
        self.assertEqual(res_held.status, "WATCH")
        self.assertTrue(any("OpenCodex coupling risk" in n for n in res_held.attention_notes))

        # Case B: OpenCodex proxy down -> held as WATCH
        dead_proxy_ocx = OpenCodexDiagnostics(
            installed_version="2.31.0",
            health="DEGRADED",
            proxy_running=False,
            proxy_state="UNHEALTHY",
            shim_aligned=True,
            detected_codex="0.149.0",
        )
        with patch("check_toolchain.safe_read_json", return_value=None):
            res_dead = auditor.check_codex_cli(ocx_diag=dead_proxy_ocx)
        self.assertEqual(res_dead.status, "WATCH")
        self.assertTrue(any("OpenCodex coupling risk" in n for n in res_dead.attention_notes))

        # Case C: Fully aligned and healthy -> UPDATE
        safe_ocx = OpenCodexDiagnostics(
            installed_version="2.31.0",
            health="HEALTHY",
            proxy_running=True,
            proxy_state="HEALTHY",
            shim_aligned=True,
            detected_codex="0.149.0",
        )
        with patch("check_toolchain.safe_read_json", return_value=None):
            res_safe = auditor.check_codex_cli(ocx_diag=safe_ocx)
        self.assertEqual(res_safe.status, "UPDATE")

    def test_active_codex_executable_is_authoritative_over_package_metadata(self):
        calls: List[str] = []
        base_runner = make_mock_runner()

        def recording_runner(args: List[str], timeout: float = 4.0) -> Tuple[int, str, str]:
            calls.append(" ".join(args))
            return base_runner(args, timeout)

        fetcher = NetworkFetcher(
            mock_data={"https://registry.npmjs.org/@openai/codex/latest": {"version": "0.149.1"}}
        )
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=recording_runner)
        healthy_ocx = OpenCodexDiagnostics(
            health="HEALTHY",
            proxy_running=True,
            proxy_state="HEALTHY",
            shim_aligned=True,
            detected_codex="0.149.0",
        )

        with patch("check_toolchain.shutil.which", return_value=r"C:\Users\test\AppData\Roaming\npm\codex.cmd"), patch(
            "check_toolchain.safe_read_json",
            side_effect=[{"version": "0.148.0"}, None],
        ):
            result = auditor.check_codex_cli(ocx_diag=healthy_ocx)

        self.assertIn("codex --version", calls)
        self.assertEqual(result.installed_version, "0.149.0")
        self.assertEqual(result.status, "WATCH")
        self.assertEqual(result.health, "DEGRADED")
        self.assertIn("npm (@openai/codex", result.install_method)
        self.assertTrue(any("executable/package mismatch" in note for note in result.attention_notes))

    def test_package_metadata_cannot_replace_failed_active_codex_version(self):
        failed_cli = make_mock_runner({"codex --version": (1, "", "access restricted")})
        auditor = ToolchainAuditor(fetcher=NetworkFetcher(offline=True), command_runner=failed_cli)
        healthy_ocx = OpenCodexDiagnostics(
            health="HEALTHY",
            proxy_running=True,
            proxy_state="HEALTHY",
            shim_aligned=True,
            detected_codex="0.149.0",
        )

        with patch("check_toolchain.shutil.which", return_value=r"C:\Users\test\AppData\Roaming\npm\codex.cmd"), patch(
            "check_toolchain.safe_read_json", return_value={"version": "0.149.0"}
        ):
            result = auditor.check_codex_cli(ocx_diag=healthy_ocx)

        self.assertIsNone(result.installed_version)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.health, "UNVERIFIED")
        self.assertTrue(any("not executable truth" in note for note in result.attention_notes))

    def test_opencodex_detected_codex_mismatch_holds_update(self):
        fetcher = NetworkFetcher(
            mock_data={"https://registry.npmjs.org/@openai/codex/latest": {"version": "0.150.0"}}
        )
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())
        mismatched_ocx = OpenCodexDiagnostics(
            health="HEALTHY",
            proxy_running=True,
            proxy_state="HEALTHY",
            shim_aligned=True,
            detected_codex="0.148.0",
        )

        result = auditor.check_codex_cli(ocx_diag=mismatched_ocx)

        self.assertEqual(result.status, "WATCH")
        self.assertEqual(result.health, "DEGRADED")
        self.assertTrue(any("Codex/OpenCodex version mismatch" in note for note in result.attention_notes))
        self.assertIn("versions disagree", result.update_recommendation.breaking_relevance)

    def test_workstation_ops_path_is_optional_and_uses_configured_repository(self):
        auditor = ToolchainAuditor(fetcher=NetworkFetcher(offline=True), command_runner=make_mock_runner())
        with patch.dict(os.environ, {"WUP_WORKSTATION_OPS_PATH": ""}):
            optional = auditor.check_workstation_ops()
        self.assertEqual(optional.status, "UNKNOWN")
        self.assertEqual(optional.health, "UNVERIFIED")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text('{"version":"1.2.0"}', encoding="utf-8")
            (root / "node_modules").mkdir()
            with patch.dict(os.environ, {"WUP_WORKSTATION_OPS_PATH": str(root)}):
                configured = auditor.check_workstation_ops()
        self.assertEqual(configured.installed_version, "1.2.0")
        self.assertEqual(configured.health, "HEALTHY")


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

    def test_python_uses_official_cycle_source(self):
        fetcher = NetworkFetcher(
            mock_data={
                "https://docs.python.org/3.10/": "<h1>Python 3.10.20 documentation</h1>",
            }
        )
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())

        result = auditor.check_system_python()

        self.assertEqual(result.installed_version, "3.10.5")
        self.assertEqual(result.latest_version, "3.10.20")
        self.assertEqual(result.status, "WATCH")
        self.assertIn("docs.python.org/3.10", result.latest_source)


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

    def test_missing_optional_system_bun_is_non_actionable_with_healthy_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp)
            bundled = appdata / "npm" / "node_modules" / "@bitkyc08" / "opencodex" / "node_modules" / "bun" / "bin" / "bun.exe"
            bundled.parent.mkdir(parents=True)
            bundled.touch()

            def runner(args: List[str], timeout: float = 4.0) -> Tuple[int, str, str]:
                if Path(args[0]) == bundled and args[1:] == ["-v"]:
                    return 0, "1.4.0", ""
                return make_mock_runner()(args, timeout)

            fetcher = NetworkFetcher(
                mock_data={"https://api.github.com/repos/oven-sh/bun/releases/latest": {"tag_name": "bun-v1.4.0"}}
            )
            auditor = ToolchainAuditor(fetcher=fetcher, appdata_roaming=appdata, command_runner=runner)
            with patch("check_toolchain.shutil.which", return_value=None):
                bun_sys, bun_bundled = auditor.check_bun()

        self.assertEqual(bun_sys.status, "UNKNOWN")
        self.assertEqual(bun_sys.health, "NOT_INSTALLED")
        self.assertEqual(bun_sys.attention_notes, [])
        self.assertIn("optional; bundled Bun in use", bun_sys.install_method)
        self.assertIsNotNone(bun_bundled)
        self.assertEqual(bun_bundled.status, "CURRENT")
        self.assertEqual(bun_bundled.health, "HEALTHY")


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

    def test_ocx_health_report_does_not_fake_direct_healthz_verification(self):
        fetcher = NetworkFetcher(
            mock_data={"https://registry.npmjs.org/@bitkyc08/opencodex/latest": {"version": "2.31.0"}}
        )
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())

        result, diag = auditor.check_opencodex()

        self.assertEqual(diag.direct_healthz, "UNVERIFIED")
        self.assertEqual(diag.proxy_state, "REPORTED_HEALTHY")
        self.assertIn("ocx health reports healthy", diag.proxy_summary)
        self.assertNotIn("healthz verified", diag.proxy_summary)
        self.assertEqual(result.health, "UNVERIFIED")
        self.assertTrue(auditor.observer_degraded)

    def test_direct_healthz_success_is_explicitly_verified(self):
        fetcher = NetworkFetcher(
            mock_data={"https://registry.npmjs.org/@bitkyc08/opencodex/latest": {"version": "2.31.0"}}
        )
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())

        with patch.object(auditor, "_probe_opencodex_healthz", return_value=True):
            result, diag = auditor.check_opencodex()

        self.assertEqual(diag.direct_healthz, "VERIFIED")
        self.assertEqual(diag.proxy_state, "HEALTHY")
        self.assertIn("direct healthz verified", diag.proxy_summary)
        self.assertEqual(result.health, "HEALTHY")

    def test_restricted_opencodex_diagnostics_are_unverified_not_host_failure(self):
        blocked = make_mock_runner({
            "ocx health": (1, "", "EPERM: permission denied"),
            "ocx status": (1, "", "EPERM: permission denied"),
        })
        fetcher = NetworkFetcher(
            mock_data={"https://registry.npmjs.org/@bitkyc08/opencodex/latest": {"version": "2.31.0"}}
        )
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=blocked)

        result, diag = auditor.check_opencodex()

        self.assertEqual(result.health, "UNVERIFIED")
        self.assertEqual(diag.proxy_state, "UNVERIFIED")
        self.assertNotIn("unreachable", diag.proxy_summary)
        self.assertTrue(auditor.observer_degraded)

    def test_restricted_opencodex_version_observation_is_not_not_installed(self):
        blocked = make_mock_runner({
            "ocx --version": (1, "", "access restricted"),
            "ocx health": (1, "", "access restricted"),
            "ocx status": (1, "", "access restricted"),
        })
        auditor = ToolchainAuditor(fetcher=NetworkFetcher(offline=True), command_runner=blocked)

        with patch("check_toolchain.safe_read_json", return_value=None):
            result, diag = auditor.check_opencodex()

        self.assertEqual(result.health, "UNVERIFIED")
        self.assertEqual(diag.health, "UNVERIFIED")
        self.assertNotEqual(result.health, "NOT_INSTALLED")
        self.assertTrue(auditor.observer_degraded)

    def test_malformed_opencodex_status_keeps_observer_evidence_unverified(self):
        malformed = make_mock_runner({"ocx status": (0, "unexpected status format", "")})
        fetcher = NetworkFetcher(
            mock_data={"https://registry.npmjs.org/@bitkyc08/opencodex/latest": {"version": "2.31.0"}}
        )
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=malformed)

        with patch.object(auditor, "_probe_opencodex_healthz", return_value=True):
            result, diag = auditor.check_opencodex()

        self.assertEqual(diag.proxy_state, "HEALTHY")
        self.assertEqual(result.health, "UNVERIFIED")
        self.assertTrue(any("complete Codex-version" in note for note in result.attention_notes))
        self.assertTrue(auditor.observer_degraded)

    def test_unresolved_findings_never_claim_stable_toolchain(self):
        fetcher = NetworkFetcher(offline=True)
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())

        report = auditor.audit_all()

        self.assertIn("No immediate update recommended; unresolved toolchain findings remain.", report.recommended_actions)
        self.assertFalse(any("Workstation toolchain is stable" in action for action in report.recommended_actions))


class TestNoHardcodedDate(unittest.TestCase):
    def test_no_hardcoded_date(self):
        fetcher = NetworkFetcher(offline=True)
        auditor = ToolchainAuditor(fetcher=fetcher, command_runner=make_mock_runner())
        report = auditor.audit_all()

        expected_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual(report.date, expected_date)
        self.assertTrue(report.timestamp.startswith(expected_date))


class TestReleaseLinks(unittest.TestCase):
    def test_actionable_supported_tools_receive_authoritative_links(self):
        auditor = ToolchainAuditor(fetcher=NetworkFetcher(offline=True), command_runner=make_mock_runner())
        cases = [
            ("Codex CLI", "2.0.0", "test", "https://www.npmjs.com/package/@openai/codex/v/2.0.0"),
            ("OpenCodex", "2.0.0", "test", "https://www.npmjs.com/package/@bitkyc08/opencodex/v/2.0.0"),
            ("Node.js", "22.1.0", "nodejs.org release index", "https://nodejs.org/en/blog/release/v22.1.0"),
            ("npm", "12.0.2", "npm registry (npm)", "https://github.com/npm/cli/releases/tag/v12.0.2"),
            ("Python", "3.13.1", "test", "https://www.python.org/downloads/release/python-3131/"),
            ("Git", "2.52.0", "test", "https://github.com/git-for-windows/git/releases"),
            ("Wrangler", "4.0.0", "test", "https://www.npmjs.com/package/wrangler/v/4.0.0"),
        ]
        for name, version, latest_source, expected in cases:
            result = ToolCheckResult(name=name, installed_version="1.0.0", install_method="test", latest_version=version, latest_source=latest_source, status="UPDATE", health="HEALTHY")
            self.assertEqual(auditor.release_url_for(result), expected)

    def test_release_link_fallbacks_are_authoritative(self):
        auditor = ToolchainAuditor(fetcher=NetworkFetcher(offline=True), command_runner=make_mock_runner())
        node = ToolCheckResult(name="Node.js", installed_version="22.0.0", install_method="test", latest_version="22.1.0", latest_source="test fallback", status="WATCH", health="HEALTHY")
        npm = ToolCheckResult(name="npm", installed_version="11.0.0", install_method="test", latest_version="12.0.2", latest_source="test fallback", status="WATCH", health="HEALTHY")
        self.assertEqual(auditor.release_url_for(node), "https://nodejs.org/dist/v22.1.0/")
        self.assertEqual(auditor.release_url_for(npm), "https://www.npmjs.com/package/npm")

    def test_unsupported_or_non_actionable_links_are_omitted(self):
        auditor = ToolchainAuditor(fetcher=NetworkFetcher(offline=True), command_runner=make_mock_runner())
        result = ToolCheckResult(name="LM Studio", installed_version="1.0.0", install_method="test", latest_version="2.0.0", latest_source="test", status="UPDATE", health="HEALTHY")
        self.assertIsNone(auditor.release_url_for(result))
        result.status = "CURRENT"
        self.assertIsNone(auditor.release_url_for(result))


if __name__ == "__main__":
    unittest.main()
