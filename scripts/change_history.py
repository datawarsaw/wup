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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record meaningful WUP audit changes in local JSONL history.")
    parser.add_argument("--input-report", type=Path, help="existing WUP JSON audit report; stdin when omitted")
    parser.add_argument("--history-path", type=Path, help="local JSONL path; defaults to WUP local state directory")
    args = parser.parse_args(argv)
    try:
        outcome = record_observations(_load_report(args.input_report)["tools"], args.history_path)
    except HistoryError as exc:
        print(f"HISTORY_ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"action": outcome["action"], "changes": outcome["changes"], "path": str(outcome["path"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
