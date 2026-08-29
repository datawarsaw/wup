#!/usr/bin/env python3
"""Observe public upstream tool releases for the GitHub Actions remote sentinel."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping

from check_toolchain import NetworkFetcher, REMOTE_VERSION_SOURCES, resolve_remote_upstream_versions

STATE_VERSION = 1


class SentinelError(RuntimeError):
    pass


def read_state(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SentinelError(f"invalid remote state: {exc}") from exc
    if not isinstance(value, dict) or value.get("version") != STATE_VERSION or not isinstance(value.get("tools"), dict):
        raise SentinelError("invalid remote state schema")
    for name, tool in value["tools"].items():
        if not isinstance(name, str) or not isinstance(tool, dict) or not isinstance(tool.get("latest_version"), str):
            raise SentinelError("invalid remote state tool entry")
    return value


def evaluate(previous: Mapping[str, Any] | None, observed: Mapping[str, Mapping[str, str]], failures: Mapping[str, str], now: str) -> Dict[str, Any]:
    if not observed:
        raise SentinelError("complete remote audit produced no trustworthy results")
    previous_tools = previous.get("tools", {}) if previous else {}
    tools = {name: dict(value) for name, value in previous_tools.items()}
    changes = []
    state_changed = previous is None
    for name, current in observed.items():
        prior = previous_tools.get(name)
        if not prior:
            tools[name] = dict(current)
            state_changed = True
        elif prior["latest_version"] != current["latest_version"]:
            changes.append({"name": name, "from": prior["latest_version"], "to": current["latest_version"]})
            tools[name] = dict(current)
            state_changed = True
    state = {"version": STATE_VERSION, "observed_at": now, "tools": tools}
    status = "BASELINE_CREATED" if previous is None else ("CHANGED" if changes else "NO_CHANGE")
    return {"status": status, "changes": changes, "unknown": dict(failures), "state_changed": state_changed, "state": state}


def write_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix="remote-version-state-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def telegram_result(changes: list[Mapping[str, str]]) -> str:
    return telegram_result_with_snapshot(changes, None, dt.datetime.now(dt.timezone.utc))


def read_snapshot(path: Path | None) -> Dict[str, Any] | None:
    if path is None or not path.exists(): return None
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return None
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("measured_at"), str) or not isinstance(value.get("tools"), dict): return None
    try:
        measured = dt.datetime.fromisoformat(value["measured_at"].replace("Z", "+00:00"))
        if measured.tzinfo is None or measured.utcoffset() is None: return None
    except ValueError: return None
    valid = {}
    for name, item in value["tools"].items():
        version = item.get("installed_version") if isinstance(item, dict) else None
        if name in REMOTE_VERSION_SOURCES and isinstance(version, str) and version and all(part.isdigit() for part in version.split(".")):
            valid[name] = {"installed_version": version}
    return {"measured_at": value["measured_at"], "tools": valid}


def _age(measured_at: str, now: dt.datetime) -> str:
    try: then = dt.datetime.fromisoformat(measured_at.replace("Z", "+00:00")); days = max(0, (now - then).days)
    except (TypeError, ValueError): return "age unknown"
    return "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago"


def telegram_result_with_snapshot(changes: list[Mapping[str, str]], snapshot: Mapping[str, Any] | None, now: dt.datetime) -> str:
    if not snapshot:
        lines = ["Toolchain releases (upstream only)"]
        lines.extend(f"{item['name']}: {item['from']} -> {item['to']}" for item in changes)
        lines.append("This does not report installed workstation versions.")
        return "\n".join(lines)
    lines = ["Toolchain releases"]
    for item in changes:
        local = snapshot["tools"].get(item["name"])
        if not local:
            lines.append(f"{item['name']}: {item['from']} -> {item['to']} (upstream only)")
        else:
            lines.extend([item["name"], f"Latest upstream: {item['to']}", f"Last locally observed: {local['installed_version']}", f"Measured: {snapshot['measured_at']} ({_age(snapshot['measured_at'], now)})"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--snapshot-path", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    previous = read_state(args.state_path)
    observed, failures = resolve_remote_upstream_versions(NetworkFetcher())
    result = evaluate(previous, observed, failures, dt.datetime.now(dt.timezone.utc).isoformat())
    if result["state_changed"]:
        write_state(args.state_path, result["state"])
    public = {key: value for key, value in result.items() if key != "state"}
    print(json.dumps(public, sort_keys=True) if args.json else f"{result['status']}: {len(result['changes'])} upstream version change(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
