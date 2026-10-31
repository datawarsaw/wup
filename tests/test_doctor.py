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

from doctor import (
    DoctorCheck,
    DoctorReport,
    check_configuration,
    check_repository,
    check_runtime,
    doctor_exit_code,
    diagnose,
    format_json_report,
    format_text_report,
    main,
)


class TestDoctor(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp(prefix="wup-doctor-test-"))
        self.repo_root = self.test_dir / "repo"
        self.repo_root.mkdir()
        (self.repo_root / "scripts").mkdir()
        (self.repo_root / "scripts" / "check_toolchain.py").write_text("# mock", encoding="utf-8")
        (self.repo_root / "scripts" / "wup_config.py").write_text("# mock", encoding="utf-8")

        self.config_path = self.repo_root / "wup.toml"
        self.config_path.write_text("[tools]\nenabled = []\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_case_1_healthy_baseline_returns_success(self) -> None:
        """Case 1: Fully valid baseline reports HEALTHY."""
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
        )
        self.assertEqual(report.status, "HEALTHY")
        self.assertTrue(all(c.status == "HEALTHY" for c in report.checks))

    def test_case_2_missing_required_repository_file_fails(self) -> None:
        """Case 2: Missing required repository script reports UNHEALTHY with missing file details."""
        (self.repo_root / "scripts" / "check_toolchain.py").unlink()
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
        )
        self.assertEqual(report.status, "UNHEALTHY")
        repo_check = next(c for c in report.checks if c.name == "repository")
        self.assertEqual(repo_check.status, "UNHEALTHY")
        self.assertIn("check_toolchain.py", repo_check.message)

    def test_case_3_malformed_config_fails_actionably(self) -> None:
        """Case 3: Malformed TOML syntax in config file reports UNHEALTHY."""
        self.config_path.write_text("invalid toml syntax without equals\n", encoding="utf-8")
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
        )
        self.assertEqual(report.status, "UNHEALTHY")
        cfg_check = next(c for c in report.checks if c.name == "configuration")
        self.assertEqual(cfg_check.status, "UNHEALTHY")
        self.assertIn("invalid configuration", cfg_check.message)

    def test_case_4_optional_missing_config_uses_defaults(self) -> None:
        """Case 4: Missing config is optional and reports HEALTHY."""
        self.config_path.unlink()
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
        )
        self.assertEqual(report.status, "HEALTHY")
        cfg_check = next(c for c in report.checks if c.name == "configuration")
        self.assertEqual(cfg_check.status, "HEALTHY")
        self.assertIn("using defaults", cfg_check.message)

    def test_case_5_missing_runtime_executable_detected(self) -> None:
        """Case 5: Missing required runtime tool (git) is detected as DEGRADED."""
        with patch("shutil.which", return_value=None):
            check = check_runtime()
        self.assertEqual(check.status, "DEGRADED")
        self.assertIn("git", check.message)

    def test_case_6_mutation_proof_guarantee(self) -> None:
        """Case 6: Prove doctor execution never creates, modifies, or deletes any file."""
        def capture_snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
            snapshot = {}
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    rel = str(path.relative_to(root))
                    snapshot[rel] = (path.stat().st_size, path.read_bytes())
            return snapshot

        before = capture_snapshot(self.test_dir)

        diagnose(repo_root=self.repo_root, config_path=self.config_path)
        diagnose(repo_root=self.repo_root, config_path=self.config_path)

        after = capture_snapshot(self.test_dir)
        self.assertEqual(before, after)

    def test_case_7_structured_report_serialization(self) -> None:
        """Case 7: Structured report serializes to clean JSON dict."""
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
        )
        d = report.to_dict()
        self.assertEqual(d["status"], "HEALTHY")
        self.assertIsInstance(d["checks"], list)
        self.assertEqual(len(d["checks"]), 3)
        self.assertEqual(
            ["repository", "configuration", "runtime"],
            [item["name"] for item in d["checks"]],
        )

    def test_case_8_removed_state_and_scheduler_checks_are_absent(self) -> None:
        """Case 8: Diagnose no longer reports deduplication state or scheduler checks."""
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
        )
        names = [check.name for check in report.checks]
        self.assertNotIn("state", names)
        self.assertNotIn("scheduler", names)

    def test_case_9_repository_check_requires_toolchain_and_config_only(self) -> None:
        """Case 9: The repository check requires check_toolchain.py and wup_config.py only."""
        check = check_repository(self.repo_root)
        self.assertEqual(check.status, "HEALTHY")
        self.assertEqual(check.details.get("missing", []), [])

        (self.repo_root / "scripts" / "wup_config.py").unlink()
        check = check_repository(self.repo_root)
        self.assertEqual(check.status, "UNHEALTHY")
        missing = {Path(item) for item in check.details["missing"]}
        self.assertIn(Path("scripts") / "wup_config.py", missing)
        self.assertNotIn(Path("scripts") / "run_notifier.py", missing)

    def test_case_10_configuration_summary_omits_removed_notification_fields(self) -> None:
        """Case 10: The configuration summary no longer exposes notification settings."""
        check = check_configuration(self.config_path)
        self.assertEqual(check.status, "HEALTHY")
        self.assertNotIn("telegram_enabled", check.details)
        self.assertNotIn("email_configured", check.details)
        self.assertIn("tools_enabled", check.details)


