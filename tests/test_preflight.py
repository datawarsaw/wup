#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from doctor import DoctorCheck, DoctorReport
from preflight import (
    format_json_report,
    format_text_report,
    main,
    preflight_exit_code,
    project_doctor_report,
)


class TestPreflightProjection(unittest.TestCase):
    def test_healthy_report_is_ready(self) -> None:
        report = project_doctor_report(DoctorReport("HEALTHY", [DoctorCheck("repository", "HEALTHY", "ok")]))
        self.assertTrue(report.ready)
        self.assertEqual("READY", report.status)
        self.assertEqual(0, preflight_exit_code(report))
        self.assertEqual([], report.blockers)
        self.assertEqual([], report.optional_warnings)

    def test_degraded_optional_report_is_ready_with_warning(self) -> None:
        report = project_doctor_report(DoctorReport("DEGRADED", [DoctorCheck("scheduler", "DEGRADED", "optional")]))
        self.assertTrue(report.ready)
        self.assertEqual(0, preflight_exit_code(report))
        self.assertEqual(["scheduler"], [finding.name for finding in report.optional_warnings])

    def test_unhealthy_report_is_not_ready_with_blocker(self) -> None:
        report = project_doctor_report(DoctorReport("UNHEALTHY", [DoctorCheck("configuration", "UNHEALTHY", "invalid")]))
        self.assertFalse(report.ready)
        self.assertEqual("NOT_READY", report.status)
        self.assertEqual(1, preflight_exit_code(report))
        self.assertEqual(["configuration"], [finding.name for finding in report.blockers])

    def test_projection_preserves_doctor_check_order(self) -> None:
        report = project_doctor_report(
            DoctorReport(
                "UNHEALTHY",
                [
                    DoctorCheck("first-warning", "DEGRADED", "one"),
                    DoctorCheck("first-blocker", "UNHEALTHY", "two"),
                    DoctorCheck("second-warning", "DEGRADED", "three"),
                    DoctorCheck("second-blocker", "UNHEALTHY", "four"),
                ],
            )
        )
        self.assertEqual(["first-blocker", "second-blocker"], [finding.name for finding in report.blockers])
        self.assertEqual(["first-warning", "second-warning"], [finding.name for finding in report.optional_warnings])

    def test_json_is_deterministic(self) -> None:
        report = project_doctor_report(DoctorReport("HEALTHY", [DoctorCheck("repository", "HEALTHY", "ok")]))
        self.assertEqual(format_json_report(report), format_json_report(report))
        self.assertEqual("READY", json.loads(format_json_report(report))["status"])

    def test_json_excludes_details_and_secrets(self) -> None:
        secret = "SECRET_PREFLIGHT_SENTINEL"
        report = project_doctor_report(
            DoctorReport("UNHEALTHY", [DoctorCheck("state", "UNHEALTHY", "state invalid", {"token": secret})])
        )
        rendered = format_json_report(report)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("details", rendered)
        self.assertEqual("NOT_READY", json.loads(rendered)["status"])

    def test_text_output_has_human_sections(self) -> None:
        report = project_doctor_report(
            DoctorReport("UNHEALTHY", [DoctorCheck("repository", "UNHEALTHY", "missing"), DoctorCheck("scheduler", "DEGRADED", "optional")])
        )
        rendered = format_text_report(report)
        self.assertIn("WUP Setup Preflight", rendered)
        self.assertIn("Blockers:", rendered)
        self.assertIn("Optional warnings:", rendered)


class TestPreflightCli(unittest.TestCase):
    def test_main_evaluates_doctor_once(self) -> None:
        doctor_report = DoctorReport("DEGRADED", [DoctorCheck("scheduler", "DEGRADED", "optional")])
        output = io.StringIO()
        with patch("preflight.doctor.diagnose", return_value=doctor_report) as diagnose, redirect_stdout(output):
            self.assertEqual(0, main(["--json", "--skip-scheduler"]))
        self.assertEqual(1, diagnose.call_count)
        self.assertEqual(["scheduler"], [item["name"] for item in json.loads(output.getvalue())["optional_warnings"]])

    def test_internal_failure_returns_two_without_exception_text(self) -> None:
        error = io.StringIO()
        with patch("preflight.doctor.diagnose", side_effect=RuntimeError("SECRET_PREFLIGHT_SENTINEL")), redirect_stderr(error):
            self.assertEqual(2, main(["--json", "--skip-scheduler"]))
        self.assertEqual("WUP setup preflight could not complete.\n", error.getvalue())
        self.assertNotIn("SECRET_PREFLIGHT_SENTINEL", error.getvalue())

    def test_real_preflight_does_not_mutate_fixture(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="wup-preflight-test-"))
        try:
            (root / "scripts").mkdir()
            for name in ("check_toolchain.py", "run_notifier.py", "wup_config.py"):
                (root / "scripts" / name).write_text("# mock", encoding="utf-8")
            config_path = root / "wup.toml"
            config_path.write_text("[tools]\nenabled = []\n", encoding="utf-8")
            state_path = root / "state.json"
            state_path.write_text('{"version": 2}', encoding="utf-8")
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["--repo-root", str(root), "--config", str(config_path), "--state-path", str(state_path), "--skip-scheduler"]))
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
