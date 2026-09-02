from __future__ import annotations

import copy
import html
import io
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from status_report import load_report_data, main, render_status_html, write_status_report
from status_snapshot import build_status_snapshot


class StatusReportTest(unittest.TestCase):
    def report(self):
        return {
            "audit_report_timestamp": "2026-09-02T12:00:00Z",
            "tools": [
                {
                    "tool_name": "Codex CLI",
                    "installed_version": "1.2.3",
                    "latest_version": "1.2.4",
                    "status": "UPDATE",
                    "health": "HEALTHY",
                    "local_observed_at": None,
                    "remote_observed_at": None,
                    "installed_version_provenance": "LOCAL",
                    "latest_version_provenance": "REMOTE",
                    "release_or_docs_url": "https://example.test/release",
                }
            ],
        }

    def test_complete_report_shows_truthful_fields_and_safe_link(self):
        rendered = render_status_html(self.report())
        for value in ("Codex CLI", "1.2.3", "1.2.4", "UPDATE", "HEALTHY", "LOCAL", "REMOTE", "2026-09-02T12:00:00Z"):
            self.assertIn(value, rendered)
        self.assertIn('href="https://example.test/release"', rendered)
        self.assertIn("Local observed</span><code>unknown", rendered)
        self.assertIn("Remote observed</span><code>unknown", rendered)

    def test_missing_values_are_unknown_and_unsafe_urls_are_not_links(self):
        for unsafe_url in ("javascript:alert(1)", "data:text/html,<script>alert(1)</script>"):
            report = {"tools": [{"tool_name": "Tool", "status": "UNKNOWN", "release_or_docs_url": unsafe_url}]}
            rendered = render_status_html(report)
            self.assertIn("unknown", rendered)
            self.assertIn(html.escape(unsafe_url, quote=True), rendered)
            self.assertNotIn("href=\"javascript:", rendered)
            self.assertNotIn("href=\"data:", rendered)
            self.assertIn("not opened", rendered)

    def test_hostile_values_are_escaped_and_diagnostics_not_leaked(self):
        report = self.report()
        report["tools"][0].update({
            "tool_name": '<script>alert(1)</script>',
            "installed_version": '"><img src=x onerror=alert(1)>',
            "status": '<script>BAD</script>',
            "attention_notes": ["SECRET_SENTINEL"],
            "raw_provider_payload": {"token": "RAW_SECRET"},
        })
        rendered = render_status_html(report)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertIn("&quot;&gt;&lt;img src=x onerror=alert(1)&gt;", rendered)
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertNotIn("SECRET_SENTINEL", rendered)
        self.assertNotIn("RAW_SECRET", rendered)

    def test_rendering_is_deterministic_and_does_not_mutate_or_execute(self):
        report = self.report()
        original = copy.deepcopy(report)
        with (
            patch("subprocess.run", side_effect=AssertionError("execution")) as run,
            patch("subprocess.Popen", side_effect=AssertionError("execution")) as popen,
            patch("socket.socket", side_effect=AssertionError("network")) as network,
            patch.object(Path, "write_text", side_effect=AssertionError("write")) as write_text,
            patch.object(Path, "write_bytes", side_effect=AssertionError("write")) as write_bytes,
        ):
            first = render_status_html(report)
            second = render_status_html(report)
        self.assertEqual(first, second)
        self.assertEqual(report, original)
        run.assert_not_called(); popen.assert_not_called(); network.assert_not_called()
        write_text.assert_not_called(); write_bytes.assert_not_called()

    def test_snapshot_object_is_accepted_without_reinterpreting_timestamp(self):
        snapshot = build_status_snapshot({
            "timestamp": "2026-09-02T12:00:00Z",
            "tools": [{"name": "Git", "installed_version": "2.0", "latest_version": "2.1", "status": "CURRENT", "health": "HEALTHY"}],
        })
        rendered = render_status_html(snapshot)
        self.assertIn("2026-09-02T12:00:00Z", rendered)
        self.assertIn("Local observed</span><code>unknown", rendered)
        self.assertNotIn("None", rendered)

    def test_cli_valid_audit_file_to_requested_output_path(self):
        audit_data = {
            "timestamp": "2026-09-02T14:30:00Z",
            "tools": [
                {
                    "name": "Wrangler",
                    "installed_version": "4.30.0",
                    "latest_version": "4.32.0",
                    "status": "WATCH",
                    "health": "HEALTHY",
                    "release_url": "https://example.test/wrangler",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_file = tmp / "audit.json"
            input_file.write_text(json.dumps(audit_data), encoding="utf-8")
            output_file = tmp / "nested" / "wup-status.html"

            exit_code = main(["--input-report", str(input_file), "--output", str(output_file)])
            self.assertEqual(0, exit_code)
            self.assertTrue(output_file.exists())

            content = output_file.read_text(encoding="utf-8")
            self.assertIn("Wrangler", content)
            self.assertIn("4.30.0", content)
            self.assertIn("4.32.0", content)
            self.assertIn("WATCH", content)
            self.assertIn("HEALTHY", content)
            self.assertIn("2026-09-02T14:30:00Z", content)
            self.assertIn("https://example.test/wrangler", content)
            self.assertIn("LOCAL", content)
            self.assertIn("REMOTE", content)
            self.assertIn("WUP · Local status", content)

            # Exactly requested output path written, no unrelated/sibling files in parent
            self.assertEqual(["wup-status.html"], [f.name for f in output_file.parent.iterdir()])

    def test_cli_stdin_pipe_generates_report(self):
        audit_data = {
            "timestamp": "2026-09-02T15:00:00Z",
            "tools": [
                {
                    "name": "Git",
                    "installed_version": "2.51.0",
                    "latest_version": "2.52.0",
                    "status": "CURRENT",
                    "health": "HEALTHY",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "stdin-report.html"
            stdin_mock = io.StringIO(json.dumps(audit_data))
            with patch.object(sys, "stdin", stdin_mock):
                with patch.object(stdin_mock, "isatty", return_value=False):
                    exit_code = main(["--output", str(output_file)])

            self.assertEqual(0, exit_code)
            self.assertTrue(output_file.exists())
            content = output_file.read_text(encoding="utf-8")
            self.assertIn("Git", content)
            self.assertIn("2.51.0", content)
            self.assertIn("CURRENT", content)

    def test_cli_malformed_json_fails_closed_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_file = tmp / "bad.json"
            input_file.write_text("{malformed: json...", encoding="utf-8")
            output_file = tmp / "status.html"

            stderr_buf = io.StringIO()
            with patch("sys.stderr", stderr_buf):
                exit_code = main(["--input-report", str(input_file), "--output", str(output_file)])

            self.assertEqual(1, exit_code)
            self.assertFalse(output_file.exists())
            self.assertIn("REPORT_INPUT_ERROR", stderr_buf.getvalue())

    def test_cli_structurally_invalid_report_fails_closed(self):
        invalid_payloads = [
            "[]",
            '{"timestamp": "2026-09-02"}',
            '{"tools": "not-a-list"}',
            '{"tools": ["not-a-dict"]}',
            "12345",
        ]
        for invalid in invalid_payloads:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                input_file = tmp / "invalid.json"
                input_file.write_text(invalid, encoding="utf-8")
                output_file = tmp / "status.html"

                stderr_buf = io.StringIO()
                with patch("sys.stderr", stderr_buf):
                    exit_code = main(["--input-report", str(input_file), "--output", str(output_file)])

                self.assertEqual(1, exit_code)
                self.assertFalse(output_file.exists())
                self.assertIn("REPORT_INPUT_ERROR", stderr_buf.getvalue())

    def test_cli_missing_input_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "status.html"
            missing_input = Path(tmpdir) / "does_not_exist.json"

            stderr_buf = io.StringIO()
            with patch("sys.stderr", stderr_buf):
                exit_code = main(["--input-report", str(missing_input), "--output", str(output_file)])

            self.assertEqual(1, exit_code)
            self.assertFalse(output_file.exists())

    def test_cli_tty_without_input_report_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "status.html"
            with patch("sys.stdin.isatty", return_value=True):
                stderr_buf = io.StringIO()
                with patch("sys.stderr", stderr_buf):
                    exit_code = main(["--output", str(output_file)])
                self.assertEqual(1, exit_code)
                self.assertFalse(output_file.exists())
                self.assertIn("provide --input-report <path> or pipe JSON", stderr_buf.getvalue())

    def test_cli_output_is_directory_fails_closed(self):
        audit_data = {"tools": [{"name": "Tool", "status": "UNKNOWN"}]}
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_file = tmp / "audit.json"
            input_file.write_text(json.dumps(audit_data), encoding="utf-8")
            dir_target = tmp / "existing_dir"
            dir_target.mkdir()

            stderr_buf = io.StringIO()
            with patch("sys.stderr", stderr_buf):
                exit_code = main(["--input-report", str(input_file), "--output", str(dir_target)])
            self.assertEqual(1, exit_code)
            self.assertIn("REPORT_WRITE_ERROR", stderr_buf.getvalue())

    def test_cli_input_report_not_mutated_and_no_execution(self):
        audit_data = {
            "timestamp": "2026-09-02T12:00:00Z",
            "tools": [{"name": "Codex CLI", "status": "CURRENT", "installed_version": "0.150.1"}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_file = tmp / "audit.json"
            original_bytes = json.dumps(audit_data, indent=2).encode("utf-8")
            input_file.write_bytes(original_bytes)
            output_file = tmp / "output.html"

            with (
                patch("subprocess.run", side_effect=AssertionError("subprocess")) as mock_sub,
                patch("subprocess.Popen", side_effect=AssertionError("popen")) as mock_popen,
                patch("socket.socket", side_effect=AssertionError("socket")) as mock_socket,
            ):
                exit_code = main(["--input-report", str(input_file), "--output", str(output_file)])

            self.assertEqual(0, exit_code)
            self.assertEqual(original_bytes, input_file.read_bytes())
            mock_sub.assert_not_called()
            mock_popen.assert_not_called()
            mock_socket.assert_not_called()

    def test_cli_subprocess_invocation_end_to_end(self):
        audit_data = {
            "timestamp": "2026-09-02T12:00:00Z",
            "tools": [{"name": "OpenCodex", "status": "CURRENT", "installed_version": "2.35.0"}],
        }
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "status_report.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_file = tmp / "audit.json"
            input_file.write_text(json.dumps(audit_data), encoding="utf-8")
            output_file = tmp / "out.html"

            res = subprocess.run(
                [sys.executable, str(script_path), "--input-report", str(input_file), "--output", str(output_file)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, res.returncode)
            self.assertTrue(output_file.exists())
            self.assertIn("OpenCodex", output_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
