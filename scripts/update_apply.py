#!/usr/bin/env python3
"""Bounded, safe npm update applicator for WUP (Stage B).

Applies updates for an allowlist of supported npm-managed CLI tools:
- OpenCodex (@bitkyc08/opencodex)
- Wrangler (wrangler)
- Codex CLI (@openai/codex, npm install method only)

This module is strictly user-invoked, non-elevated, and updates exactly
one tool at a time with structured subprocess execution.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Tuple

try:
    from check_toolchain import (
        NetworkFetcher,
        ToolchainAuditor,
        ToolCheckResult,
    )
    from update_plan import build_update_plan
except ImportError:  # pragma: no cover
    from .check_toolchain import (
        NetworkFetcher,
        ToolchainAuditor,
        ToolCheckResult,
    )
    from .update_plan import build_update_plan


NPM_TIMEOUT_SECONDS = 120

# Static allowlist of APPLY-supported tools and their authoritative packages.
APPLY_ALLOWLIST: dict[str, str] = {
    "OpenCodex": "@bitkyc08/opencodex",
    "Wrangler": "wrangler",
    "Codex CLI": "@openai/codex",
}

# Regex to validate semver format before subprocess invocation.
SEMVER_REGEX = re.compile(r"^[0-9]+(?:.[0-9]+)+(?:-[-a-zA-Z0-9_.]+)?$")


class ApplyError(RuntimeError):
    """Base error for update application failures."""


class PreconditionFailedError(ApplyError):
    """A pre-execution gate or TOCTOU check failed."""


def resolve_npm_executable() -> Optional[Path]:
    """Safely resolve the npm executable as an absolute path."""
    candidates = ["npm.cmd", "npm.exe", "npm"] if os.name == "nt" else ["npm"]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            resolved = Path(found).resolve()
            if resolved.is_file() and resolved.name.lower() in ("npm.cmd", "npm.exe", "npm"):
                return resolved
    return None


def probe_single_tool(
    tool_name: str,
    auditor: Optional[ToolchainAuditor] = None,
) -> ToolCheckResult:
    """Run a fresh local probe and remote lookup for exactly one tool."""
    if auditor is None:
        fetcher = NetworkFetcher(offline=False)
        auditor = ToolchainAuditor(fetcher=fetcher)

    if tool_name == "OpenCodex":
        tool_res, _ = auditor.check_opencodex()
        return tool_res
    elif tool_name == "Wrangler":
        return auditor.check_wrangler()
    elif tool_name == "Codex CLI":
        return auditor.check_codex_cli()
    else:
        raise ValueError(f"Tool {tool_name!r} is not an APPLY-supported tool")


def validate_target_version(version: Optional[str]) -> str:
    """Validate that the target version has a safe semver shape."""
    if not version or not isinstance(version, str):
        raise PreconditionFailedError("Target version is missing or empty")
    cleaned = version.strip()
    if not SEMVER_REGEX.match(cleaned):
        raise PreconditionFailedError(f"Target version {version!r} does not match valid semver syntax")
    return cleaned


def check_preconditions(
    tool_name: str,
    tool_res: ToolCheckResult,
) -> Tuple[str, str, str]:
    """Evaluate pre-execution freshness gates and return (package, installed_ver, target_ver)."""
    if tool_name not in APPLY_ALLOWLIST:
        raise PreconditionFailedError(
            f"Tool {tool_name!r} is not in the APPLY allowlist. Supported: {', '.join(sorted(APPLY_ALLOWLIST))}"
        )

    package_name = APPLY_ALLOWLIST[tool_name]

    installed = tool_res.installed_version
    if not installed:
        raise PreconditionFailedError(f"Tool {tool_name!r} is not currently installed on this workstation")

    latest = tool_res.latest_version
    if not latest:
        raise PreconditionFailedError(f"Latest version for {tool_name!r} could not be determined")

    target_version = validate_target_version(latest)

    if installed == target_version:
        raise PreconditionFailedError(f"Tool {tool_name!r} is already at target version {target_version}")

    # Confirm installation method is npm for every supported tool
    method = (tool_res.install_method or "").strip().lower()
    if not method.startswith("npm"):
        raise PreconditionFailedError(
            f"{tool_name} install method is {tool_res.install_method!r}; only confirmed npm installations are supported for APPLY"
        )

    # Check update plan eligibility
    plan = build_update_plan([tool_res])
    if not plan.entries or not plan.entries[0].update_needed:
        raise PreconditionFailedError(
            f"Tool {tool_name!r} status is {tool_res.status}, update is not eligible"
        )

    entry = plan.entries[0]
    if entry.mechanism != "npm" or entry.automation_mode != "AUTOMATABLE":
        raise PreconditionFailedError(
            f"Tool {tool_name!r} mechanism is {entry.mechanism} ({entry.automation_mode}), not AUTOMATABLE npm"
        )

    return package_name, installed, target_version


def format_recovery_guidance(tool_name: str, package_name: str, previous_version: str) -> str:
    """Format deterministic manual recovery guidance."""
    return (
        f"Recovery guidance:\n"
        f"  Reinstall previous version manually:\n"
        f"    npm install -g {package_name}@{previous_version}\n"
        f"  Verify recovery:\n"
        f"    python scripts/check_toolchain.py"
    )


def apply_update(
    tool_name: str,
    *,
    yes: bool = False,
    auditor: Optional[ToolchainAuditor] = None,
    npm_path: Optional[Path] = None,
    subprocess_runner: Optional[Callable[..., Any]] = None,
    input_prompter: Optional[Callable[[str], str]] = None,
    timeout: int = NPM_TIMEOUT_SECONDS,
) -> Tuple[str, str]:
    """Execute the bounded APPLY flow for one tool.

    Returns (status_result, message).
    """
    if tool_name not in APPLY_ALLOWLIST:
        return "UNSUPPORTED_TOOL", f"Tool {tool_name!r} is not supported for automated update."

    # Gate 1: Fresh pre-execution probe (TOCTOU check)
    try:
        tool_res = probe_single_tool(tool_name, auditor=auditor)
        package_name, installed_version, target_version = check_preconditions(tool_name, tool_res)
    except PreconditionFailedError as exc:
        return "PRECONDITION_FAILED", str(exc)

    # Gate 2: Resolve npm executable
    if npm_path is None:
        npm_path = resolve_npm_executable()
    else:
        npm_path = Path(npm_path)

    if not npm_path or not npm_path.is_file():
        return "NPM_NOT_FOUND", "npm executable could not be resolved safely on PATH."

    # Gate 3: Authorization prompt
    if not yes:
        print("WUP Update Apply (Stage B)", file=sys.stderr)
        print("========================================", file=sys.stderr)
        print(f"  Tool:             {tool_name}", file=sys.stderr)
        print(f"  Current version:  {installed_version}", file=sys.stderr)
        print(f"  Target version:   {target_version}", file=sys.stderr)
        print(f"  Package:          {package_name}@{target_version}", file=sys.stderr)
        print(f"  Planned action:   npm install -g {package_name}@{target_version}", file=sys.stderr)
        print("----------------------------------------", file=sys.stderr)

        prompter = input_prompter or input
        try:
            answer = prompter(f"Proceed with updating {tool_name}? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "USER_DECLINED", "Update canceled: input stream closed."

        if answer not in ("y", "yes"):
            return "USER_DECLINED", "Update declined by user."

    # Gate 4: Execution via structured argv (shell=False)
    cmd = [str(npm_path), "install", "-g", f"{package_name}@{target_version}"]
    runner = subprocess_runner or subprocess.run

    try:
        res = runner(cmd, capture_output=True, text=True, timeout=timeout, shell=False, check=False)
    except subprocess.TimeoutExpired:
        recovery = format_recovery_guidance(tool_name, package_name, installed_version)
        return "APPLY_EXECUTION_FAILED", f"npm install timed out after {timeout} seconds.\n{recovery}"
    except OSError as exc:
        recovery = format_recovery_guidance(tool_name, package_name, installed_version)
        return "APPLY_EXECUTION_FAILED", f"Failed to launch npm: {exc}\n{recovery}"

    # Gate 5: Check exit code & elevation requirements
    if res.returncode != 0:
        err_lower = (res.stderr or "").lower() + (res.stdout or "").lower()
        recovery = format_recovery_guidance(tool_name, package_name, installed_version)
        if "eacces" in err_lower or "eperm" in err_lower or "operation not permitted" in err_lower:
            return (
                "ELEVATION_REQUIRED",
                f"npm install failed with permissions/access error. WUP does not perform administrative elevation.\n{recovery}",
            )
        return (
            "APPLY_EXECUTION_FAILED",
            f"npm install failed with exit code {res.returncode}.\n{recovery}",
        )

    # Gate 6: Post-update Verification
    post_res = probe_single_tool(tool_name, auditor=auditor)
    if post_res.installed_version != target_version:
        recovery = format_recovery_guidance(tool_name, package_name, installed_version)
        return (
            "APPLY_VERIFICATION_FAILED",
            f"Post-update verification failed: expected {target_version}, observed {post_res.installed_version}.\n{recovery}",
        )

    post_health = (post_res.health or "").strip().upper()
    if post_health != "HEALTHY":
        recovery = format_recovery_guidance(tool_name, package_name, installed_version)
        return (
            "APPLY_VERIFICATION_FAILED",
            f"Post-update health check failed: expected HEALTHY, observed {post_res.health!r}.\n{recovery}",
        )

    return "APPLY_VERIFIED", f"{tool_name} successfully updated from {installed_version} to {target_version}."


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tool",
        required=True,
        help=f"Tool to update. Supported: {', '.join(sorted(APPLY_ALLOWLIST))}",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip interactive confirmation (explicit foreground CLI only)",
    )
    args = parser.parse_args(argv)

    status, message = apply_update(args.tool, yes=args.yes)
    if status == "APPLY_VERIFIED":
        print(f"APPLY_VERIFIED: {message}")
        return 0
    else:
        print(f"{status}: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
