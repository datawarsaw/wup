"""Sanitized local installed-version snapshot and best-effort GitHub publisher."""
from __future__ import annotations

import datetime as dt
import json
import re
import os
import subprocess
from typing import Any, Callable, Mapping

SNAPSHOT_VERSION = 1
SNAPSHOT_TOOLS = ("Codex CLI", "OpenCodex", "Node.js", "npm", "Git", "Wrangler", "Bun")
LOCAL_TO_SNAPSHOT = {**{name: name for name in SNAPSHOT_TOOLS if name != "Bun"}, "Bun (System)": "Bun"}
VERSION = re.compile(r"^v?(\d+(?:\.\d+){1,3})$")
GH_TIMEOUT_SECONDS = 10
RunProcess = Callable[..., subprocess.CompletedProcess[str]]


def normalized_version(value: Any) -> str | None:
    match = VERSION.match(str(value or "").strip())
    return match.group(1) if match else None


def build_snapshot(report: Mapping[str, Any]) -> dict[str, Any]:
    measured_at = report.get("timestamp")
    tools = report.get("tools")
    if not isinstance(measured_at, str) or not isinstance(tools, list):
        raise ValueError("audit report cannot produce a workstation snapshot")
    if not measured_at:
        raise ValueError("audit report timestamp is missing")
    snapshot_tools: dict[str, dict[str, str]] = {}
    for item in tools:
        if not isinstance(item, Mapping) or item.get("name") not in LOCAL_TO_SNAPSHOT:
            continue
        version = normalized_version(item.get("installed_version"))
        if version:
            snapshot_tools[LOCAL_TO_SNAPSHOT[str(item["name"])]] = {"installed_version": version}
    return {"version": SNAPSHOT_VERSION, "measured_at": measured_at, "tools": snapshot_tools}


def publish_snapshot(snapshot: Mapping[str, Any], run_process: RunProcess = subprocess.run, attempts: int = 2, repository: str | None = None) -> bool:
    """Best-effort private snapshot publication through a GitHub Actions secret."""
    content = json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n"
    repository = repository or os.environ.get("WUP_REMOTE_REPOSITORY", "")
    if not repository or "/" not in repository:
        return False
    for _ in range(attempts):
        try:
            result = run_process(["gh", "secret", "set", "WUP_WORKSTATION_SNAPSHOT", "--repo", repository], input=content, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=GH_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            continue
        if result.returncode == 0:
            return True
    return False
