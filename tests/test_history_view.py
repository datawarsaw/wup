from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from change_history import HistoryError, format_history_json, main, record_observations, view_history


def tool(name="Codex CLI", installed="1.0", latest="1.0", status="CURRENT", health="HEALTHY"):
    return {"name": name, "installed_version": installed, "latest_version": latest, "status": status, "health": health}


class HistoryViewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state" / "change-history.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def record(self, observed, timestamp):
        return record_observations(observed, self.path, timestamp=timestamp)

    def test_missing_history_is_empty_and_not_created(self):
        self.assertEqual([], view_history(self.path))
        self.assertFalse(self.path.exists())
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, main(["--view", "--history-path", str(self.path)]))
        self.assertEqual("WUP History\nNo history available.\n", output.getvalue())
        self.assertFalse(self.path.exists())

    def test_empty_history_is_safe_and_read_only(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"")
        original = self.path.read_bytes()
        self.assertEqual([], view_history(self.path))
        self.assertEqual(original, self.path.read_bytes())

    def test_baseline_and_change_text_are_distinct(self):
        self.record([tool()], "2026-09-01T00:00:00Z")
        self.record([tool(status="UPDATE")], "2026-09-02T00:00:00Z")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, main(["--view", "--history-path", str(self.path)]))
        rendered = output.getvalue()
        self.assertIn("BASELINE 2026-09-01T00:00:00Z", rendered)
        self.assertIn("installed_version: 1.0", rendered)
        self.assertIn("CHANGE 2026-09-02T00:00:00Z", rendered)
        self.assertIn("status: CURRENT -> UPDATE", rendered)

    def test_baseline_json_exposes_validated_tool_snapshot(self):
        self.record([tool(installed="1.0", latest="1.1")], "2026-09-01T00:00:00Z")
        record = view_history(self.path)[0]
        self.assertEqual("baseline", record["type"])
        self.assertEqual(
            {"name": "Codex CLI", "installed_version": "1.0", "latest_version": "1.1", "status": "CURRENT", "health": "HEALTHY"},
            record["tools"][0],
        )

    def test_change_json_exposes_previous_and_current_values(self):
        self.record([tool(installed="1.0", status="CURRENT")], "2026-09-01T00:00:00Z")
        self.record([tool(installed="1.1", status="UPDATE")], "2026-09-02T00:00:00Z")
        change = view_history(self.path)[1]
        fields = change["changes"][0]["fields"]
        self.assertEqual({"previous": "1.0", "current": "1.1"}, fields["installed_version"])
        self.assertEqual({"previous": "CURRENT", "current": "UPDATE"}, fields["status"])
        self.assertNotIn("latest_version", fields)

    def test_records_and_limit_stay_chronological(self):
        self.record([tool()], "2026-09-01T00:00:00Z")
        self.record([tool(status="UPDATE")], "2026-09-02T00:00:00Z")
        self.record([tool(status="UPDATE", health="DEGRADED")], "2026-09-03T00:00:00Z")
        self.assertEqual(["2026-09-02T00:00:00Z", "2026-09-03T00:00:00Z"], [record["timestamp"] for record in view_history(self.path, limit=2)])

    def test_json_is_safe_and_deterministic(self):
        self.record([tool()], "2026-09-01T00:00:00Z")
        records = view_history(self.path)
        rendered = format_history_json(records)
        self.assertEqual(rendered, format_history_json(records))
        parsed = json.loads(rendered)
        self.assertEqual(1, parsed["count"])
        self.assertIn("installed_version", rendered)

    def test_malformed_history_fails_closed_and_preserves_bytes(self):
        secret = b'SECRET_TOKEN_ABC123'
        self.path.parent.mkdir()
        self.path.write_bytes(b'{"type":"baseline"}\n' + secret)
        original = self.path.read_bytes()
        error = io.StringIO()
        with redirect_stderr(error):
            self.assertEqual(1, main(["--view", "--history-path", str(self.path), "--json"]))
        self.assertEqual(original, self.path.read_bytes())
        self.assertNotIn("SECRET_TOKEN_ABC123", error.getvalue())

    def test_unknown_payload_fails_without_rendering_it(self):
        secret = "SECRET_TOKEN_ABC123"
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"type": "baseline", "timestamp": "2026-09-01T00:00:00Z", "current": {"tools": []}, "unexpected": secret}) + "\n", encoding="utf-8")
        error = io.StringIO()
        with redirect_stderr(error):
            self.assertEqual(1, main(["--view", "--history-path", str(self.path)]))
        self.assertNotIn(secret, error.getvalue())

    def test_existing_history_bytes_are_unchanged_by_view(self):
        self.record([tool()], "2026-09-01T00:00:00Z")
        original = self.path.read_bytes()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(["--view", "--history-path", str(self.path), "--limit", "1", "--json"]))
        self.assertEqual(original, self.path.read_bytes())

    def test_invalid_limits_are_usage_errors(self):
        for value in ("0", "-1", "invalid"):
            with self.assertRaises(SystemExit) as raised, redirect_stderr(io.StringIO()):
                main(["--view", "--limit", value])
            self.assertEqual(2, raised.exception.code)

    def test_view_never_calls_append_logic(self):
        with patch("change_history.record_observations", side_effect=AssertionError("append called")):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["--view", "--history-path", str(self.path)]))


if __name__ == "__main__":
    unittest.main()
