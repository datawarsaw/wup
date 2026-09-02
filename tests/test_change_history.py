from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from change_history import HistoryError, default_history_path, record_observations


def tools(*items: dict) -> list[dict]:
    return list(items)


def tool(name="Codex CLI", installed="1.0", latest="1.0", status="CURRENT", health="HEALTHY", **extra):
    return {"name": name, "installed_version": installed, "latest_version": latest, "status": status, "health": health, **extra}


class ChangeHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state" / "change-history.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def read(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def record(self, observed, timestamp="2026-09-02T00:00:00Z"):
        return record_observations(observed, self.path, timestamp=timestamp)

    def test_first_observation_is_an_explicit_baseline(self):
        result = self.record(tools(tool()))
        self.assertEqual("baseline", result["action"])
        self.assertEqual(["baseline"], [record["type"] for record in self.read()])

    def test_identical_observations_do_not_append_noise(self):
        self.record(tools(tool()))
        self.assertEqual("unchanged", self.record(tools(tool()), "2026-09-03T00:00:00Z")["action"])
        self.assertEqual(1, len(self.read()))

    def test_installed_version_change_records_one_change(self):
        self.record(tools(tool()))
        result = self.record(tools(tool(installed="1.1")))
        self.assertEqual([{"fields": ["installed_version"], "tool": "Codex CLI"}], result["changes"])

    def test_latest_version_change_records_one_change(self):
        self.record(tools(tool()))
        self.assertEqual(["latest_version"], self.record(tools(tool(latest="1.1")))["changes"][0]["fields"])

    def test_status_change_records_one_change(self):
        self.record(tools(tool()))
        self.assertEqual(["status"], self.record(tools(tool(status="UPDATE")))["changes"][0]["fields"])

    def test_health_change_records_one_change(self):
        self.record(tools(tool()))
        self.assertEqual(["health"], self.record(tools(tool(health="DEGRADED")))["changes"][0]["fields"])

    def test_observation_order_does_not_append_noise(self):
        self.record(tools(tool("Zulu"), tool("Alpha")))
        self.assertEqual("unchanged", self.record(tools(tool("Alpha"), tool("Zulu")))["action"])

    def test_unrelated_and_secret_fields_are_not_persisted(self):
        self.record(tools(tool(token="top-secret", attention_notes=["ignore"], release_url="https://example.test")))
        data = self.path.read_text(encoding="utf-8")
        self.assertNotIn("top-secret", data)
        self.assertNotIn("attention_notes", data)
        self.assertNotIn("release_url", data)

    def test_multiple_changes_are_deterministically_sorted(self):
        self.record(tools(tool("Zulu"), tool("Alpha")))
        result = self.record(tools(tool("Alpha", status="UPDATE"), tool("Zulu", health="DEGRADED")))
        self.assertEqual(["Alpha", "Zulu"], [item["tool"] for item in result["changes"]])

    def test_missing_and_empty_history_create_baseline(self):
        self.assertEqual("baseline", self.record(tools(tool()))["action"])
        self.path.write_text("", encoding="utf-8")
        self.assertEqual("baseline", self.record(tools(tool()))["action"])

    def test_malformed_final_line_fails_without_rewriting_history(self):
        self.record(tools(tool()))
        original = self.path.read_bytes() + b'{"partial"'
        self.path.write_bytes(original)
        with self.assertRaises(HistoryError):
            self.record(tools(tool(status="UPDATE")))
        self.assertEqual(original, self.path.read_bytes())

    def test_append_preserves_existing_history_bytes(self):
        self.record(tools(tool()))
        original = self.path.read_bytes()
        self.record(tools(tool(status="UPDATE")))
        self.assertTrue(self.path.read_bytes().startswith(original))

    def test_write_failure_is_explicit_and_preserves_existing_history(self):
        self.record(tools(tool()))
        original = self.path.read_bytes()
        def denied(*_args, **_kwargs):
            raise OSError("denied")

        with self.assertRaises(HistoryError):
            record_observations(tools(tool(status="UPDATE")), self.path, timestamp="2026-09-03T00:00:00Z", opener=denied)
        self.assertEqual(original, self.path.read_bytes())

    def test_clock_is_injectable(self):
        result = record_observations(tools(tool()), self.path, clock=lambda: "2030-01-01T00:00:00Z")
        self.assertEqual("2030-01-01T00:00:00Z", result["record"]["timestamp"])

    def test_each_history_line_is_individually_parseable(self):
        self.record(tools(tool()))
        self.record(tools(tool(status="UPDATE")))
        self.assertEqual(2, len(self.read()))

    def test_default_path_uses_configured_local_state_directory(self):
        self.assertEqual(Path("custom-state") / "change-history.jsonl", default_history_path({"local": {"state_dir": "custom-state"}}))


if __name__ == "__main__":
    unittest.main()
