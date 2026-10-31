import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from wup_config import DEFAULT_CONFIG, apply_runtime_config, load_config


REMOVED_CONFIG_SECTIONS = ("notifications", "remote")
REMOVED_ENVIRONMENT_KEYS = (
    "WUP_REMOTE_REPOSITORY",
    "WUP_STATE_BRANCH",
    "WUP_SNAPSHOT_PUBLISH",
    "WUP_TELEGRAM_ENABLED",
    "WUP_TELEGRAM_ENV_FILE",
    "WUP_EMAIL_COMMAND",
)


class PortabilityTests(unittest.TestCase):
    def test_clean_defaults_are_secret_free_and_minimal(self):
        self.assertEqual(set(DEFAULT_CONFIG), {"tools", "local"})
        for section in REMOVED_CONFIG_SECTIONS:
            self.assertNotIn(section, DEFAULT_CONFIG)
        self.assertNotIn("TOKEN", repr(DEFAULT_CONFIG).upper())
        self.assertNotIn("SECRET", repr(DEFAULT_CONFIG).upper())

    def test_removed_sections_are_ignored_when_loading_a_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wup.toml"
            path.write_text(
                '[local]\nworkstation_ops_path = "C:/optional/workstation-ops"\n'
                '[notifications.telegram]\nenabled = true\nenv_file = "C:/generic/secrets.env"\n'
                '[notifications.email]\ncommand = "node send.js"\n'
                '[remote]\nrepository = "example/wup"\nstate_branch = "state"\npublish_snapshot = true\n',
                encoding="utf-8",
            )
            config = load_config(path)
        self.assertEqual(set(config), {"tools", "local"})
        for section in REMOVED_CONFIG_SECTIONS:
            self.assertNotIn(section, config)
        self.assertEqual(config["local"]["workstation_ops_path"], "C:/optional/workstation-ops")

    def test_config_drives_only_the_local_runtime_identity(self):
        previous = {key: os.environ.get(key) for key in ("WUP_ENABLED_TOOLS", "WUP_WORKSTATION_OPS_PATH")}
        removed = {key: os.environ.pop(key, None) for key in REMOVED_ENVIRONMENT_KEYS}
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "wup.toml"
                path.write_text('[tools]\nenabled = ["Node.js", "Git"]\n[local]\nworkstation_ops_path = "C:/optional/workstation-ops"\n', encoding="utf-8")
                config = load_config(path)
                apply_runtime_config(config)
            self.assertEqual(os.environ["WUP_ENABLED_TOOLS"], "Node.js,Git")
            self.assertEqual(os.environ["WUP_WORKSTATION_OPS_PATH"], "C:/optional/workstation-ops")
            for key in REMOVED_ENVIRONMENT_KEYS:
                self.assertNotIn(key, os.environ)
        finally:
            for key, value in previous.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value
            for key, value in removed.items():
                if value is not None: os.environ[key] = value

    def test_audit_cli_applies_selected_configuration(self):
        import check_toolchain
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wup.toml"
            path.write_text('[tools]\nenabled = ["Git"]\n[local]\nworkstation_ops_path = "C:/example"\n', encoding="utf-8")
            def audit():
                self.assertEqual(os.environ["WUP_ENABLED_TOOLS"], "Git")
                self.assertEqual(os.environ["WUP_WORKSTATION_OPS_PATH"], "C:/example")
                return object()
            with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", ["check_toolchain.py", "--config", str(path), "--offline"]), patch.object(check_toolchain.ToolchainAuditor, "audit_all", side_effect=audit), patch.object(check_toolchain, "format_text_report", return_value="report"), patch("builtins.print"):
                self.assertEqual(check_toolchain.main(), 0)

    def test_product_logic_has_no_internal_repository_dependency(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in SCRIPTS.glob("*.py"))
        self.assertNotIn("datawarsaw/code-skills", source)
        self.assertNotIn("C:\\AI\\code-skills", source)
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("code-skills", workflow)

    def test_notification_and_scheduler_infrastructure_is_absent(self):
        removed_files = (
            "scripts/run_notifier.py",
            "scripts/telegram_notify.py",
            "scripts/telegram_secrets.py",
            "scripts/remote_version_sentinel.py",
            "scripts/workstation_snapshot.py",
            "scripts/run-scheduled-notifier.ps1",
            "scripts/manage-scheduled-task.ps1",
            "scripts/validate-scheduled-context.ps1",
            ".github/workflows/toolchain-remote-version-sentinel.yml",
        )
        for relative in removed_files:
            with self.subTest(path=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_scripts_do_not_reference_removed_notification_infrastructure(self):
        for path in sorted(SCRIPTS.glob("*.py")):
            with self.subTest(script=path.name):
                source = path.read_text(encoding="utf-8").lower()
                for marker in ("telegram", "run_notifier", "remote_version_sentinel", "workstation_snapshot", "last-alerted"):
                    self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
