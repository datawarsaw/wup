import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from wup_config import DEFAULT_CONFIG, apply_runtime_config, load_config
from workstation_snapshot import publish_snapshot
from telegram_notify import load_credentials


class PortabilityTests(unittest.TestCase):
    def test_clean_defaults_are_secret_free_and_telegram_optional(self):
        self.assertFalse(DEFAULT_CONFIG["notifications"]["telegram"]["enabled"])
        self.assertEqual(DEFAULT_CONFIG["notifications"]["email"]["command"], "")
        self.assertNotIn("TOKEN", repr(DEFAULT_CONFIG).upper())

    def test_config_drives_portable_remote_identity(self):
        previous = {key: os.environ.get(key) for key in ("WUP_REMOTE_REPOSITORY", "WUP_STATE_BRANCH", "WUP_SNAPSHOT_PUBLISH", "WUP_TELEGRAM_ENABLED", "WUP_TELEGRAM_ENV_FILE", "WUP_WORKSTATION_OPS_PATH")}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wup.toml"
            path.write_text('[local]\nworkstation_ops_path = "C:/optional/workstation-ops"\n[remote]\nrepository = "example/wup"\nstate_branch = "state"\npublish_snapshot = true\n[notifications.telegram]\nenabled = true\nenv_file = "C:/generic/secrets.env"\n', encoding="utf-8")
            config = load_config(path); apply_runtime_config(config)
            self.assertEqual(os.environ["WUP_REMOTE_REPOSITORY"], "example/wup")
            self.assertEqual(os.environ["WUP_STATE_BRANCH"], "state")
            self.assertEqual(os.environ["WUP_SNAPSHOT_PUBLISH"], "1")
            self.assertEqual(os.environ["WUP_TELEGRAM_ENV_FILE"], "C:/generic/secrets.env")
            self.assertEqual(os.environ["WUP_WORKSTATION_OPS_PATH"], "C:/optional/workstation-ops")
        for key, value in previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value

    def test_snapshot_requires_configured_repository(self):
        previous = os.environ.pop("WUP_REMOTE_REPOSITORY", None)
        try: self.assertFalse(publish_snapshot({"version": 1, "measured_at": "x", "tools": {}}, lambda *a, **k: None))
        finally:
            if previous is not None: os.environ["WUP_REMOTE_REPOSITORY"] = previous

    def test_product_logic_has_no_internal_repository_dependency(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in SCRIPTS.glob("*.py"))
        self.assertNotIn("datawarsaw/code-skills", source)
        self.assertNotIn("C:\\AI\\code-skills", source)
        workflow = (ROOT / ".github" / "workflows" / "toolchain-remote-version-sentinel.yml").read_text(encoding="utf-8")
        self.assertNotIn("code-skills", workflow)
        self.assertIn("scripts/remote_version_sentinel.py", workflow)

    def test_telegram_env_file_is_narrow_and_process_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.env"
            path.write_text("TELEGRAM_BOT_TOKEN=file-token\nTELEGRAM_CHAT_ID=file-chat\nUNRELATED=value\n", encoding="utf-8")
            values = load_credentials({"TELEGRAM_BOT_TOKEN": "process-token", "WUP_TELEGRAM_ENV_FILE": str(path)})
            self.assertEqual(values, {"TELEGRAM_BOT_TOKEN": "process-token", "TELEGRAM_CHAT_ID": "file-chat"})
            self.assertNotIn("UNRELATED", values)

    def test_missing_or_malformed_telegram_env_file_exposes_no_secret_material(self):
        values = load_credentials({"WUP_TELEGRAM_ENV_FILE": "C:/does-not-exist/secrets.env"})
        self.assertEqual(values, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""})

    def test_public_workflow_uses_private_snapshot_secret_and_daily_schedule(self):
        workflow = (ROOT / ".github" / "workflows" / "toolchain-remote-version-sentinel.yml").read_text(encoding="utf-8")
        self.assertIn("secrets.WUP_WORKSTATION_SNAPSHOT", workflow)
        self.assertNotIn("workstation-snapshot.json >", workflow)
        self.assertIn('schedule:\n    - cron: "30 6 * * *"', workflow)
        self.assertIn("workflow_dispatch:", workflow)

    def test_scheduled_runner_requires_explicit_config_path(self):
        runner = (SCRIPTS / "run-scheduled-notifier.ps1").read_text(encoding="utf-8")
        manager = (SCRIPTS / "manage-scheduled-task.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$ConfigPath", runner)
        self.assertIn("$env:WUP_CONFIG", runner)
        self.assertIn("-ConfigPath $(Quote-TaskArgument $resolvedConfig)", manager)


if __name__ == "__main__": unittest.main()
