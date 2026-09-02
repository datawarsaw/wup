"""Append-only local history for meaningful WUP tool observations.

This module is deliberately separate from alert deduplication and remote
sentinel state.  Callers provide existing audit observations; no probes,
network requests, notifications, or configuration mutation occur here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from wup_config import load_config


FINGERPRINT_FIELDS = ("installed_version", "latest_version", "status", "health")
_PERSISTED_FIELDS = ("name", *FINGERPRINT_FIELDS)


class HistoryError(RuntimeError):
    """The local history could not be read or appended safely."""


def utc_now() -> str:
    """Return a stable ISO-8601 UTC timestamp for production callers."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_history_path(config: Mapping[str, Any] | None = None) -> Path:
    """Use WUP's existing local state-directory convention without mutating it."""
    config = config or load_config()
    configured = config.get("local", {}).get("state_dir") if isinstance(config.get("local"), Mapping) else None
    state_root = configured or os.environ.get("WUP_STATE_DIR")
    if not state_root:
        state_root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WUP"
    return Path(state_root) / "change-history.jsonl"


def normalize_observations(tools: Iterable[Any]) -> list[dict[str, Any]]:
    """Keep only stable, approved observation fields in canonical name order."""
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        source = tool if isinstance(tool, Mapping) else vars(tool)
        name = source.get("name")
        if not isinstance(name, str) or not name:
            raise HistoryError("each observation requires a non-empty name")
        normalized.append({field: source.get(field) for field in _PERSISTED_FIELDS})
    normalized.sort(key=lambda item: (item["name"].casefold(), item["name"]))
    names = [item["name"] for item in normalized]
    if len(names) != len(set(names)):
        raise HistoryError("observation names must be unique")
    return normalized


def _read_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise HistoryError(f"could not read history: {exc}") from exc

    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            position = "trailing" if index == len(lines) else "non-trailing"
            raise HistoryError(f"malformed {position} history line {index}; history was not changed") from exc
        if not isinstance(record, dict):
            raise HistoryError(f"history line {index} is not a JSON object; history was not changed")
        records.append(record)
    return records


def _latest_snapshot(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]] | None:
    if not records:
        return None
    latest = records[-1].get("current")
    if not isinstance(latest, Mapping) or not isinstance(latest.get("tools"), list):
        raise HistoryError("latest history record has no valid current snapshot; history was not changed")
    return normalize_observations(latest["tools"])


