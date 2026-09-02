#!/usr/bin/env python3
"""Report whether WUP is ready to activate without changing local state."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import doctor


@dataclass(frozen=True)
class PreflightFinding:
    name: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "message": self.message}


@dataclass(frozen=True)
class PreflightReport:
    ready: bool
    blockers: List[PreflightFinding]
    optional_warnings: List[PreflightFinding]

    @property
    def status(self) -> str:
        return "READY" if self.ready else "NOT_READY"

    def to_dict(self) -> Dict[str, object]:
        return {
            "ready": self.ready,
            "status": self.status,
            "blockers": [finding.to_dict() for finding in self.blockers],
            "optional_warnings": [finding.to_dict() for finding in self.optional_warnings],
        }


def project_doctor_report(report: doctor.DoctorReport) -> PreflightReport:
    """Project existing doctor truth; this function adds no new diagnostics."""
    blockers: List[PreflightFinding] = []
    warnings: List[PreflightFinding] = []

    for check in report.checks:
        finding = PreflightFinding(name=check.name, message=check.message)
        if check.status == "UNHEALTHY":
            blockers.append(finding)
        elif check.status == "DEGRADED":
            warnings.append(finding)

    return PreflightReport(
        ready=not blockers,
        blockers=blockers,
        optional_warnings=warnings,
    )


def format_text_report(report: PreflightReport) -> str:
    lines = ["WUP Setup Preflight", f"Status: {report.status}"]

    if report.blockers:
        lines.extend(["", "Blockers:"])
        lines.extend(f"* {finding.name}: {finding.message}" for finding in report.blockers)

    if report.optional_warnings:
        lines.extend(["", "Optional warnings:"])
        lines.extend(f"* {finding.name}: {finding.message}" for finding in report.optional_warnings)

    return "\n".join(lines)


def format_json_report(report: PreflightReport) -> str:
    return json.dumps(report.to_dict(), sort_keys=True)


def preflight_exit_code(report: PreflightReport) -> int:
    return 0 if report.ready else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="path to configuration file")
    parser.add_argument("--repo-root", type=Path, help="path to repository root")
    parser.add_argument("--state-path", type=Path, help="path to state file")
    parser.add_argument("--skip-scheduler", action="store_true", help="skip Windows Task Scheduler inspection")
    parser.add_argument("--json", action="store_true", help="output report as deterministic JSON")
    args = parser.parse_args(argv)

    try:
        doctor_report = doctor.diagnose(
            repo_root=args.repo_root,
            config_path=args.config,
            state_path=args.state_path,
            check_scheduler_task=not args.skip_scheduler,
        )
        report = project_doctor_report(doctor_report)
        print(format_json_report(report) if args.json else format_text_report(report))
        return preflight_exit_code(report)
    except Exception:
        print("WUP setup preflight could not complete.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
