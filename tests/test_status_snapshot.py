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
        self.assertEqual({
            "local_runtime": "UNKNOWN",
            "local_tunnel_client": "UNKNOWN",
            "remote_connector": "UNKNOWN",
            "last_remote_success_at": None,
            "classification": "UNKNOWN",
        }, data["execution_path"])

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
        self.assertEqual("UNKNOWN", snapshot.execution_path.classification)

    def test_no_execution_network_or_writes(self):
        report = self.complete_report()
        report["execution_path"] = {
            "local_runtime": "HEALTHY",
            "local_tunnel_client": "HEALTHY",
            "remote_connector": "HEALTHY",
            "last_remote_success_at": "2026-09-04T09:36:00Z",
        }
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
        report["execution_path"] = {
            "local_runtime": "HEALTHY",
            "local_tunnel_client": "HEALTHY",
            "remote_connector": "HEALTHY",
            "last_remote_success_at": "2026-09-04T09:36:00Z",
            "raw_error": "EXECUTION_SECRET_SENTINEL",
        }
        original = copy.deepcopy(report)
        snapshot = build_status_snapshot(report)
        self.assertEqual(original, report)
        rendered = format_status_snapshot_json(snapshot) + format_status_snapshot_text(snapshot)
        for sentinel in (
            "SECRET_SENTINEL", "RAW_TOKEN_SENTINEL", "machine/path/sentinel",
            "COMMAND_SENTINEL", "EXECUTION_SECRET_SENTINEL",
        ):
            self.assertNotIn(sentinel, rendered)

    def test_execution_path_all_healthy_classifies_healthy(self):
        report = self.complete_report()
        report["execution_path"] = {
            "local_runtime": "HEALTHY",
            "local_tunnel_client": "HEALTHY",
            "remote_connector": "HEALTHY",
            "last_remote_success_at": "2026-09-04T09:36:00Z",
        }
        execution = build_status_snapshot(report).execution_path
        self.assertEqual("HEALTHY", execution.classification)
        self.assertEqual("2026-09-04T09:36:00Z", execution.last_remote_success_at)

    def test_remote_or_tunnel_failure_does_not_become_host_failure(self):
        for failing_field in ("local_tunnel_client", "remote_connector"):
            report = self.complete_report()
            report["execution_path"] = {
                "local_runtime": "HEALTHY",
                "local_tunnel_client": "HEALTHY",
                "remote_connector": "HEALTHY",
            }
            report["execution_path"][failing_field] = "DEGRADED"
            execution = build_status_snapshot(report).execution_path
            self.assertEqual("EXECUTION_PATH_DEGRADED", execution.classification)
            self.assertNotEqual("HOST_RUNTIME_DEGRADED", execution.classification)

    def test_local_runtime_degraded_classifies_host_runtime_degraded(self):
        report = self.complete_report()
        report["execution_path"] = {
            "local_runtime": "DEGRADED",
            "local_tunnel_client": "UNKNOWN",
            "remote_connector": "DEGRADED",
        }
        self.assertEqual(
            "HOST_RUNTIME_DEGRADED",
            build_status_snapshot(report).execution_path.classification,
        )

    def test_invalid_execution_path_values_are_rejected_to_unknown(self):
        report = self.complete_report()
        report["execution_path"] = {
            "local_runtime": "BROKEN",
            "local_tunnel_client": "YES",
            "remote_connector": 200,
            "classification": "HOST_FAILURE",
            "last_remote_success_at": 123,
        }
        execution = build_status_snapshot(report).execution_path
        self.assertEqual("UNKNOWN", execution.local_runtime)
        self.assertEqual("UNKNOWN", execution.local_tunnel_client)
        self.assertEqual("UNKNOWN", execution.remote_connector)
        self.assertEqual("UNKNOWN", execution.classification)
        self.assertIsNone(execution.last_remote_success_at)

    def test_text_surface_includes_execution_path_domains(self):
        report = self.complete_report()
        report["execution_path"] = {
            "local_runtime": "HEALTHY",
            "local_tunnel_client": "DEGRADED",
            "remote_connector": "UNKNOWN",
        }
        rendered = format_status_snapshot_text(build_status_snapshot(report))
        self.assertIn("Execution path:", rendered)
        self.assertIn("Local runtime: HEALTHY", rendered)
        self.assertIn("Local tunnel client: DEGRADED", rendered)
        self.assertIn("Remote connector: UNKNOWN", rendered)
        self.assertIn("Classification: EXECUTION_PATH_DEGRADED", rendered)