def _changed_fields(previous: Sequence[Mapping[str, Any]], current: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    old = {item["name"]: item for item in previous}
    new = {item["name"]: item for item in current}
    changes: list[dict[str, Any]] = []
    for name in sorted(set(old) | set(new), key=lambda value: (value.casefold(), value)):
        before, after = old.get(name), new.get(name)
        fields = list(FINGERPRINT_FIELDS) if before is None or after is None else [field for field in FINGERPRINT_FIELDS if before[field] != after[field]]
        if fields:
            changes.append({"fields": fields, "tool": name})
    return changes


def _append(path: Path, record: Mapping[str, Any], opener: Callable[..., Any] = open) -> None:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with opener(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
    except OSError as exc:
        raise HistoryError(f"could not append history: {exc}") from exc


def record_observations(
    tools: Iterable[Any],
    history_path: Path | str | None = None,
    *,
    timestamp: str | None = None,
    clock: Callable[[], str] = utc_now,
    opener: Callable[..., Any] = open,
) -> dict[str, Any]:
    """Append one baseline/change record only when the fingerprint changes."""
    path = Path(history_path) if history_path is not None else default_history_path()
    current = normalize_observations(tools)
    records = _read_history(path)
    previous = _latest_snapshot(records)
    recorded_at = timestamp if timestamp is not None else clock()

    if previous is None:
        record = {"current": {"tools": current}, "timestamp": recorded_at, "type": "baseline"}
        _append(path, record, opener)
        return {"action": "baseline", "changes": [], "path": path, "record": record}

    changes = _changed_fields(previous, current)
    if not changes:
        return {"action": "unchanged", "changes": [], "path": path, "record": None}

    record = {
        "changed_fields": changes,
        "current": {"tools": current},
        "previous": {"tools": previous},
        "timestamp": recorded_at,
        "type": "change",
    }
    _append(path, record, opener)
    return {"action": "changed", "changes": changes, "path": path, "record": record}


def _load_report(path: Path | None) -> Mapping[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8") if path else sys.stdin.read()
        report = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoryError(f"could not load audit report: {exc}") from exc
    if not isinstance(report, Mapping) or not isinstance(report.get("tools"), list):
        raise HistoryError("audit report must be a JSON object containing tools")
    return report


def _validated_snapshot(value: Any, record_index: int, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"tools"} or not isinstance(value.get("tools"), list):
        raise HistoryError(f"history record {record_index} has no valid {field} snapshot")

    for tool in value["tools"]:
        if not isinstance(tool, Mapping) or set(tool) != set(_PERSISTED_FIELDS):
            raise HistoryError(f"history record {record_index} has an invalid {field} tool snapshot")

    return normalize_observations(value["tools"])


def _safe_history_record(record: Mapping[str, Any], record_index: int) -> dict[str, Any]:
    record_type = record.get("type")
    timestamp = record.get("timestamp")

    if not isinstance(timestamp, str) or record_type not in {"baseline", "change"}:
        raise HistoryError(f"history record {record_index} has an invalid type or timestamp")

    if record_type == "baseline":
        if set(record) != {"type", "timestamp", "current"}:
            raise HistoryError(f"history record {record_index} has unexpected baseline fields")
        current = _validated_snapshot(record["current"], record_index, "current")
        return {"type": "baseline", "timestamp": timestamp, "tools": current}

    if set(record) != {"type", "timestamp", "previous", "current", "changed_fields"}:
        raise HistoryError(f"history record {record_index} has unexpected change fields")
    previous = _validated_snapshot(record["previous"], record_index, "previous")
    current = _validated_snapshot(record["current"], record_index, "current")
    previous_by_name = {tool["name"]: tool for tool in previous}
    current_by_name = {tool["name"]: tool for tool in current}
    changes = record.get("changed_fields")
    if not isinstance(changes, list):
        raise HistoryError(f"history record {record_index} has invalid changed fields")

    safe_changes: list[dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, Mapping) or set(change) != {"tool", "fields"}:
            raise HistoryError(f"history record {record_index} has invalid changed fields")
        tool = change.get("tool")
        fields = change.get("fields")
        if not isinstance(tool, str) or not tool or not isinstance(fields, list) or not fields or any(field not in FINGERPRINT_FIELDS for field in fields):
            raise HistoryError(f"history record {record_index} has invalid changed fields")
        if tool not in previous_by_name or tool not in current_by_name:
            raise HistoryError(f"history record {record_index} references an unknown changed tool")
        safe_changes.append(
            {
                "tool": tool,
                "fields": {
                    field: {"previous": previous_by_name[tool].get(field), "current": current_by_name[tool].get(field)}
                    for field in fields
                },
            }
        )

    return {"type": "change", "timestamp": timestamp, "changes": safe_changes}


def view_history(history_path: Path | str | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Read and safely summarize existing history without creating or changing it."""
    if limit is not None and limit <= 0:
        raise ValueError("limit must be a positive integer")

    path = Path(history_path) if history_path is not None else default_history_path()
    records = _read_history(path)
    safe_records = [_safe_history_record(record, index) for index, record in enumerate(records, start=1)]
    return safe_records[-limit:] if limit is not None else safe_records


def format_history_text(records: Sequence[Mapping[str, Any]]) -> str:
    if not records:
        return "WUP History\nNo history available."

    lines = ["WUP History"]
    for record in records:
        if record["type"] == "baseline":
            lines.append(f"BASELINE {record['timestamp']}")
            for tool in record["tools"]:
                lines.append(f"  {tool['name']}")
                for field in _PERSISTED_FIELDS[1:]:
                    lines.append(f"    {field}: {_text_history_value(tool.get(field))}")
        else:
            lines.append(f"CHANGE {record['timestamp']}")
            for change in record["changes"]:
                lines.append(f"  {change['tool']}")
                for field, values in change["fields"].items():
                    previous = _text_history_value(values.get("previous"))
                    current = _text_history_value(values.get("current"))
                    lines.append(f"    {field}: {previous} -> {current}")
    return "\n".join(lines)


def _text_history_value(value: Any) -> str:
    return "unknown" if value is None else str(value)


def format_history_json(records: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps({"count": len(records), "records": list(records)}, sort_keys=True)


def _positive_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if limit <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return limit


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record meaningful WUP audit changes in local JSONL history.")
    parser.add_argument("--input-report", type=Path, help="existing WUP JSON audit report; stdin when omitted")
    parser.add_argument("--history-path", type=Path, help="local JSONL path; defaults to WUP local state directory")
    parser.add_argument("--view", action="store_true", help="read and summarize local history without changing it")
    parser.add_argument("--json", action="store_true", help="render --view output as deterministic JSON")
    parser.add_argument("--limit", type=_positive_limit, help="with --view, show the most recent N records in chronological order")
    args = parser.parse_args(argv)

    if args.view:
        if args.input_report is not None:
            parser.error("--input-report cannot be used with --view")
        try:
            records = view_history(args.history_path, limit=args.limit)
        except (HistoryError, ValueError):
            print("HISTORY_VIEW_ERROR: history could not be read safely.", file=sys.stderr)
            return 1
        print(format_history_json(records) if args.json else format_history_text(records))
        return 0

    if args.json or args.limit is not None:
        parser.error("--json and --limit require --view")

    try:
        outcome = record_observations(_load_report(args.input_report)["tools"], args.history_path)
    except HistoryError as exc:
        print(f"HISTORY_ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"action": outcome["action"], "changes": outcome["changes"], "path": str(outcome["path"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
