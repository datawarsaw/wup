#!/usr/bin/env python3
"""Read-only doctor diagnostics baseline for WUP."""
from __future__ import annotations

import argparse
import json
import shutil
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
        return {"name": self.name, "status": self.status, "message": self.message}


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
            message=f"invalid configuration in {config_path.name}",
            details={"config_path": str(config_path), "error": "parse_failure"},
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


def diagnose(
    repo_root: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> DoctorReport:
    root = repo_root or REPO_ROOT
    cfg_path = config_path or (root / "wup.toml")

    checks = [
        check_repository(root),
        check_configuration(cfg_path),
        check_runtime(),
    ]

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


def doctor_exit_code(report: DoctorReport) -> int:
    return 1 if report.status == "UNHEALTHY" else 0


def format_json_report(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), sort_keys=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="path to configuration file")
    parser.add_argument("--repo-root", type=Path, help="path to repository root")
    parser.add_argument("--json", action="store_true", help="output report as JSON")
    args = parser.parse_args(argv)
    try:
        report = diagnose(
            repo_root=args.repo_root,
            config_path=args.config,
        )
        print(format_json_report(report) if args.json else format_text_report(report))
        return doctor_exit_code(report)
    except Exception:
        print("WUP doctor could not complete.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
