from __future__ import annotations

import copy
import json
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_toolchain import ToolCheckResult, ToolchainAuditReport
from status_snapshot import (
    LOCAL,
    REMOTE,
    build_status_snapshot,
    format_status_snapshot_json,
    format_status_snapshot_text,
)


class StatusSnapshotTest(unittest.TestCase):
    def complete_report(self):
        return {
            "timestamp": "2026-09-02T12:00:00Z",
            "tools": [{
                "name": "Codex CLI",
                "installed_version": "1.2.3",
                "latest_version": "1.2.4",
                "status": "UPDATE",
                "health": "HEALTHY",
                "release_url": "https://example.test/releases/1.2.4",
            }],
        }

    def test_complete_snapshot_and_provenance(self):
        data = build_status_snapshot(self.complete_report()).to_dict()
        self.assertEqual("2026-09-02T12:00:00Z", data["audit_report_timestamp"])
        self.assertEqual({
            "tool_name": "Codex CLI", "installed_version": "1.2.3", "latest_version": "1.2.4",
            "status": "UPDATE", "health": "HEALTHY", "local_observed_at": None,
            "remote_observed_at": None, "installed_version_provenance": LOCAL,
            "latest_version_provenance": REMOTE, "release_or_docs_url": "https://example.test/releases/1.2.4",
        }, data["tools"][0])

    def test_missing_timestamps_are_null_unknown_and_not_fabricated(self):
        snapshot = build_status_snapshot(self.complete_report())
        tool = snapshot.tools[0]
        self.assertIsNone(tool.local_observed_at)
        self.assertIsNone(tool.remote_observed_at)
        self.assertIn("Local observed: unknown", format_status_snapshot_text(snapshot))
        self.assertIn("Remote observed: unknown", format_status_snapshot_text(snapshot))
        self.assertNotEqual(snapshot.audit_report_timestamp, tool.local_observed_at)
        self.assertNotEqual(snapshot.audit_report_timestamp, tool.remote_observed_at)

    def test_deterministic_primitive_only_serialization(self):
        first = format_status_snapshot_json(build_status_snapshot(self.complete_report()))
        second = format_status_snapshot_json(build_status_snapshot(self.complete_report()))
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first), build_status_snapshot(self.complete_report()).to_dict())

    def test_missing_url_remains_null(self):
        report = {"tools": [{"name": "Tool", "status": "UNKNOWN", "health": "UNVERIFIED"}]}
        self.assertIsNone(build_status_snapshot(report).tools[0].release_or_docs_url)

    def test_existing_audit_report_object_is_supported(self):
        tool = ToolCheckResult(
            name="Wrangler", installed_version="4.0.0", install_method="npm",
            latest_version="4.1.0", latest_source="npm", status="UPDATE", health="HEALTHY",
            release_url="https://example.test/wrangler",
        )
        report = ToolchainAuditReport(
            timestamp="2026-09-02T12:00:00Z", date="2026-09-02", core_stack_health="HEALTHY", tools=[tool],
        )
        snapshot = build_status_snapshot(report)
        self.assertEqual("Wrangler", snapshot.tools[0].tool_name)
        self.assertEqual("https://example.test/wrangler", snapshot.tools[0].release_or_docs_url)

    def test_no_execution_network_or_writes(self):
        report = self.complete_report()
        with (
            patch.object(subprocess, "run", side_effect=AssertionError("executed")) as run,
            patch.object(subprocess, "Popen", side_effect=AssertionError("executed")) as popen,
            patch.object(socket, "socket", side_effect=AssertionError("network")) as network,
            patch.object(Path, "write_text", side_effect=AssertionError("write")) as write_text,
            patch.object(Path, "write_bytes", side_effect=AssertionError("write")) as write_bytes,
            patch("check_toolchain.ToolchainAuditor.audit_all", side_effect=AssertionError("audit rerun")) as audit_all,
        ):
            format_status_snapshot_json(build_status_snapshot(report))
        run.assert_not_called(); popen.assert_not_called(); network.assert_not_called()
        write_text.assert_not_called(); write_bytes.assert_not_called(); audit_all.assert_not_called()

    def test_input_is_not_mutated_and_sensitive_fields_are_not_leaked(self):
        report = self.complete_report()
        report["tools"][0].update({
            "attention_notes": ["SECRET_SENTINEL"],
            "raw_provider_payload": {"token": "RAW_TOKEN_SENTINEL"},
            "environment": {"HOME": "C:/machine/path/sentinel"},
            "update_recommendation": {"proposed_command": "COMMAND_SENTINEL"},
        })
        original = copy.deepcopy(report)
        snapshot = build_status_snapshot(report)
        self.assertEqual(original, report)
        rendered = format_status_snapshot_json(snapshot) + format_status_snapshot_text(snapshot)
        for sentinel in ("SECRET_SENTINEL", "RAW_TOKEN_SENTINEL", "machine/path/sentinel", "COMMAND_SENTINEL"):
            self.assertNotIn(sentinel, rendered)
