#!/usr/bin/env python3
"""Read-only doctor diagnostics baseline for WUP."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR))
import wup_config


@dataclass
class DoctorCheck:
    name: str
    status: str  # "HEALTHY", "DEGRADED", "UNHEALTHY"
    message: str
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        if self.details is None:
            data.pop("details", None)
        return data


@dataclass
class DoctorReport:
    status: str  # "HEALTHY", "DEGRADED", "UNHEALTHY"
    checks: List[DoctorCheck]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
        }


def check_repository(repo_root: Path) -> DoctorCheck:
    required_files = [
        Path("scripts/check_toolchain.py"),
        Path("scripts/run_notifier.py"),
        Path("scripts/wup_config.py"),
    ]
    missing = [str(rel_path) for rel_path in required_files if not (repo_root / rel_path).is_file()]

    if missing:
        return DoctorCheck(
            name="repository",
            status="UNHEALTHY",
            message=f"missing required repository files: {', '.join(missing)}",
            details={"repo_root": str(repo_root), "missing": missing},
        )

    return DoctorCheck(
        name="repository",
        status="HEALTHY",
        message="repository structure is valid",
        details={"repo_root": str(repo_root)},
    )


def check_configuration(config_path: Path) -> DoctorCheck:
    if not config_path.is_file():
        return DoctorCheck(
            name="configuration",
            status="HEALTHY",
            message=f"configuration file not present (using defaults): {config_path.name}",
            details={"config_path": str(config_path), "present": False},
        )

    try:
        config = wup_config.load_config(config_path)
        safe_summary = {
            "config_path": str(config_path),
            "present": True,
            "tools_enabled": len(config.get("tools", {}).get("enabled", [])),
            "telegram_enabled": config.get("notifications", {}).get("telegram", {}).get("enabled", False),
            "email_configured": bool(config.get("notifications", {}).get("email", {}).get("command")),
        }
        return DoctorCheck(
            name="configuration",
            status="HEALTHY",
            message=f"configuration is valid: {config_path.name}",
            details=safe_summary,
        )
    except Exception as exc:
        return DoctorCheck(
            name="configuration",
            status="UNHEALTHY",
            message=f"invalid configuration in {config_path.name}: {exc}",
            details={"config_path": str(config_path), "error": str(exc)},
        )


def check_runtime() -> DoctorCheck:
    python_version = sys.version_info
    if python_version < (3, 10):
        return DoctorCheck(
            name="runtime",
            status="UNHEALTHY",
            message=f"Python 3.10+ required, current: {sys.version.split()[0]}",
        )

    git_path = shutil.which("git")
    if not git_path:
        return DoctorCheck(
            name="runtime",
            status="DEGRADED",
            message="git executable not found in PATH",
        )

    return DoctorCheck(
        name="runtime",
        status="HEALTHY",
        message=f"Python {python_version.major}.{python_version.minor}.{python_version.micro} and git available",
    )


def check_state(state_path: Path) -> DoctorCheck:
    if not state_path.exists():
        return DoctorCheck(
            name="state",
            status="HEALTHY",
            message=f"deduplication state not yet present (will baseline on first run): {state_path.name}",
            details={"state_path": str(state_path), "present": False},
        )

    try:
        content = state_path.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            return DoctorCheck(
                name="state",
                status="UNHEALTHY",
                message=f"state file {state_path.name} does not contain a JSON object",
                details={"state_path": str(state_path)},
            )
        return DoctorCheck(
            name="state",
            status="HEALTHY",
            message=f"state file is readable: {state_path.name}",
            details={"state_path": str(state_path), "present": True, "version": data.get("version")},
        )
    except Exception as exc:
        return DoctorCheck(
            name="state",
            status="UNHEALTHY",
            message=f"unreadable or malformed state file {state_path.name}: {exc}",
            details={"state_path": str(state_path), "error": str(exc)},
        )


def check_scheduler() -> DoctorCheck:
    if sys.platform != "win32":
        return DoctorCheck(
            name="scheduler",
            status="HEALTHY",
            message="scheduled task check skipped on non-Windows platform",
        )

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return DoctorCheck(
            name="scheduler",
            status="DEGRADED",
            message="powershell not available to inspect scheduled tasks",
        )

    try:
        cmd = [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "(Get-ScheduledTask -TaskName 'WUP Toolchain Update Watch' -ErrorAction SilentlyContinue).State",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        output = result.stdout.strip()
        if result.returncode == 0 and output:
            return DoctorCheck(
                name="scheduler",
                status="HEALTHY",
                message=f"WUP scheduled task is present (State: {output})",
                details={"task_name": "WUP Toolchain Update Watch", "state": output},
            )
        return DoctorCheck(
                name="scheduler",
                status="DEGRADED",
                message="WUP scheduled task 'WUP Toolchain Update Watch' not found (optional)",
                details={"task_name": "WUP Toolchain Update Watch", "present": False},
        )
    except Exception as exc:
        return DoctorCheck(
            name="scheduler",
            status="DEGRADED",
            message=f"scheduler inspection unavailable: {exc}",
            details={"error": str(exc)},
        )


def diagnose(
    repo_root: Optional[Path] = None,
    config_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    check_scheduler_task: bool = True,
) -> DoctorReport:
    root = repo_root or REPO_ROOT
    cfg_path = config_path or (root / "wup.toml")

    if state_path is None:
        try:
            cfg = wup_config.load_config(cfg_path)
            custom_state_dir = cfg.get("local", {}).get("state_dir")
            if custom_state_dir:
                state_root = Path(custom_state_dir)
            else:
                state_root = Path(os.environ.get("WUP_STATE_DIR") or (Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WUP"))
            st_path = state_root / "last-alerted.json"
        except Exception:
            st_path = Path("last-alerted.json")
    else:
        st_path = state_path

    checks = [
        check_repository(root),
        check_configuration(cfg_path),
        check_runtime(),
        check_state(st_path),
    ]

    if check_scheduler_task:
        checks.append(check_scheduler())

    if any(c.status == "UNHEALTHY" for c in checks):
        overall = "UNHEALTHY"
    elif any(c.status == "DEGRADED" for c in checks):
        overall = "DEGRADED"
    else:
        overall = "HEALTHY"

    return DoctorReport(status=overall, checks=checks)


def format_text_report(report: DoctorReport) -> str:
    lines = [f"WUP Doctor: {report.status}", "=" * 40]
    for c in report.checks:
        status_tag = f"[{c.status}]"
        lines.append(f"  {status_tag:<12} {c.name}: {c.message}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="path to configuration file")
    parser.add_argument("--repo-root", type=Path, help="path to repository root")
    parser.add_argument("--state-path", type=Path, help="path to state file")
    parser.add_argument("--skip-scheduler", action="store_true", help="skip Windows Task Scheduler inspection")
    parser.add_argument("--json", action="store_true", help="output report as JSON")
    args = parser.parse_args()

    report = diagnose(
        repo_root=args.repo_root,
        config_path=args.config,
        state_path=args.state_path,
        check_scheduler_task=not args.skip_scheduler,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_text_report(report))

    return 0 if report.status in ("HEALTHY", "DEGRADED") else 1


if __name__ == "__main__":
    sys.exit(main())
