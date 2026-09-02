#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from doctor import (
    DoctorCheck,
    DoctorReport,
    check_configuration,
    check_repository,
    check_runtime,
    check_scheduler,
    check_state,
    diagnose,
)


class TestDoctor(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp(prefix="wup-doctor-test-"))
        self.repo_root = self.test_dir / "repo"
        self.repo_root.mkdir()
        (self.repo_root / "scripts").mkdir()
        (self.repo_root / "scripts" / "check_toolchain.py").write_text("# mock", encoding="utf-8")
        (self.repo_root / "scripts" / "run_notifier.py").write_text("# mock", encoding="utf-8")
        (self.repo_root / "scripts" / "wup_config.py").write_text("# mock", encoding="utf-8")

        self.config_path = self.repo_root / "wup.toml"
        self.config_path.write_text("[tools]\nenabled = []\n", encoding="utf-8")

        self.state_path = self.test_dir / "last-alerted.json"
        self.state_path.write_text(json.dumps({"version": 2, "last_alerted": {}}), encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_case_1_healthy_baseline_returns_success(self) -> None:
        """Case 1: Fully valid baseline reports HEALTHY."""
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
            state_path=self.state_path,
            check_scheduler_task=False,
        )
        self.assertEqual(report.status, "HEALTHY")
        self.assertTrue(all(c.status == "HEALTHY" for c in report.checks))

    def test_case_2_missing_required_repository_file_fails(self) -> None:
        """Case 2: Missing required repository script reports UNHEALTHY with missing file details."""
        (self.repo_root / "scripts" / "check_toolchain.py").unlink()
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
            state_path=self.state_path,
            check_scheduler_task=False,
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
            state_path=self.state_path,
            check_scheduler_task=False,
        )
        self.assertEqual(report.status, "UNHEALTHY")
        cfg_check = next(c for c in report.checks if c.name == "configuration")
        self.assertEqual(cfg_check.status, "UNHEALTHY")
        self.assertIn("invalid configuration", cfg_check.message)

    def test_case_4_malformed_state_fails_actionably(self) -> None:
        """Case 4: Corrupted state JSON reports UNHEALTHY."""
        self.state_path.write_text("{not valid json", encoding="utf-8")
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
            state_path=self.state_path,
            check_scheduler_task=False,
        )
        self.assertEqual(report.status, "UNHEALTHY")
        state_check = next(c for c in report.checks if c.name == "state")
        self.assertEqual(state_check.status, "UNHEALTHY")
        self.assertIn("unreadable or malformed state file", state_check.message)

    def test_case_5_optional_missing_state_does_not_fail(self) -> None:
        """Case 5: Missing state file is acceptable on clean install and reports HEALTHY."""
        self.state_path.unlink()
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
            state_path=self.state_path,
            check_scheduler_task=False,
        )
        self.assertEqual(report.status, "HEALTHY")
        state_check = next(c for c in report.checks if c.name == "state")
        self.assertEqual(state_check.status, "HEALTHY")
        self.assertIn("not yet present", state_check.message)

    def test_case_6_optional_missing_config_uses_defaults(self) -> None:
        """Missing config is optional and reports HEALTHY."""
        self.config_path.unlink()
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
            state_path=self.state_path,
            check_scheduler_task=False,
        )
        self.assertEqual(report.status, "HEALTHY")
        cfg_check = next(c for c in report.checks if c.name == "configuration")
        self.assertEqual(cfg_check.status, "HEALTHY")
        self.assertIn("using defaults", cfg_check.message)

    def test_case_7_missing_runtime_executable_detected(self) -> None:
        """Case 6 (prompt): Missing required runtime tool (git) is detected as DEGRADED."""
        with patch("shutil.which", return_value=None):
            check = check_runtime()
        self.assertEqual(check.status, "DEGRADED")
        self.assertIn("git", check.message)

    def test_case_8_scheduler_inspection_handled_read_only(self) -> None:
        """Case 7 (prompt): Scheduler inspection failure or absence is handled without raising exception."""
        with patch("subprocess.run", side_effect=OSError("simulated error")):
            check = check_scheduler()
        self.assertIn(check.status, ("HEALTHY", "DEGRADED"))

    def test_case_9_mutation_proof_guarantee(self) -> None:
        """Step 10: Prove doctor execution never creates, modifies, or deletes any file."""
        def capture_snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
            snapshot = {}
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    rel = str(path.relative_to(root))
                    snapshot[rel] = (path.stat().st_size, path.read_bytes())
            return snapshot

        before = capture_snapshot(self.test_dir)

        # Execute doctor multiple times
        diagnose(repo_root=self.repo_root, config_path=self.config_path, state_path=self.state_path, check_scheduler_task=False)
        diagnose(repo_root=self.repo_root, config_path=self.config_path, state_path=self.state_path, check_scheduler_task=False)

        after = capture_snapshot(self.test_dir)
        self.assertEqual(before, after)

    def test_case_10_structured_report_serialization(self) -> None:
        """Structured report serializes to clean JSON dict."""
        report = diagnose(
            repo_root=self.repo_root,
            config_path=self.config_path,
            state_path=self.state_path,
            check_scheduler_task=False,
        )
        d = report.to_dict()
        self.assertEqual(d["status"], "HEALTHY")
        self.assertIsInstance(d["checks"], list)
        self.assertEqual(len(d["checks"]), 4)


if __name__ == "__main__":
    unittest.main()
