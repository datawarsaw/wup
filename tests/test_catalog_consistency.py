#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_toolchain
from test_check_toolchain import make_mock_runner
from check_toolchain import (
    LOCAL_PROBE_HANDLERS,
    MONITORED_TOOL_CATALOG,
    REMOTE_TOOL_PROVIDER_REGISTRY,
    MonitoredToolEntry,
    NetworkFetcher,
    RemoteToolProvider,
    ToolchainAuditor,
    validate_catalog_consistency,
)


class TestCatalogProbeConsistency(unittest.TestCase):
    def test_production_registries_validate_successfully(self):
        """Case 1: Current production registries validate without error."""
        validate_catalog_consistency()

    def test_duplicate_catalog_tool_key_fails(self):
        """Case 2: Duplicate tool name in catalog raises ValueError."""
        duplicate_catalog = MONITORED_TOOL_CATALOG + (
            MonitoredToolEntry("Git", local_probe="git", remote_provider="Git"),
        )
        with self.assertRaisesRegex(ValueError, "duplicate tool name: Git"):
            validate_catalog_consistency(catalog=duplicate_catalog)

    def test_unknown_local_probe_reference_fails(self):
        """Case 3: Catalog entry referencing an unknown local probe raises ValueError."""
        invalid_catalog = tuple(
            MonitoredToolEntry(entry.name, local_probe="nonexistent_probe", remote_provider=entry.remote_provider)
            if entry.name == "Git"
            else entry
            for entry in MONITORED_TOOL_CATALOG
        )
        with self.assertRaisesRegex(ValueError, "references unknown local probe: nonexistent_probe"):
            validate_catalog_consistency(catalog=invalid_catalog)

    def test_unknown_remote_provider_reference_fails(self):
        """Case 4: Catalog entry referencing an unknown remote provider raises ValueError."""
        invalid_catalog = tuple(
            MonitoredToolEntry(entry.name, local_probe=entry.local_probe, remote_provider="Nonexistent Provider")
            if entry.name == "Git"
            else entry
            for entry in MONITORED_TOOL_CATALOG
        )
        with self.assertRaisesRegex(ValueError, "references unknown remote provider: Nonexistent Provider"):
            validate_catalog_consistency(catalog=invalid_catalog)

    def test_missing_registry_coverage_without_exemption_fails(self):
        """Case 5: Tool missing local probe or remote provider without explicit exemption fails."""
        # Missing remote provider without exemption
        missing_remote = tuple(
            MonitoredToolEntry(entry.name, local_probe=entry.local_probe, remote_provider=None, remote_exempt_reason=None)
            if entry.name == "Git"
            else entry
            for entry in MONITORED_TOOL_CATALOG
        )
        with self.assertRaisesRegex(ValueError, "missing remote provider coverage without an explicit exemption"):
            validate_catalog_consistency(catalog=missing_remote)

        # Missing local probe without exemption
        missing_local = tuple(
            MonitoredToolEntry(entry.name, local_probe=None, local_exempt_reason=None, remote_provider=entry.remote_provider)
            if entry.name == "Git"
            else entry
            for entry in MONITORED_TOOL_CATALOG
        )
        with self.assertRaisesRegex(ValueError, "missing local probe coverage without an explicit exemption"):
            validate_catalog_consistency(catalog=missing_local)

    def test_unknown_registry_entry_not_in_catalog_fails(self):
        """Case 6: Registry entry not associated with any catalog entry fails."""
        # Unlinked remote provider
        extra_remote = REMOTE_TOOL_PROVIDER_REGISTRY + (
            RemoteToolProvider("UnknownTool", "https://example.com", "npm_latest", "https://example.com/{version}", 99),
        )
        with self.assertRaisesRegex(ValueError, "remote provider registry contains provider 'UnknownTool' not associated with any catalog entry"):
            validate_catalog_consistency(remote_registry=extra_remote)

        # Unlinked local probe handler
        extra_local = dict(LOCAL_PROBE_HANDLERS)
        extra_local["unlinked_probe"] = ToolchainAuditor.check_git
        with self.assertRaisesRegex(ValueError, "local probe registry contains probe 'unlinked_probe' not associated with any catalog entry"):
            validate_catalog_consistency(local_handlers=extra_local)

    def test_intentional_exemptions_remain_valid(self):
        """Case 7: Intentional local-only and remote-only exemptions validate cleanly."""
        # Verify all remote-exempt tools have declared, non-empty reasons
        remote_exempt = [entry for entry in MONITORED_TOOL_CATALOG if entry.remote_provider is None]
        self.assertTrue(len(remote_exempt) > 0)
        for entry in remote_exempt:
            self.assertIsNotNone(entry.remote_exempt_reason)
            self.assertTrue(len(entry.remote_exempt_reason.strip()) > 0)

        exempt_names = {entry.name for entry in remote_exempt}
        expected_exempt_names = {
            "Codex Desktop",
            "OpenCodex Proxy & Shims",
            "Workstation Ops / MCP",
            "Python",
            "LM Studio",
        }
        self.assertEqual(exempt_names, expected_exempt_names)


class TestCatalogDrivenOrchestration(unittest.TestCase):
    """The normal audit path must iterate from the authoritative catalog."""

    def run_audit_names(self, catalog):
        auditor = ToolchainAuditor(fetcher=NetworkFetcher(offline=True), command_runner=make_mock_runner())
        with patch.object(check_toolchain, "MONITORED_TOOL_CATALOG", catalog):
            report = auditor.audit_all()
        return [tool.name for tool in report.tools]

    def test_orchestration_names_derive_from_catalog(self):
        # The audit path must invoke exactly the catalog's local probes, in
        # catalog order. Spy on the dispatcher to prove the catalog is the sole
        # orchestration authority rather than a second hard-coded list.
        auditor = ToolchainAuditor(fetcher=NetworkFetcher(offline=True), command_runner=make_mock_runner())
        calls: list[str] = []
        original = ToolchainAuditor._run_catalog_probe

        def spy(self_, probe_key, ocx_cache):
            calls.append(probe_key)
            return original(self_, probe_key, ocx_cache)

        with patch.object(ToolchainAuditor, "_run_catalog_probe", spy):
            auditor.audit_all()

        expected_probes = [entry.local_probe for entry in MONITORED_TOOL_CATALOG if entry.local_probe]
        self.assertEqual(calls, expected_probes)

    def test_orchestration_order_follows_catalog(self):
        reordered = tuple(
            entry for entry in MONITORED_TOOL_CATALOG
            if entry.name == "Git"
        ) + tuple(
            entry for entry in MONITORED_TOOL_CATALOG
            if entry.name != "Git"
        )
        names = self.run_audit_names(reordered)
        self.assertEqual(names[0], "Git")
        self.assertEqual(names[1], "Codex CLI")

    def test_synthetic_catalog_inclusion(self):
        synthetic = (
            MonitoredToolEntry("Codex CLI", local_probe="check_codex_cli", remote_provider="Codex CLI"),
            MonitoredToolEntry("Node.js", local_probe="system_node", remote_provider="Node.js"),
        )
        names = self.run_audit_names(synthetic)
        self.assertEqual(names, ["Codex CLI", "Node.js"])

    def test_synthetic_catalog_exclusion(self):
        synthetic = (
            MonitoredToolEntry("Node.js", local_probe="system_node", remote_provider="Node.js"),
        )
        names = self.run_audit_names(synthetic)
        self.assertEqual(names, ["Node.js"])
        self.assertNotIn("Git", names)
        self.assertNotIn("Bun", names)


if __name__ == "__main__":
    unittest.main()

