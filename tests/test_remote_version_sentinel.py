import sys
import json
import tempfile
import os
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_toolchain import REMOTE_VERSION_SOURCES, NetworkFetcher, resolve_remote_upstream_versions
from remote_version_sentinel import SentinelError, evaluate, read_snapshot, read_state, telegram_result, telegram_result_with_snapshot
from workstation_snapshot import SNAPSHOT_TOOLS, build_snapshot, publish_snapshot
from run_notifier import execute


def observed(**versions):
    return {name: {"latest_version": version, "release_url": f"https://example.test/{name}/{version}"} for name, version in versions.items()}


class RemoteSentinelTests(unittest.TestCase):
    def test_first_run_baselines_silently(self):
        result = evaluate(None, observed(**{"Codex CLI": "1.0.0"}), {}, "2026-08-29T00:00:00+00:00")
        self.assertEqual(result["status"], "BASELINE_CREATED")
        self.assertEqual(result["changes"], [])
        self.assertTrue(result["state_changed"])

    def test_one_and_multiple_version_changes(self):
        previous = {"version": 1, "tools": observed(**{"Codex CLI": "1.0.0", "Node.js": "24.19.0"})}
        result = evaluate(previous, observed(**{"Codex CLI": "1.1.0", "Node.js": "24.20.0"}), {}, "2026-08-29T00:00:00+00:00")
        self.assertEqual(result["status"], "CHANGED")
        self.assertEqual(result["changes"], [{"name": "Codex CLI", "from": "1.0.0", "to": "1.1.0"}, {"name": "Node.js", "from": "24.19.0", "to": "24.20.0"}])

    def test_identical_and_release_url_only_changes_are_silent(self):
        previous = {"version": 1, "tools": observed(**{"Codex CLI": "1.0.0"})}
        current = observed(**{"Codex CLI": "1.0.0"})
        current["Codex CLI"]["release_url"] = "https://example.test/new-url"
        result = evaluate(previous, current, {}, "2026-08-29T00:00:00+00:00")
        self.assertEqual(result["status"], "NO_CHANGE")
        self.assertFalse(result["state_changed"])

    def test_partial_failure_preserves_good_state(self):
        previous = {"version": 1, "tools": observed(**{"Codex CLI": "1.0.0", "Git": "2.0.0"})}
        result = evaluate(previous, observed(**{"Codex CLI": "1.0.0"}), {"Git": "unavailable"}, "2026-08-29T00:00:00+00:00")
        self.assertEqual(result["status"], "NO_CHANGE")
        self.assertEqual(result["state"]["tools"]["Git"]["latest_version"], "2.0.0")
        self.assertEqual(result["unknown"], {"Git": "unavailable"})

    def test_invalid_state_and_empty_audit_fail_closed(self):
        with self.assertRaises(SentinelError):
            evaluate(None, {}, {}, "2026-08-29T00:00:00+00:00")
        with self.assertRaises(SentinelError):
            read_state(Path(__file__).resolve())

    def test_supported_allowlist_reuses_public_sources(self):
        self.assertEqual(set(REMOTE_VERSION_SOURCES), {"Codex CLI", "OpenCodex", "Node.js", "npm", "Git", "Wrangler", "Bun"})

    def test_generated_state_has_no_secret_material(self):
        result = evaluate(None, observed(**{"Codex CLI": "1.0.0"}), {}, "2026-08-29T00:00:00+00:00")
        serialized = json.dumps(result["state"])
        self.assertNotIn("TOKEN", serialized.upper())
        self.assertNotIn("SECRET", serialized.upper())

    def test_telegram_wording_never_claims_installed_version(self):
        message = telegram_result([{"name": "Node.js", "from": "24.19.0", "to": "24.20.0"}])
        self.assertIn("upstream only", message)
        self.assertIn("does not report installed workstation versions", message)
        self.assertNotIn("installed version is", message.lower())

    def test_snapshot_enriches_fresh_stale_partial_and_ignores_invalid(self):
        changes = [{"name": "Node.js", "from": "24.19.0", "to": "24.20.0"}, {"name": "Git", "from": "2.0.0", "to": "2.1.0"}]
        snapshot = {"measured_at": "2026-08-27T08:03:00+00:00", "tools": {"Node.js": {"installed_version": "24.19.0"}}}
        text = telegram_result_with_snapshot(changes, snapshot, __import__("datetime").datetime(2026, 8, 29, tzinfo=__import__("datetime").timezone.utc))
        self.assertIn("Last locally observed: 24.19.0", text); self.assertIn("1 day ago", text); self.assertIn("Git: 2.0.0 -> 2.1.0 (upstream only)", text)
        self.assertEqual(read_snapshot(None), None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workstation-snapshot.json"
            path.write_text('{"version": 2}', encoding="utf-8")
            self.assertIsNone(read_snapshot(path))
            for measured_at in ("not-a-date", "2026-08-29T08:03:00", "2026-02-30T08:03:00+00:00"):
                path.write_text(json.dumps({"version": 1, "measured_at": measured_at, "tools": {}}), encoding="utf-8")
                self.assertIsNone(read_snapshot(path))

    def test_snapshot_build_and_secret_safe_omission(self):
        snapshot = build_snapshot({"timestamp": "2026-08-29T08:03:00+00:00", "tools": [{"name": "Codex CLI", "installed_version": "v1.2.3", "health": "HEALTHY"}, {"name": "Bun (System)", "installed_version": "1.2.3"}, {"name": "Bun (OpenCodex bundled)", "installed_version": "9.9.9"}, {"name": "Git", "installed_version": "unknown"}, {"name": "LM Studio", "installed_version": "9.9.9"}]})
        self.assertEqual(snapshot["tools"], {"Codex CLI": {"installed_version": "1.2.3"}, "Bun": {"installed_version": "1.2.3"}})
        self.assertNotIn("TOKEN", json.dumps(snapshot).upper())
        with self.assertRaises(ValueError): build_snapshot({"tools": []})
        self.assertIn("Bun", SNAPSHOT_TOOLS)

    def test_snapshot_publisher_uses_private_secret_stdin_and_retries_safely(self):
        calls = []
        class Result:
            def __init__(self, code, stdout=""): self.returncode, self.stdout = code, stdout
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return Result(1 if len(calls) == 1 else 0)
        self.assertTrue(publish_snapshot({"version": 1, "measured_at": "x", "tools": {}}, runner, repository="example/wup"))
        self.assertEqual(len(calls), 2)
        args, kwargs = calls[-1]
        self.assertEqual(args, ["gh", "secret", "set", "WUP_WORKSTATION_SNAPSHOT", "--repo", "example/wup"])
        self.assertIn('"measured_at":"x"', kwargs["input"])
        self.assertNotIn('"measured_at":"x"', " ".join(args))
        self.assertFalse(publish_snapshot({"version": 1, "measured_at": "x", "tools": {}}, lambda *a, **k: Result(1), repository="example/wup"))

    def test_snapshot_publisher_timeout_is_bounded_and_safe(self):
        def timeout(*_, **kwargs):
            self.assertEqual(kwargs["timeout"], 10)
            raise __import__("subprocess").TimeoutExpired("gh", 10)
        self.assertFalse(publish_snapshot({"version": 1, "measured_at": "x", "tools": {}}, timeout, repository="example/wup"))

    def test_publisher_failure_does_not_change_local_notifier_result_or_state(self):
        report = {"timestamp": "2026-08-29T08:03:00+00:00", "tools": [{"name": "Node.js", "status": "CURRENT", "installed_version": "24.20.0", "latest_version": "24.20.0", "health": "HEALTHY"}]}
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "last-alerted.json"
            previous = os.environ.get("WUP_SNAPSHOT_PUBLISH")
            os.environ["WUP_SNAPSHOT_PUBLISH"] = "1"
            try: self.assertEqual(execute(state_path, audit_runner=lambda: report, snapshot_publisher=lambda _: (_ for _ in ()).throw(__import__("subprocess").TimeoutExpired("gh", 10))), 0)
            finally:
                if previous is None: os.environ.pop("WUP_SNAPSHOT_PUBLISH", None)
                else: os.environ["WUP_SNAPSHOT_PUBLISH"] = previous
            self.assertFalse(state_path.exists())

    def test_resolvers_use_deterministic_mocked_public_sources(self):
        data = {
            REMOTE_VERSION_SOURCES["Codex CLI"]: {"version": "1.0.0"}, REMOTE_VERSION_SOURCES["OpenCodex"]: {"version": "2.0.0"}, REMOTE_VERSION_SOURCES["npm"]: {"version": "3.0.0"}, REMOTE_VERSION_SOURCES["Wrangler"]: {"version": "4.0.0"},
            REMOTE_VERSION_SOURCES["Node.js"]: [{"version": "v24.20.0"}], REMOTE_VERSION_SOURCES["Git"]: {"tag_name": "v2.52.0.windows.1", "html_url": "https://github.com/git-for-windows/git/releases/tag/v2.52.0.windows.1"}, REMOTE_VERSION_SOURCES["Bun"]: {"tag_name": "bun-v1.2.0", "html_url": "https://github.com/oven-sh/bun/releases/tag/bun-v1.2.0"},
        }
        versions, failures = resolve_remote_upstream_versions(NetworkFetcher(mock_data=data))
        self.assertEqual(failures, {})
        self.assertEqual(set(versions), set(REMOTE_VERSION_SOURCES))
        self.assertEqual(versions["Node.js"]["latest_version"], "24.20.0")
