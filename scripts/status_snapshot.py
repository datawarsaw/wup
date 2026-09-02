#!/usr/bin/env python3
"""Pure, safe status-snapshot projection of an existing WUP audit report.

The snapshot deliberately records no inferred freshness.  WUP audits currently
have one report-level timestamp, but no independent local or remote
per-tool observation timestamps.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence


LOCAL = "LOCAL"
REMOTE = "REMOTE"


@dataclass(frozen=True)
class ToolStatusSnapshot:
    """Whitelisted, static-report-safe view of one existing tool result."""

    tool_name: str
    installed_version: Optional[str]
    latest_version: Optional[str]
    status: str
    health: str
    local_observed_at: None = None
    remote_observed_at: None = None
    installed_version_provenance: str = LOCAL
    latest_version_provenance: str = REMOTE
    release_or_docs_url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class StatusSnapshot:
    """Immutable snapshot built only from already-produced audit data."""

    audit_report_timestamp: Optional[str]
    tools: tuple[ToolStatusSnapshot, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_report_timestamp": self.audit_report_timestamp,
            "tools": [tool.to_dict() for tool in self.tools],
        }


def _value(source: Any, field: str, default: Any = None) -> Any:
    return source.get(field, default) if isinstance(source, Mapping) else getattr(source, field, default)


def _optional_text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _text(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


def _tools(report_or_results: Any) -> Sequence[Any]:
    if isinstance(report_or_results, Mapping):
        raw_tools = report_or_results.get("tools", ())
    elif hasattr(report_or_results, "tools"):
        raw_tools = getattr(report_or_results, "tools")
    elif isinstance(report_or_results, (list, tuple)):
        raw_tools = report_or_results
    else:
        raw_tools = ()
    return raw_tools if isinstance(raw_tools, (list, tuple)) else ()


def build_status_snapshot(report_or_results: Any) -> StatusSnapshot:
    """Build a deterministic snapshot without probes, I/O, or input mutation."""
    report_timestamp = _optional_text(_value(report_or_results, "timestamp"))
    tools = tuple(
        ToolStatusSnapshot(
            tool_name=_text(_value(raw_tool, "name"), ""),
            installed_version=_optional_text(_value(raw_tool, "installed_version")),
            latest_version=_optional_text(_value(raw_tool, "latest_version")),
            status=_text(_value(raw_tool, "status"), "UNKNOWN"),
            health=_text(_value(raw_tool, "health"), "UNVERIFIED"),
            # Existing WUP report data has no independent per-tool timestamps.
            local_observed_at=None,
            remote_observed_at=None,
            release_or_docs_url=_optional_text(_value(raw_tool, "release_url")),
        )
        for raw_tool in _tools(report_or_results)
    )
    return StatusSnapshot(audit_report_timestamp=report_timestamp, tools=tools)


def format_status_snapshot_json(snapshot: StatusSnapshot) -> str:
    """Return deterministic JSON containing only JSON primitives."""
    return json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":"))


def format_status_snapshot_text(snapshot: StatusSnapshot) -> str:
    """Render a concise display form; absent timestamps are explicitly unknown."""
    lines = ["WUP Status Snapshot", f"Audit report timestamp: {snapshot.audit_report_timestamp or 'unknown'}"]
    for tool in snapshot.tools:
        lines.extend((
            f"{tool.tool_name}: {tool.status} ({tool.health})",
            f"  Installed: {tool.installed_version or 'unknown'} [{tool.installed_version_provenance}]",
            f"  Latest: {tool.latest_version or 'unknown'} [{tool.latest_version_provenance}]",
            f"  Local observed: {tool.local_observed_at or 'unknown'}",
            f"  Remote observed: {tool.remote_observed_at or 'unknown'}",
        ))
        if tool.release_or_docs_url:
            lines.append(f"  Release/docs: {tool.release_or_docs_url}")
    return "\n".join(lines)
