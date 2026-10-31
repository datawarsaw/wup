#!/usr/bin/env python3
"""CLI regression tests for the toolchain audit entrypoints.

Two confirmed regressions are pinned here:

1. An explicitly supplied ``--config`` path that does not exist was silently
   ignored, so the audit ran with unintended default settings.
2. ``python -m scripts.check_toolchain`` failed with
   ``ModuleNotFoundError: No module named 'wup_config'`` because the sibling
   configuration module was only reachable from the script directory.

The subprocess entrypoint tests neutralise ``PATH`` so the local tool probes
resolve to nothing and the read-only audit completes quickly and identically
on every workstation.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_toolchain


class ExplicitConfigurationTests(unittest.TestCase):
    """An explicit --config path must be honoured or rejected, never ignored."""

    def _run_main(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", argv), redirect_stdout(stdout), redirect_stderr(stderr):
            status = check_toolchain.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_missing_explicit_config_fails_before_any_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.toml"
            argv = ["check_toolchain.py", "--config", str(missing), "--offline"]
            with patch.object(
                check_toolchain.ToolchainAuditor,
                "audit_all",
                side_effect=AssertionError("audit must not run for a missing configuration"),
            ):
                status, stdout, stderr = self._run_main(argv)
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("configuration file not found", stderr)
        self.assertIn(str(missing), stderr)

    def test_explicit_config_that_is_not_a_file_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            argv = ["check_toolchain.py", "--config", str(directory_path), "--offline"]
            with patch.object(
                check_toolchain.ToolchainAuditor,
                "audit_all",
                side_effect=AssertionError("audit must not run for an unreadable configuration"),
            ):
                status, stdout, stderr = self._run_main(argv)
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("configuration path is not a file", stderr)

    def test_unreadable_explicit_config_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "broken.toml"
            broken.write_text("[tools\nthis is not a valid configuration\n", encoding="utf-8")
            argv = ["check_toolchain.py", "--config", str(broken), "--offline"]
            with patch.object(
                check_toolchain.ToolchainAuditor,
                "audit_all",
                side_effect=AssertionError("audit must not run for an unreadable configuration"),
            ):
                status, stdout, stderr = self._run_main(argv)
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Error loading WUP configuration", stderr)
        self.assertIn("unsupported WUP TOML syntax", stderr)

    def test_permission_denied_explicit_config_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "wup.toml"
            config.write_text('[tools]\nenabled = []\n', encoding="utf-8")
            argv = ["check_toolchain.py", "--config", str(config), "--offline"]
            with patch.object(
                Path,
                "read_text",
                side_effect=PermissionError(13, "Permission denied"),
            ), patch.object(
                check_toolchain.ToolchainAuditor,
                "audit_all",
                side_effect=AssertionError("audit must not run for an unreadable configuration"),
            ):
                status, stdout, stderr = self._run_main(argv)
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Error loading WUP configuration", stderr)
        self.assertIn("Permission denied", stderr)

    def test_absent_default_configuration_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            absent_default = Path(directory) / "absent.toml"
            argv = ["check_toolchain.py", "--offline"]
            with patch.dict(os.environ, {"WUP_CONFIG": str(absent_default)}), patch.object(
                check_toolchain.ToolchainAuditor,
                "audit_all",
                return_value=check_toolchain.ToolchainAuditReport(
                    timestamp="2026-01-01T00:00:00+00:00",
                    date="2026-01-01",
                    core_stack_health="HEALTHY",
                ),
            ), patch.object(check_toolchain, "format_text_report", return_value="report"):
                status, stdout, stderr = self._run_main(argv)
        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "report\n")


class EntrypointTests(unittest.TestCase):
    """Direct script execution and module execution must both work."""

    def _run_cli(self, arguments):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ)
            # Neutralise the local probes: the audit is read-only and must not
            # depend on which tools happen to be installed on the workstation.
            environment["PATH"] = directory
            environment["WUP_CONFIG"] = str(Path(directory) / "absent.toml")
            return subprocess.run(
                [sys.executable, *arguments],
                cwd=str(ROOT),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
            )

    def test_direct_script_entrypoint_runs(self):
        result = self._run_cli([str(SCRIPTS / "check_toolchain.py"), "--offline"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WUP Toolchain Audit", result.stdout)
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_module_entrypoint_runs(self):
        result = self._run_cli(["-m", "scripts.check_toolchain", "--offline"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WUP Toolchain Audit", result.stdout)
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_module_entrypoint_reports_missing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.toml"
            result = self._run_cli(["-m", "scripts.check_toolchain", "--config", str(missing)])
        self.assertEqual(result.returncode, 1)
        self.assertIn("configuration file not found", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
