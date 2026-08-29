#!/usr/bin/env python3
"""Run the read-only toolchain audit and deliver changed findings to Telegram and email."""
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence

ACTIONABLE = ("URGENT", "UPDATE", "WATCH")
IGNORED = ("CURRENT", "UNKNOWN")
SIGNATURE_FIELDS = ("name", "status", "installed_version", "latest_version", "health")
PROJECT, TASK = "Agent Platform", "Toolchain Update Watch"
SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT_SCRIPT = SCRIPT_DIR / "check_toolchain.py"
NOTIFY_SCRIPT = SCRIPT_DIR.parent.parent / "telegram-notify" / "scripts" / "notify.mjs"
EMAIL_HELPER = Path(os.environ.get("WORKSTATION_OPS_EMAIL_HELPER", r"C:\AI\workstation-ops-mcp\dist\cloudflare-email-cli.js"))
RunProcess = Callable[..., subprocess.CompletedProcess[str]]

class AuditError(RuntimeError): pass

def default_state_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WhiteGull" / "toolchain-update-watch" / "last-alerted.json"

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

def changed_findings(findings: Sequence[Mapping[str, Any]], previous: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(item) for item in findings if previous.get(str(item["name"])) != signature(item)]

def format_telegram_result(findings: Sequence[Mapping[str, Any]]) -> str:
    groups = []
    for status in ACTIONABLE:
        items = [f"{item['name']} {item.get('installed_version') or '?'}->{item.get('latest_version') or '?'} [{item.get('health') or '?'}]" for item in findings if item["status"] == status]
        if items: groups.append(f"{status}: " + ", ".join(items))
    return " | ".join(groups)

def format_email(findings: Sequence[Mapping[str, Any]], today: date | None = None) -> tuple[str, str, str]:
    highest = next((status for status in ACTIONABLE if any(item["status"] == status for item in findings)), "UPDATE")
    subject = f"Toolchain Update Watch  {highest}  {(today or date.today()).isoformat()}"
    text_lines, html_parts = ["Toolchain Update Watch", "", "Only new or materially changed actionable findings are listed."], ["<h1>Toolchain Update Watch</h1><p>Only new or materially changed actionable findings are listed.</p>"]
    for status in ACTIONABLE:
        members = [item for item in findings if item["status"] == status]
        if not members: continue
        text_lines.extend(["", status]); html_parts.extend([f"<h2>{status}</h2><ul>"])
        for item in members:
            action = item.get("proposed_action")
            text_lines.append(f"- {item['name']}: installed {item.get('installed_version') or '?'}; latest {item.get('latest_version') or '?'}; health {item.get('health') or '?'}" + (f"; proposed action: {action}" if action else ""))
            html_parts.append(f"<li><strong>{html.escape(str(item['name']))}</strong>: installed {html.escape(str(item.get('installed_version') or '?'))}; latest {html.escape(str(item.get('latest_version') or '?'))}; health {html.escape(str(item.get('health') or '?'))}" + (f"<br>Proposed action: <code>{html.escape(str(action))}</code>" if action else "") + "</li>")
        html_parts.append("</ul>")
    return subject, "\n".join(text_lines), "".join(html_parts)

def telegram_notify(result: str, state: str = "COMPLETE", run_process: RunProcess = subprocess.run) -> bool:
    return run_process(["node", str(NOTIFY_SCRIPT), "--project", PROJECT, "--task", TASK, "--state", state, "--result", result], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False).returncode == 0

def email_notify(subject: str, text: str, html_body: str, run_process: RunProcess = subprocess.run) -> bool:
    return run_process(["node", str(EMAIL_HELPER), "send", "--subject", subject, "--text", text, "--html", html_body], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False).returncode == 0

def write_state(path: Path, last_alerted: Sequence[Mapping[str, Any]], pending: Mapping[str, Mapping[str, Any]] | None = None) -> None:
    payload: Dict[str, Any] = {"version": 2, "last_alerted": {str(item["name"]): signature(item) for item in last_alerted}}
    if pending: payload["pending"] = {str(name): dict(value) for name, value in pending.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix="last-alerted-", suffix=".tmp", dir=path.parent); temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream: json.dump(payload, stream, indent=2, sort_keys=True); stream.write("\n")
        os.replace(temporary, path)
    finally: temporary.unlink(missing_ok=True)

def execute(state_path: Path, dry_run: bool = False, audit_runner: Callable[[], Mapping[str, Any]] = run_audit, telegram_sender: Callable[[str], bool] = lambda result: telegram_notify(result), email_sender: Callable[[str, str, str], bool] = email_notify, failed_telegram_sender: Callable[[str], bool] = lambda result: telegram_notify(result, "FAILED")) -> int:
    try: findings, state = actionable_findings(audit_runner()), read_state(state_path); changed = changed_findings(findings, state["last_alerted"])
    except Exception as exc:
        message = f"Toolchain audit failed: {exc}"
        if not dry_run: failed_telegram_sender(message)
        print(f"FAILED: {message}", file=sys.stderr); return 1
    if not changed: print("NO_CHANGE: no new or materially changed actionable findings"); return 0
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
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--dry-run", action="store_true", help="audit and render payloads without notification or state writes"); parser.add_argument("--state-path", type=Path, default=default_state_path(), help=argparse.SUPPRESS); args = parser.parse_args()
    return execute(args.state_path, dry_run=args.dry_run)

if __name__ == "__main__": raise SystemExit(main())
