from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import update_apply
from check_toolchain import ToolCheckResult
from update_apply import (
    APPLY_ALLOWLIST,
    apply_update,
    main,
    probe_single_tool,
    resolve_npm_executable,
    validate_target_version,
)


def make_tool_result(
    name: str = "Wrangler",
    installed: str | None = "4.30.0",
    latest: str | None = "4.32.0",
    status: str = "UPDATE",
    health: str = "HEALTHY",
    install_method: str = "npm (wrangler)",
) -> ToolCheckResult:
    return ToolCheckResult(
        name=name,
        installed_version=installed,
        install_method=install_method,
        latest_version=latest,
        latest_source="npm registry",
        status=status,
        health=health,
    )


class TestUpdateApply(unittest.TestCase):
    def setUp(self):
        self.mock_npm = Path(__file__).resolve()

    def test_case_01_supported_opencodex_npm_success(self):
        pre_res = make_tool_result(
            name="OpenCodex",
            installed="2.31.0",
            latest="2.34.0",
            status="UPDATE",
            install_method="npm (@bitkyc08/opencodex)",
        )
        post_res = make_tool_result(
            name="OpenCodex",
            installed="2.34.0",
            latest="2.34.0",
            status="CURRENT",
            install_method="npm (@bitkyc08/opencodex)",
        )
        mock_auditor = MagicMock()
        mock_auditor.check_opencodex.side_effect = [(pre_res, None), (post_res, None)]

        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, msg = apply_update(
            "OpenCodex",
            yes=True,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
        )

        self.assertEqual("APPLY_VERIFIED", status)
        self.assertIn("successfully updated from 2.31.0 to 2.34.0", msg)
        mock_runner.assert_called_once()
        cmd = mock_runner.call_args[0][0]
        self.assertEqual([str(self.mock_npm), "install", "-g", "@bitkyc08/opencodex@2.34.0"], cmd)

    def test_case_02_supported_wrangler_npm_success(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]

        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, msg = apply_update(
            "Wrangler",
            yes=True,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
        )
        self.assertEqual("APPLY_VERIFIED", status)
        self.assertEqual([str(self.mock_npm), "install", "-g", "wrangler@4.32.0"], mock_runner.call_args[0][0])

    def test_case_03_codex_cli_success_when_install_method_is_npm(self):
        pre_res = make_tool_result(
            "Codex CLI",
            "0.150.1",
            "0.151.0",
            "UPDATE",
            install_method="npm (@openai/codex; active executable: C:/npm/codex.cmd)",
        )
        post_res = make_tool_result(
            "Codex CLI",
            "0.151.0",
            "0.151.0",
            "CURRENT",
            install_method="npm (@openai/codex; active executable: C:/npm/codex.cmd)",
        )
        mock_auditor = MagicMock()
        mock_auditor.check_codex_cli.side_effect = [pre_res, post_res]

        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, msg = apply_update(
            "Codex CLI",
            yes=True,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
        )
        self.assertEqual("APPLY_VERIFIED", status)
        self.assertEqual([str(self.mock_npm), "install", "-g", "@openai/codex@0.151.0"], mock_runner.call_args[0][0])

    def test_case_04_codex_cli_rejected_for_windows_app_or_msix(self):
        tool_res = make_tool_result(
            "Codex CLI",
            "0.150.1",
            "0.151.0",
            "UPDATE",
            install_method="Windows App package (C:/Program Files/WindowsApps/codex.exe)",
        )
        mock_auditor = MagicMock()
        mock_auditor.check_codex_cli.return_value = tool_res
        mock_runner = MagicMock()

        status, msg = apply_update(
            "Codex CLI",
            yes=True,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
        )
        self.assertEqual("PRECONDITION_FAILED", status)
        self.assertIn("Windows App package", msg)
        mock_runner.assert_not_called()

    def test_case_05_unsupported_tool_rejected_before_subprocess(self):
        mock_runner = MagicMock()
        status, msg = apply_update("Git", yes=True, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("UNSUPPORTED_TOOL", status)
        mock_runner.assert_not_called()

    def test_case_06_explicit_confirmation_required_by_default(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = pre_res
        mock_runner = MagicMock()
        mock_prompter = MagicMock(return_value="n")

        status, msg = apply_update(
            "Wrangler",
            yes=False,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
            input_prompter=mock_prompter,
        )
        mock_prompter.assert_called_once()
        self.assertEqual("USER_DECLINED", status)
        mock_runner.assert_not_called()

    def test_case_07_user_decline_does_not_execute_subprocess(self):
        pre_res = make_tool_result("OpenCodex", "2.31.0", "2.34.0", "UPDATE", install_method="npm")
        mock_auditor = MagicMock()
        mock_auditor.check_opencodex.return_value = (pre_res, None)
        mock_runner = MagicMock()
        mock_prompter = MagicMock(return_value="no")

        status, msg = apply_update(
            "OpenCodex",
            yes=False,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
            input_prompter=mock_prompter,
        )
        self.assertEqual("USER_DECLINED", status)
        mock_runner.assert_not_called()

    def test_case_08_yes_flag_bypasses_prompt_but_checks_safety_gates(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        mock_prompter = MagicMock()

        status, _ = apply_update(
            "Wrangler",
            yes=True,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
            input_prompter=mock_prompter,
        )
        self.assertEqual("APPLY_VERIFIED", status)
        mock_prompter.assert_not_called()

    def test_case_09_source_installed_version_toctou_already_current(self):
        current_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = current_res
        mock_runner = MagicMock()

        status, msg = apply_update(
            "Wrangler",
            yes=True,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
        )
        self.assertEqual("PRECONDITION_FAILED", status)
        self.assertIn("already at target version", msg)
        mock_runner.assert_not_called()

    def test_case_10_target_latest_unknown_fails_precondition(self):
        tool_res = make_tool_result("Wrangler", "4.30.0", None, "UNKNOWN")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = tool_res
        mock_runner = MagicMock()

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("PRECONDITION_FAILED", status)
        self.assertIn("could not be determined", msg)
        mock_runner.assert_not_called()

    def test_case_11_manual_mechanism_rejected(self):
        tool_res = make_tool_result("Codex CLI", "0.150.1", "0.151.0", "UPDATE", install_method="binary (C:/bin/codex.exe)")
        mock_auditor = MagicMock()
        mock_auditor.check_codex_cli.return_value = tool_res
        mock_runner = MagicMock()

        status, msg = apply_update("Codex CLI", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("PRECONDITION_FAILED", status)
        mock_runner.assert_not_called()

    def test_case_12_unknown_mechanism_rejected(self):
        tool_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UNKNOWN", install_method="unknown")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = tool_res
        mock_runner = MagicMock()

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("PRECONDITION_FAILED", status)
        mock_runner.assert_not_called()

    def test_case_13_npm_not_found_causes_no_mutation(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = pre_res
        mock_runner = MagicMock()

        status, msg = apply_update(
            "Wrangler",
            yes=True,
            auditor=mock_auditor,
            npm_path=Path("C:/does_not_exist/npm.cmd"),
            subprocess_runner=mock_runner,
        )
        self.assertEqual("NPM_NOT_FOUND", status)
        mock_runner.assert_not_called()

    def test_case_14_npm_invoked_with_structured_argv(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        args, _ = mock_runner.call_args
        cmd = args[0]
        self.assertIsInstance(cmd, list)
        self.assertEqual([str(self.mock_npm), "install", "-g", "wrangler@4.32.0"], cmd)

    def test_case_15_npm_invoked_with_shell_false(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        _, kwargs = mock_runner.call_args
        self.assertFalse(kwargs.get("shell", True))

    def test_case_16_npm_invoked_with_exact_version_pin(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        cmd = mock_runner.call_args[0][0]
        self.assertEqual("wrangler@4.32.0", cmd[3])

    def test_case_17_npm_invoked_with_bounded_timeout(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, _ = apply_update(
            "Wrangler",
            yes=True,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
            timeout=120,
        )
        self.assertEqual("APPLY_VERIFIED", status)
        _, kwargs = mock_runner.call_args
        self.assertEqual(120, kwargs.get("timeout"))

    def test_case_31_main_exit_codes(self):
        with patch("update_apply.apply_update", return_value=("APPLY_VERIFIED", "all good")):
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(0, main(["--tool", "Wrangler", "--yes"]))

        with patch("update_apply.apply_update", return_value=("PRECONDITION_FAILED", "failed")):
            with patch("sys.stderr", io.StringIO()):
                self.assertEqual(1, main(["--tool", "Wrangler", "--yes"]))

    def test_case_32_resolve_npm_executable_returns_absolute_path_when_present(self):
        resolved = resolve_npm_executable()
        if resolved is not None:
            self.assertTrue(resolved.is_absolute())
            self.assertTrue(resolved.is_file())
            self.assertIn(resolved.name.lower(), ("npm.cmd", "npm.exe", "npm"))

    def test_case_18_npm_nonzero_fails_with_apply_execution_failed(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = pre_res
        mock_runner = MagicMock(return_value=MagicMock(returncode=1, stdout="", stderr="network error"))

        status, msg = apply_update(
            "Wrangler",
            yes=True,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
        )
        self.assertEqual("APPLY_EXECUTION_FAILED", status)
        self.assertIn("failed with exit code 1", msg)
        self.assertIn("npm install -g wrangler@4.30.0", msg)

    def test_case_19_timeout_causes_execution_failure(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = pre_res
        mock_runner = MagicMock(side_effect=subprocess.TimeoutExpired(cmd=["npm"], timeout=120))

        status, msg = apply_update(
            "Wrangler",
            yes=True,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
        )
        self.assertEqual("APPLY_EXECUTION_FAILED", status)
        self.assertIn("timed out", msg)
        self.assertIn("npm install -g wrangler@4.30.0", msg)

    def test_case_20_permission_like_failure_reports_elevation_required_no_uac(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = pre_res
        mock_runner = MagicMock(return_value=MagicMock(returncode=1, stdout="", stderr="npm ERR! code EPERM"))

        status, msg = apply_update(
            "Wrangler",
            yes=True,
            auditor=mock_auditor,
            npm_path=self.mock_npm,
            subprocess_runner=mock_runner,
        )
        self.assertEqual("ELEVATION_REQUIRED", status)
        self.assertIn("does not perform administrative elevation", msg)
        self.assertIn("npm install -g wrangler@4.30.0", msg)

    def test_case_21_npm_exit_0_correct_version_passes_verified(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT", health="HEALTHY")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFIED", status)
        self.assertIn("4.32.0", msg)

    def test_case_22_npm_exit_0_wrong_installed_version_fails_verification(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE", health="HEALTHY")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFICATION_FAILED", status)
        self.assertIn("expected 4.32.0, observed 4.30.0", msg)
        self.assertIn("npm install -g wrangler@4.30.0", msg)

    def test_case_23_no_automatic_rollback_subprocess(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = pre_res
        mock_runner = MagicMock(return_value=MagicMock(returncode=1, stdout="", stderr="fatal error"))

        apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual(1, mock_runner.call_count)

    def test_case_24_recovery_guidance_references_previous_exact_version(self):
        pre_res = make_tool_result("OpenCodex", "2.33.0", "2.35.0", "UPDATE", install_method="npm")
        mock_auditor = MagicMock()
        mock_auditor.check_opencodex.return_value = (pre_res, None)
        mock_runner = MagicMock(return_value=MagicMock(returncode=1, stdout="", stderr="error"))

        _, msg = apply_update("OpenCodex", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertIn("npm install -g @bitkyc08/opencodex@2.33.0", msg)

    def test_case_25_no_arbitrary_package_name_accepted(self):
        mock_runner = MagicMock()
        status, _ = apply_update("malicious-package", yes=True, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("UNSUPPORTED_TOOL", status)
        mock_runner.assert_not_called()

    def test_case_26_target_version_safety_validation(self):
        self.assertEqual("1.2.3", validate_target_version("1.2.3"))
        self.assertEqual("2.35.0-beta.1", validate_target_version("2.35.0-beta.1"))
        with self.assertRaises(update_apply.PreconditionFailedError):
            validate_target_version("1.2.3; rm -rf /")
        with self.assertRaises(update_apply.PreconditionFailedError):
            validate_target_version("1.2.3 && calc.exe")
        with self.assertRaises(update_apply.PreconditionFailedError):
            validate_target_version("")

    def test_case_27_no_all_or_batch_flag_in_cli(self):
        with patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                main(["--all"])
            self.assertNotEqual(0, cm.exception.code)

    def test_case_28_no_scheduler_or_notifier_integration(self):
        root = Path(__file__).resolve().parent.parent
        for rel in [
            "scripts/run_notifier.py",
            "scripts/manage-scheduled-task.ps1",
            "scripts/run-scheduled-notifier.ps1",
            "scripts/remote_version_sentinel.py",
        ]:
            p = root / rel
            if p.exists():
                content = p.read_text(encoding="utf-8")
                self.assertNotIn("update_apply", content)
                self.assertNotIn("--apply", content)

    def test_case_29_history_integration_is_deferred(self):
        self.assertTrue(hasattr(update_apply, "apply_update"))

    def test_case_30_import_update_apply_has_no_side_effects(self):
        with (
            patch("subprocess.run", side_effect=AssertionError("subprocess called")),
            patch("socket.socket", side_effect=AssertionError("network called")),
        ):
            import importlib
            importlib.reload(update_apply)

    def test_case_31_main_exit_codes(self):
        with patch("update_apply.apply_update", return_value=("APPLY_VERIFIED", "all good")):
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(0, main(["--tool", "Wrangler", "--yes"]))

        with patch("update_apply.apply_update", return_value=("PRECONDITION_FAILED", "failed")):
            with patch("sys.stderr", io.StringIO()):
                self.assertEqual(1, main(["--tool", "Wrangler", "--yes"]))

    def test_case_32_resolve_npm_executable_returns_absolute_path_when_present(self):
        resolved = resolve_npm_executable()
        if resolved is not None:
            self.assertTrue(resolved.is_absolute())
            self.assertTrue(resolved.is_file())
            self.assertIn(resolved.name.lower(), ("npm.cmd", "npm.exe", "npm"))

    # -----------------------------------------------------------------------
    # Corrective turn: Blocker 1 - Strict install_method gating on all tools
    # -----------------------------------------------------------------------

    def test_blocker1_opencodex_unknown_install_method_rejected(self):
        tool_res = make_tool_result("OpenCodex", "2.31.0", "2.34.0", "UPDATE", install_method="unknown")
        mock_auditor = MagicMock()
        mock_auditor.check_opencodex.return_value = (tool_res, None)
        mock_runner = MagicMock()

        status, msg = apply_update("OpenCodex", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("PRECONDITION_FAILED", status)
        self.assertIn("only confirmed npm installations are supported", msg)
        mock_runner.assert_not_called()

    def test_blocker1_opencodex_binary_install_method_rejected(self):
        tool_res = make_tool_result("OpenCodex", "2.31.0", "2.34.0", "UPDATE", install_method="binary (C:/bin/ocx.exe)")
        mock_auditor = MagicMock()
        mock_auditor.check_opencodex.return_value = (tool_res, None)
        mock_runner = MagicMock()

        status, msg = apply_update("OpenCodex", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("PRECONDITION_FAILED", status)
        self.assertIn("only confirmed npm installations are supported", msg)
        mock_runner.assert_not_called()

    def test_blocker1_wrangler_unknown_install_method_rejected(self):
        tool_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE", install_method="unknown")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = tool_res
        mock_runner = MagicMock()

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("PRECONDITION_FAILED", status)
        self.assertIn("only confirmed npm installations are supported", msg)
        mock_runner.assert_not_called()

    def test_blocker1_wrangler_manual_install_method_rejected(self):
        tool_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE", install_method="manual")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.return_value = tool_res
        mock_runner = MagicMock()

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("PRECONDITION_FAILED", status)
        self.assertIn("only confirmed npm installations are supported", msg)
        mock_runner.assert_not_called()

    def test_blocker1_opencodex_npm_install_method_eligible(self):
        pre_res = make_tool_result("OpenCodex", "2.31.0", "2.34.0", "UPDATE", install_method="npm (@bitkyc08/opencodex)")
        post_res = make_tool_result("OpenCodex", "2.34.0", "2.34.0", "CURRENT", health="HEALTHY", install_method="npm (@bitkyc08/opencodex)")
        mock_auditor = MagicMock()
        mock_auditor.check_opencodex.side_effect = [(pre_res, None), (post_res, None)]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, _ = apply_update("OpenCodex", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFIED", status)

    def test_blocker1_wrangler_npm_install_method_eligible(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE", install_method="npm (wrangler)")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT", health="HEALTHY", install_method="npm (wrangler)")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, _ = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFIED", status)

    # -----------------------------------------------------------------------
    # Corrective turn: Blocker 2 - Strict post-update health check (requires HEALTHY)
    # -----------------------------------------------------------------------

    def test_blocker2_post_update_health_healthy_passes(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT", health="HEALTHY")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, _ = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFIED", status)

    def test_blocker2_post_update_health_unverified_fails_verification(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT", health="UNVERIFIED")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFICATION_FAILED", status)
        self.assertIn("expected HEALTHY, observed 'UNVERIFIED'", msg)
        self.assertIn("npm install -g wrangler@4.30.0", msg)

    def test_blocker2_post_update_health_degraded_fails_verification(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT", health="DEGRADED")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFICATION_FAILED", status)
        self.assertIn("expected HEALTHY, observed 'DEGRADED'", msg)

    def test_blocker2_post_update_health_not_installed_fails_verification(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT", health="NOT_INSTALLED")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFICATION_FAILED", status)
        self.assertIn("expected HEALTHY, observed 'NOT_INSTALLED'", msg)

    def test_blocker2_post_update_health_missing_fails_verification(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.32.0", "4.32.0", "CURRENT", health="")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFICATION_FAILED", status)
        self.assertIn("expected HEALTHY", msg)

    def test_blocker2_wrong_version_with_healthy_fails_verification(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE", health="HEALTHY")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, msg = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFICATION_FAILED", status)
        self.assertIn("expected 4.32.0, observed 4.30.0", msg)

    def test_blocker2_npm_exit_zero_alone_never_yields_success_without_verification(self):
        pre_res = make_tool_result("Wrangler", "4.30.0", "4.32.0", "UPDATE")
        post_res = make_tool_result("Wrangler", None, "4.32.0", "UNKNOWN", health="NOT_INSTALLED")
        mock_auditor = MagicMock()
        mock_auditor.check_wrangler.side_effect = [pre_res, post_res]
        mock_runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        status, _ = apply_update("Wrangler", yes=True, auditor=mock_auditor, npm_path=self.mock_npm, subprocess_runner=mock_runner)
        self.assertEqual("APPLY_VERIFICATION_FAILED", status)


if __name__ == "__main__":
    unittest.main()
