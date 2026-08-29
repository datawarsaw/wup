#!/usr/bin/env python3
"""Run the read-only toolchain audit and deliver changed findings to Telegram and email."""
from __future__ import annotations

import argparse
import html
import json
import os
import shlex
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence
from workstation_snapshot import build_snapshot, publish_snapshot
from wup_config import apply_runtime_config, load_config

ACTIONABLE = ("URGENT", "UPDATE", "WATCH")
IGNORED = ("CURRENT", "UNKNOWN")
SIGNATURE_FIELDS = ("name", "status", "installed_version", "latest_version", "health")
HEALTH_SEVERITY = {"HEALTHY": 0, "UNVERIFIED": 1, "DEGRADED": 2, "NOT_INSTALLED": 3}
PROJECT, TASK = "WUP", "Toolchain Update Watch"
SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT_SCRIPT = SCRIPT_DIR / "check_toolchain.py"
NOTIFY_SCRIPT = SCRIPT_DIR / "telegram_notify.py"
RunProcess = Callable[..., subprocess.CompletedProcess[str]]

class AuditError(RuntimeError): pass

def default_state_path() -> Path:
    root = os.environ.get("WUP_STATE_DIR") or (Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WUP")
    return Path(root) / "last-alerted.json"

def run_audit(run_process: RunProcess = subprocess.run) -> Mapping[str, Any]:
    process = run_process([sys.executable, str(AUDIT_SCRIPT), "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if process.returncode != 0: raise AuditError(f"audit exited {process.returncode}")
    try: report = json.loads(process.stdout.lstrip("\ufeff"))
    except (json.JSONDecodeError, TypeError) as exc: raise AuditError("audit emitted invalid JSON") from exc
    if not isinstance(report, dict) or not isinstance(report.get("tools"), list): raise AuditError("audit JSON is missing the tools array")
    return report

def signature(tool: Mapping[str, Any]) -> Dict[str, Any]: return {field: tool.get(field) for field in SIGNATURE_FIELDS}

def actionable_findings(report: Mapping[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for raw in report["tools"]:
        if not isinstance(raw, dict): raise AuditError("audit tools array contains a non-object entry")
        if raw.get("status") in ACTIONABLE:
            if not isinstance(raw.get("name"), str) or not raw["name"]: raise AuditError("actionable audit finding is missing a tool name")
            item = signature(raw)
            release_url = raw.get("release_url")
            if isinstance(release_url, str) and release_url.startswith("https://"):
                item["release_url"] = release_url
            recommendation = raw.get("update_recommendation")
            if isinstance(recommendation, dict) and isinstance(recommendation.get("proposed_command"), str): item["proposed_action"] = recommendation["proposed_command"]
            findings.append(item)
        elif raw.get("status") not in IGNORED: raise AuditError(f"audit finding has unsupported status: {raw.get('status')!r}")
    return findings

def read_state(path: Path) -> Dict[str, Any]:
    if not path.exists(): return {"version": 2, "last_alerted": {}, "pending": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("last_alerted"), dict) or not isinstance(data.get("pending", {}), dict): raise TypeError
        return {"version": 2, "last_alerted": data["last_alerted"], "pending": data.get("pending", {})}
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc: raise RuntimeError(f"cannot read deduplication state: {path}") from exc

def _health_worsened(previous: Any, current: Any) -> bool:
    if previous == current:
        return False
    previous_severity = HEALTH_SEVERITY.get(str(previous))
    current_severity = HEALTH_SEVERITY.get(str(current))
    if previous_severity is None or current_severity is None:
        return True
    return current_severity > previous_severity


def _health_improved_only(item: Mapping[str, Any], previous: Mapping[str, Any]) -> bool:
    if any(item.get(field) != previous.get(field) for field in ("name", "status", "installed_version", "latest_version")):
        return False
    previous_severity = HEALTH_SEVERITY.get(str(previous.get("health")))
    current_severity = HEALTH_SEVERITY.get(str(item.get("health")))
    return previous_severity is not None and current_severity is not None and current_severity < previous_severity


def finding_changed(item: Mapping[str, Any], previous: Mapping[str, Any] | None) -> bool:
    if previous is None:
        return True
    if any(item.get(field) != previous.get(field) for field in ("name", "status", "installed_version", "latest_version")):
        return True
    return _health_worsened(previous.get("health"), item.get("health"))


def changed_findings(findings: Sequence[Mapping[str, Any]], previous: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(item) for item in findings if finding_changed(item, previous.get(str(item["name"])))]


def has_silent_health_refresh(findings: Sequence[Mapping[str, Any]], previous: Mapping[str, Mapping[str, Any]]) -> bool:
    return any(_health_improved_only(item, prior) for item in findings if (prior := previous.get(str(item["name"]))) is not None)

def format_telegram_result(findings: Sequence[Mapping[str, Any]]) -> str:
    groups = []
    for status in ACTIONABLE:
        items = [f"{item['name']} {item.get('installed_version') or '?'}->{item.get('latest_version') or '?'} [{item.get('health') or '?'}]" for item in findings if item["status"] == status]
        if items: groups.append(f"{status}: " + ", ".join(items))
    return " | ".join(groups)

def _email_text(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "?"))

def _severity_summary(findings: Sequence[Mapping[str, Any]]) -> str:
    parts = [f"{sum(1 for item in findings if item['status'] == status)} {status}" for status in ACTIONABLE if any(item['status'] == status for item in findings)]
    return " | ".join(parts)

def _severity_style(status: str) -> tuple[str, str, str]:
    styles = {
        "URGENT": ("#8a1c1c", "#fff1f1", "#d8a1a1"),
        "UPDATE": ("#7a4b00", "#fff7e6", "#e3c58a"),
        "WATCH": ("#1f4f78", "#edf6ff", "#a9c8e3"),
    }
    return styles[status]

def _email_card(item: Mapping[str, Any]) -> str:
    status = str(item["status"])
    accent, tint, border = _severity_style(status)
    name = _email_text(item["name"])
    installed = _email_text(item.get("installed_version"))
    latest = _email_text(item.get("latest_version"))
    health = _email_text(item.get("health"))
    metric_style = "padding:0 12px 0 0;font-family:Arial,Helvetica,sans-serif;vertical-align:top;"
    html_parts = [
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 12px 0;border:1px solid #d9dfdf;border-radius:6px;background:#ffffff;">',
        '<tr><td style="padding:18px 18px 16px 18px;">',
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:17px;line-height:22px;font-weight:700;overflow-wrap:anywhere;color:#172321;">{name}</div>',
        f'<div style="margin-top:7px;font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:16px;font-weight:700;letter-spacing:0.7px;color:{accent};"><span style="display:inline-block;padding:3px 7px;border:1px solid {border};border-radius:3px;background:{tint};">{status}</span></div>',
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:14px;table-layout:fixed;"><tr>',
        f'<td width="33%" style="{metric_style}"><div style="font-family:Arial,Helvetica,sans-serif;font-size:10px;line-height:14px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;color:#61706d;">Installed</div><div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:20px;color:#172321;">{installed}</div></td>',
        f'<td width="33%" style="{metric_style}"><div style="font-family:Arial,Helvetica,sans-serif;font-size:10px;line-height:14px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;color:#61706d;">Latest</div><div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:20px;color:#172321;">{latest}</div></td>',
        f'<td width="34%" style="font-family:Arial,Helvetica,sans-serif;vertical-align:top;"><div style="font-family:Arial,Helvetica,sans-serif;font-size:10px;line-height:14px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;color:#61706d;">Health</div><div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:20px;color:#172321;">{health}</div></td>',
        '</tr></table>',
    ]
    release_url = item.get("release_url")
    if release_url:
        safe_url = html.escape(str(release_url), quote=True)
        html_parts.extend([
            '<div style="margin-top:16px;font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:16px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;color:#61706d;">What changed</div>',
            f'<div style="margin-top:6px;"><a href="{safe_url}" style="display:inline-block;padding:8px 11px;border:1px solid #2d625b;border-radius:3px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:18px;font-weight:700;color:#174b44;text-decoration:none;">Open authoritative release notes</a></div>',
        ])
    action = item.get("proposed_action")
    if action:
        html_parts.extend([
            '<div style="margin-top:16px;font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:16px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;color:#61706d;">Proposed action</div>',
            f'<pre style="margin:6px 0 0 0;padding:10px 12px;overflow-wrap:anywhere;white-space:pre-wrap;border:1px solid #d9dfdf;border-radius:3px;background:#f4f6f5;font-family:Consolas,\'Courier New\',monospace;font-size:12px;line-height:18px;color:#172321;">{html.escape(str(action))}</pre>',
        ])
    html_parts.extend(['</td></tr></table>'])
    return "".join(html_parts)

def format_email(findings: Sequence[Mapping[str, Any]], today: date | None = None) -> tuple[str, str, str]:
    highest = next((status for status in ACTIONABLE if any(item["status"] == status for item in findings)), "UPDATE")
    audit_date = (today or date.today()).isoformat()
    subject = f"WUP Toolchain Update Watch  {highest}  {audit_date}"
    summary = _severity_summary(findings)
    text_lines = ["WUP Toolchain Update Watch", f"Audit date: {audit_date}", f"{len(findings)} actionable finding(s): {summary}", "", "Only new or materially changed actionable findings are listed."]
    html_parts = [
        '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body style="margin:0;padding:0;background:#eef1f0;">',
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background:#eef1f0;"><tr><td align="center" style="padding:24px 12px;">',
        '<table role="presentation" width="640" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:640px;table-layout:fixed;border:1px solid #d9dfdf;background:#ffffff;">',
        '<tr><td style="padding:24px 24px 20px 24px;border-bottom:1px solid #d9dfdf;background:#f7f8f7;">',
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:10px;line-height:14px;font-weight:700;letter-spacing:1.2px;color:#4f625e;">WUP</div>',
        '<div style="margin-top:7px;font-family:Arial,Helvetica,sans-serif;font-size:26px;line-height:32px;font-weight:700;color:#172321;">WUP Toolchain Update Watch</div>',
        f'<div style="margin-top:6px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:19px;color:#61706d;">Audit date: {audit_date}</div>',
        '</td></tr>',
        '<tr><td style="padding:18px 24px;border-bottom:1px solid #d9dfdf;">',
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:22px;font-weight:700;color:#172321;">{len(findings)} actionable finding(s)</div>',
        f'<div style="margin-top:3px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:19px;color:#61706d;">{summary}</div>',
        '</td></tr>',
        '<tr><td style="padding:24px;">',
    ]
    for status in ACTIONABLE:
        members = [item for item in findings if item["status"] == status]
        if not members: continue
        accent, tint, border = _severity_style(status)
        text_lines.extend(["", status])
        html_parts.extend([
            f'<div style="margin:{"0" if status == ACTIONABLE[0] else "20px"} 0 10px 0;padding:8px 10px;border-left:4px solid {accent};background:{tint};font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:18px;font-weight:700;letter-spacing:0.7px;color:{accent};">{status} &nbsp; {len(members)} finding(s)</div>',
        ])
        for item in members:
            action = item.get("proposed_action")
            release_url = item.get("release_url")
            text_lines.extend([f"- {item['name']}", f"  Installed: {item.get('installed_version') or '?'}", f"  Latest: {item.get('latest_version') or '?'}", f"  Health: {item.get('health') or '?'}"])
            if release_url:
                text_lines.extend(["", "  What changed:", f"  {release_url}"])
            if action:
                text_lines.extend(["", "  Proposed action:", f"  {action}"])
            html_parts.append(_email_card(item))
    html_parts.extend([
        '</td></tr>',
        '<tr><td style="padding:16px 24px;border-top:1px solid #d9dfdf;background:#f7f8f7;font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:17px;color:#61706d;">WUP<br>Automated read-only workstation audit</td></tr>',
        '</table></td></tr></table></body></html>',
    ])
    return subject, "\n".join(text_lines), "".join(html_parts)

def telegram_notify(result: str, state: str = "COMPLETE", run_process: RunProcess = subprocess.run) -> bool:
    if os.environ.get("WUP_TELEGRAM_ENABLED") != "1": return True
    message = f"{TASK} ({state})\n{result}"
    return run_process([sys.executable, str(NOTIFY_SCRIPT), "--message", message], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False).returncode == 0

def email_notify(subject: str, text: str, html_body: str, run_process: RunProcess = subprocess.run) -> bool:
    command = os.environ.get("WUP_EMAIL_COMMAND", "").strip()
    if not command: return True
    return run_process([*shlex.split(command, posix=os.name != "nt"), "send", "--subject", subject, "--text", text, "--html", html_body], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False).returncode == 0

def write_state(path: Path, last_alerted: Sequence[Mapping[str, Any]], pending: Mapping[str, Mapping[str, Any]] | None = None) -> None:
    payload: Dict[str, Any] = {"version": 2, "last_alerted": {str(item["name"]): signature(item) for item in last_alerted}}
    if pending: payload["pending"] = {str(name): dict(value) for name, value in pending.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix="last-alerted-", suffix=".tmp", dir=path.parent); temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream: json.dump(payload, stream, indent=2, sort_keys=True); stream.write("\n")
        os.replace(temporary, path)
    finally: temporary.unlink(missing_ok=True)

def execute(state_path: Path, dry_run: bool = False, audit_runner: Callable[[], Mapping[str, Any]] = run_audit, telegram_sender: Callable[[str], bool] = lambda result: telegram_notify(result), email_sender: Callable[[str, str, str], bool] = email_notify, failed_telegram_sender: Callable[[str], bool] = lambda result: telegram_notify(result, "FAILED"), snapshot_publisher: Callable[[Mapping[str, Any]], bool] = publish_snapshot) -> int:
    try: report = audit_runner(); findings, state = actionable_findings(report), read_state(state_path); changed = changed_findings(findings, state["last_alerted"])
    except Exception as exc:
        message = f"Toolchain audit failed: {exc}"
        if not dry_run: failed_telegram_sender(message)
        print(f"FAILED: {message}", file=sys.stderr); return 1
    if os.environ.get("WUP_SNAPSHOT_PUBLISH") == "1" and not dry_run:
        try:
            if not snapshot_publisher(build_snapshot(report)): print("SNAPSHOT_PUBLISH: unavailable", file=sys.stderr)
        except Exception: print("SNAPSHOT_PUBLISH: unavailable", file=sys.stderr)
    if not changed:
        if not dry_run and not state["pending"] and has_silent_health_refresh(findings, state["last_alerted"]):
            write_state(state_path, findings)
            print("NO_CHANGE: health-only improvements refreshed the local baseline")
        else:
            print("NO_CHANGE: no new or materially changed actionable findings")
        return 0
    telegram_result = format_telegram_result(changed); subject, text, html_body = format_email(changed)
    if dry_run:
        print(f"DRY_RUN_TELEGRAM: {telegram_result}"); print(f"DRY_RUN_EMAIL_SUBJECT: {subject}"); print(f"DRY_RUN_EMAIL_BODY:\n{text}"); return 0
    pending = state["pending"]; telegram_pending = pending.get("telegram_delivered", {}) if isinstance(pending, dict) else {}
    telegram_needed = [item for item in changed if telegram_pending.get(item["name"]) != signature(item)]
    if telegram_needed and not telegram_sender(format_telegram_result(telegram_needed)): print("FAILED: Telegram notification was not delivered", file=sys.stderr); return 1
    delivered_telegram = {**telegram_pending, **{str(item["name"]): signature(item) for item in telegram_needed}}
    if not email_sender(subject, text, html_body):
        write_state(state_path, [{"name": name, **value} for name, value in state["last_alerted"].items()], {"telegram_delivered": delivered_telegram})
        print("FAILED: Cloudflare email notification was not delivered; prior good alert state was preserved", file=sys.stderr); return 1
    write_state(state_path, findings); print(f"ALERTED: {telegram_result}"); return 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--dry-run", action="store_true", help="audit and render payloads without notification or state writes"); parser.add_argument("--config", type=Path, help="optional WUP TOML configuration"); parser.add_argument("--state-path", type=Path, default=None, help=argparse.SUPPRESS); args = parser.parse_args()
    config = load_config(args.config); apply_runtime_config(config)
    if config["local"].get("state_dir"): os.environ["WUP_STATE_DIR"] = str(config["local"]["state_dir"])
    state_path = args.state_path or default_state_path()
    return execute(state_path, dry_run=args.dry_run)

if __name__ == "__main__": raise SystemExit(main())
