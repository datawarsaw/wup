#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from update_plan import (
    UpdatePlan,
    UpdatePlanEntry,
    UpdatePlanSummary,
    build_update_plan,
    format_text_plan,
)


class TestUpdatePlan(unittest.TestCase):
    def test_case_1_tool_current(self) -> None:
        """Case 1: When tool is current, update_needed is False and status is CURRENT."""
        tool = {
            "name": "Git",
            "installed_version": "2.45.1",
            "latest_version": "2.45.1",
            "status": "CURRENT",
            "release_url": "https://github.com/git-for-windows/git/releases",
        }
        plan = build_update_plan([tool])
        self.assertEqual(len(plan.entries), 1)
        entry = plan.entries[0]
        self.assertEqual(entry.tool, "Git")
        self.assertEqual(entry.installed_version, "2.45.1")
        self.assertEqual(entry.latest_version, "2.45.1")
        self.assertFalse(entry.update_needed)
        self.assertEqual(entry.status, "CURRENT")
        self.assertEqual(entry.reason, "up to date")
        self.assertEqual(plan.summary.current, 1)
        self.assertEqual(plan.summary.updates_available, 0)

    def test_case_2_tool_update_available(self) -> None:
        """Case 2: When tool has update available, update_needed is True."""
        tool = {
            "name": "OpenCodex",
            "installed_version": "2.33.0",
            "latest_version": "2.35.0",
            "status": "UPDATE",
            "update_recommendation": {
                "why": "Newer stable release 2.35.0 available (current: 2.33.0)",
                "proposed_command": "npm install -g @bitkyc08/opencodex@2.35.0",
            },
            "release_url": "https://www.npmjs.com/package/@bitkyc08/opencodex/v/2.35.0",
        }
        plan = build_update_plan([tool])
        self.assertEqual(len(plan.entries), 1)
        entry = plan.entries[0]
        self.assertEqual(entry.tool, "OpenCodex")
        self.assertTrue(entry.update_needed)
        self.assertEqual(entry.status, "UPDATE")
        self.assertEqual(entry.reason, "Newer stable release 2.35.0 available (current: 2.33.0)")
        # Verify no executable command is present in entry
        d = entry.to_dict()
        self.assertNotIn("proposed_command", d)
        self.assertNotIn("rollback_command", d)
        self.assertEqual(plan.summary.updates_available, 1)

    def test_case_3_mixed_plan_summary_counts(self) -> None:
        """Case 3: Mixed tool statuses produce accurate summary totals."""
        tools = [
            {"name": "T1", "installed_version": "1.0", "latest_version": "1.0", "status": "CURRENT"},
            {"name": "T2", "installed_version": "1.0", "latest_version": "2.0", "status": "UPDATE"},
            {"name": "T3", "installed_version": "1.0", "latest_version": "3.0", "status": "URGENT"},
            {"name": "T4", "installed_version": "1.0", "latest_version": "1.1", "status": "WATCH"},
            {"name": "T5", "installed_version": None, "latest_version": None, "status": "UNKNOWN"},
        ]
        plan = build_update_plan({"tools": tools})
        self.assertEqual(plan.summary.total_tools, 5)
        self.assertEqual(plan.summary.current, 1)
        self.assertEqual(plan.summary.updates_available, 2)  # UPDATE + URGENT
        self.assertEqual(plan.summary.watch, 1)
        self.assertEqual(plan.summary.unknown, 1)

    def test_case_4_unknown_latest_version(self) -> None:
        """Case 4: Tool with unknown latest version does not claim update_needed."""
        tool = {
            "name": "CustomTool",
            "installed_version": "1.0.0",
            "latest_version": None,
            "status": "UNKNOWN",
            "attention_notes": ["release lookup failed (offline)"],
        }
        plan = build_update_plan([tool])
        entry = plan.entries[0]
        self.assertFalse(entry.update_needed)
        self.assertEqual(entry.status, "UNKNOWN")
        self.assertEqual(entry.reason, "release lookup failed (offline)")

    def test_case_5_not_installed_or_unobserved_local(self) -> None:
        """Case 5: Not installed tool preserves status and does not claim update_needed."""
        tool = {
            "name": "Bun",
            "installed_version": None,
            "latest_version": "1.4.0",
            "status": "UNKNOWN",
            "health": "NOT_INSTALLED",
            "attention_notes": ["binary not found in PATH"],
        }
        plan = build_update_plan([tool])
        entry = plan.entries[0]
        self.assertFalse(entry.update_needed)
        self.assertEqual(entry.status, "UNKNOWN")
        self.assertEqual(entry.reason, "binary not found in PATH")

    def test_case_6_unsupported_unparseable_comparison_state(self) -> None:
        """Case 6: Unsupported comparison state surfaces existing reason/status without guessing."""
        tool = {
            "name": "UnparseableTool",
            "installed_version": "git-commit-abc",
            "latest_version": "git-commit-xyz",
            "status": "WATCH",
            "attention_notes": ["semver comparison unsupported for commit hash"],
        }
        plan = build_update_plan([tool])
        entry = plan.entries[0]
        self.assertFalse(entry.update_needed)
        self.assertEqual(entry.status, "WATCH")
        self.assertEqual(entry.reason, "semver comparison unsupported for commit hash")

    def test_case_7_release_or_docs_url_preserved(self) -> None:
        """Case 7: release_url is preserved in entry when present."""
        tool = {
            "name": "Node.js",
            "installed_version": "22.0.0",
            "latest_version": "22.1.0",
            "status": "WATCH",
            "release_url": "https://nodejs.org/en/blog/release/v22.1.0",
        }
        plan = build_update_plan([tool])
        self.assertEqual(plan.entries[0].release_or_docs_url, "https://nodejs.org/en/blog/release/v22.1.0")

    def test_case_8_no_secret_or_unrelated_fields_in_json(self) -> None:
        """Case 8: Output dictionary does not leak internal/command fields or secrets."""
        tool = {
            "name": "SafeTool",
            "installed_version": "1.0",
            "latest_version": "2.0",
            "status": "UPDATE",
            "update_recommendation": {
                "why": "newer version",
                "proposed_command": "npm install -g safe-tool@2.0",
            },
            "internal_secret": "DO_NOT_LEAK_TOKEN",
        }
        plan = build_update_plan([tool])
        json_str = json.dumps(plan.to_dict())
        self.assertNotIn("DO_NOT_LEAK_TOKEN", json_str)
        self.assertNotIn("proposed_command", json_str)
        self.assertNotIn("npm install", json_str)

    def test_case_9_input_data_not_mutated(self) -> None:
        """Case 9: build_update_plan does not mutate input report or tools."""
        original_input = {
            "tools": [
                {"name": "ToolA", "installed_version": "1.0", "latest_version": "2.0", "status": "UPDATE"},
            ]
        }
        input_copy = copy.deepcopy(original_input)
        build_update_plan(input_copy)
        self.assertEqual(original_input, input_copy)

    def test_case_10_no_subprocess_or_network_execution(self) -> None:
        """Case 10: Step 11/13 proof: zero subprocess or network calls during plan generation."""
        with (
            patch("subprocess.run") as mock_sub,
            patch("subprocess.Popen") as mock_popen,
            patch("socket.socket") as mock_socket,
        ):
            tools = [{"name": "ToolX", "installed_version": "1.0", "latest_version": "2.0", "status": "UPDATE"}]
            plan = build_update_plan(tools)
            _ = format_text_plan(plan)
            self.assertEqual(len(plan.entries), 1)
            mock_sub.assert_not_called()
            mock_popen.assert_not_called()
            mock_socket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