class TestDoctorCliContract(unittest.TestCase):
    def test_json_whitelists_fields_and_preserves_check_order(self):
        report = DoctorReport("HEALTHY", [DoctorCheck("one", "HEALTHY", "ok", {"token": "secret"}), DoctorCheck("two", "DEGRADED", "optional")])
        rendered = format_json_report(report)
        self.assertEqual(rendered, format_json_report(report))
        self.assertEqual(["one", "two"], [item["name"] for item in json.loads(rendered)["checks"]])
        self.assertNotIn("secret", rendered)
        self.assertNotIn("details", rendered)

    def test_exit_codes_follow_existing_required_readiness(self):
        self.assertEqual(0, doctor_exit_code(DoctorReport("HEALTHY", [])))
        self.assertEqual(0, doctor_exit_code(DoctorReport("DEGRADED", [DoctorCheck("runtime", "DEGRADED", "optional")])))
        self.assertEqual(1, doctor_exit_code(DoctorReport("UNHEALTHY", [])))

    def test_main_evaluates_once_and_keeps_unhealthy_diagnostics(self):
        report = DoctorReport("UNHEALTHY", [DoctorCheck("configuration", "UNHEALTHY", "malformed")])
        out, err = io.StringIO(), io.StringIO()
        with patch("doctor.diagnose", return_value=report) as mocked, redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(1, main(["--json"]))
        self.assertEqual(1, mocked.call_count)
        self.assertEqual("malformed", json.loads(out.getvalue())["checks"][0]["message"])
        self.assertEqual("", err.getvalue())

    def test_default_text_and_internal_failure_exit_two(self):
        report = DoctorReport("HEALTHY", [DoctorCheck("repository", "HEALTHY", "ok")])
        out = io.StringIO()
        with patch("doctor.diagnose", return_value=report), redirect_stdout(out):
            self.assertEqual(0, main([]))
        self.assertEqual(format_text_report(report) + "\n", out.getvalue())
        err = io.StringIO()
        with patch("doctor.diagnose", side_effect=RuntimeError("SECRET_TOKEN_ABC123")), redirect_stderr(err):
            self.assertEqual(2, main(["--json"]))
        self.assertEqual("WUP doctor could not complete.\n", err.getvalue())
        self.assertNotIn("SECRET_TOKEN_ABC123", err.getvalue())

    def test_removed_cli_flags_are_rejected(self):
        for flag in (["--state-path", "state.json"], ["--skip-scheduler"]):
            with self.subTest(flag=flag):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(flag)
                self.assertEqual(2, raised.exception.code)

    def test_parse_failure_sentinels_are_not_rendered(self):
        with patch("doctor.wup_config.load_config", side_effect=ValueError("SECRET_TOKEN_ABC123")):
            check = check_configuration(Path("bad.toml"))
        report = DoctorReport("UNHEALTHY", [check])
        self.assertEqual("configuration", check.name)
        self.assertNotIn("SECRET_TOKEN_ABC123", format_text_report(report))
        self.assertNotIn("SECRET_TOKEN_ABC123", format_json_report(report))


if __name__ == "__main__":
    unittest.main()
