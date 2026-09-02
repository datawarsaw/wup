#!/usr/bin/env python3
"""Deterministic, read-only update PLAN representation for WUP."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class UpdatePlanEntry:
    tool: str
    installed_version: Optional[str]
    latest_version: Optional[str]
    update_needed: bool
    status: str
    reason: Optional[str] = None
    release_or_docs_url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        if self.reason is None:
            data.pop("reason", None)
        if self.release_or_docs_url is None:
            data.pop("release_or_docs_url", None)
        return data


@dataclass(frozen=True)
class UpdatePlanSummary:
    total_tools: int
    updates_available: int
    current: int
    watch: int
    unknown: int

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class UpdatePlan:
    entries: List[UpdatePlanEntry]
    summary: UpdatePlanSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "entries": [entry.to_dict() for entry in self.entries],
        }


def _extract_tool_data(tool: Any) -> tuple[str, Optional[str], Optional[str], str, Optional[str], Optional[str]]:
    if isinstance(tool, Mapping):
        name = str(tool.get("name", ""))
        installed = tool.get("installed_version")
        latest = tool.get("latest_version")
        status = str(tool.get("status", "UNKNOWN"))
        release_url = tool.get("release_url")

        rec = tool.get("update_recommendation")
        if isinstance(rec, Mapping) and rec.get("why"):
            reason = str(rec["why"])
        elif tool.get("attention_notes"):
            notes = tool["attention_notes"]
            reason = "; ".join(str(n) for n in notes) if isinstance(notes, list) else str(notes)
        elif status == "CURRENT":
            reason = "up to date"
        else:
            reason = None

        return name, installed, latest, status, reason, release_url

    name = getattr(tool, "name", "")
    installed = getattr(tool, "installed_version", None)
    latest = getattr(tool, "latest_version", None)
    status = getattr(tool, "status", "UNKNOWN")
    release_url = getattr(tool, "release_url", None)

    rec = getattr(tool, "update_recommendation", None)
    why = getattr(rec, "why", None) if rec else None
    notes = getattr(tool, "attention_notes", [])

    if why:
        reason = str(why)
    elif notes:
        reason = "; ".join(str(n) for n in notes)
    elif status == "CURRENT":
        reason = "up to date"
    else:
        reason = None

    return name, installed, latest, status, reason, release_url


def build_update_plan(
    report_or_results: Any,
) -> UpdatePlan:
    """Pure transformation of toolchain audit results into a descriptive UpdatePlan."""
    raw_tools: Sequence[Any]
    if isinstance(report_or_results, Mapping):
        raw_tools = report_or_results.get("tools", [])
    elif hasattr(report_or_results, "tools"):
        raw_tools = getattr(report_or_results, "tools")
    elif isinstance(report_or_results, (list, tuple)):
        raw_tools = report_or_results
    else:
        raw_tools = []

    entries: List[UpdatePlanEntry] = []
    for raw in raw_tools:
        name, installed, latest, status, reason, release_url = _extract_tool_data(raw)
        update_needed = status in ("UPDATE", "URGENT")
        entries.append(
            UpdatePlanEntry(
                tool=name,
                installed_version=installed,
                latest_version=latest,
                update_needed=update_needed,
                status=status,
                reason=reason,
                release_or_docs_url=release_url,
            )
        )

    summary = UpdatePlanSummary(
        total_tools=len(entries),
        updates_available=sum(1 for e in entries if e.update_needed),
        current=sum(1 for e in entries if e.status == "CURRENT"),
        watch=sum(1 for e in entries if e.status == "WATCH"),
        unknown=sum(1 for e in entries if e.status not in ("CURRENT", "WATCH", "UPDATE", "URGENT")),
    )

    return UpdatePlan(entries=entries, summary=summary)


def format_text_plan(plan: UpdatePlan) -> str:
    lines = [
        "WUP Update Plan (descriptive only)",
        "=" * 40,
        f"Summary: {plan.summary.total_tools} tools monitored | "
        f"{plan.summary.updates_available} updates available | "
        f"{plan.summary.current} current | "
        f"{plan.summary.watch} watch | "
        f"{plan.summary.unknown} unknown/other",
        "-" * 40,
    ]

    for entry in plan.entries:
        inst_str = entry.installed_version or "not installed"
        lat_str = entry.latest_version or "unknown"
        status_tag = f"[{entry.status}]"
        flag = " [UPDATE NEEDED]" if entry.update_needed else ""
        lines.append(f"  {status_tag:<10} {entry.tool}: {inst_str} -> {lat_str}{flag}")
        if entry.reason:
            lines.append(f"             Reason: {entry.reason}")
        if entry.release_or_docs_url:
            lines.append(f"             Info: {entry.release_or_docs_url}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-report", type=Path, help="path to JSON audit report file")
    parser.add_argument("--json", action="store_true", help="output plan as JSON")
    args = parser.parse_args()

    if args.input_report:
        if not args.input_report.is_file():
            print(f"error: input report file not found: {args.input_report}", file=sys.stderr)
            return 1
        content = args.input_report.read_text(encoding="utf-8")
        data = json.loads(content)
    else:
        if sys.stdin.isatty():
            print("error: provide --input-report <path> or pipe JSON audit output to stdin", file=sys.stderr)
            return 1
        content = sys.stdin.read()
        data = json.loads(content)

    plan = build_update_plan(data)

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2))
    else:
        print(format_text_plan(plan))

    return 0


if __name__ == "__main__":
    sys.exit(main())
