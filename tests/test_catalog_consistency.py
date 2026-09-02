#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_toolchain import (
    LOCAL_PROBE_HANDLERS,
    MONITORED_TOOL_CATALOG,
    REMOTE_TOOL_PROVIDER_REGISTRY,
    MonitoredToolEntry,
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


if __name__ == "__main__":
    unittest.main()

