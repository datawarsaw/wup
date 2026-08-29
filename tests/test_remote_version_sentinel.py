import sys
import json
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_toolchain import REMOTE_VERSION_SOURCES, NetworkFetcher, resolve_remote_upstream_versions
from remote_version_sentinel import SentinelError, evaluate, read_state, telegram_result


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

    def test_resolvers_use_deterministic_mocked_public_sources(self):
        data = {
            REMOTE_VERSION_SOURCES["Codex CLI"]: {"version": "1.0.0"}, REMOTE_VERSION_SOURCES["OpenCodex"]: {"version": "2.0.0"}, REMOTE_VERSION_SOURCES["npm"]: {"version": "3.0.0"}, REMOTE_VERSION_SOURCES["Wrangler"]: {"version": "4.0.0"},
            REMOTE_VERSION_SOURCES["Node.js"]: [{"version": "v24.20.0"}], REMOTE_VERSION_SOURCES["Git"]: {"tag_name": "v2.52.0.windows.1", "html_url": "https://github.com/git-for-windows/git/releases/tag/v2.52.0.windows.1"}, REMOTE_VERSION_SOURCES["Bun"]: {"tag_name": "bun-v1.2.0", "html_url": "https://github.com/oven-sh/bun/releases/tag/bun-v1.2.0"},
        }
        versions, failures = resolve_remote_upstream_versions(NetworkFetcher(mock_data=data))
        self.assertEqual(failures, {})
        self.assertEqual(set(versions), set(REMOTE_VERSION_SOURCES))
        self.assertEqual(versions["Node.js"]["latest_version"], "24.20.0")
