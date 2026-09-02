#!/usr/bin/env python3
"""
check_toolchain.py - Daily Toolchain Watch V1

Deterministic, read-only AI workstation toolchain health and version auditor.
Checks core agent tools, runtimes, and local AI components without modifying
workstation state, installing packages, restarting daemons, or exposing secrets.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Semantic Versioning Helper
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemVer:
    raw: str
    parts: Tuple[int, ...]
    prerelease: Optional[str] = None

    @classmethod
    def parse(cls, version_str: Optional[str]) -> Optional["SemVer"]:
        if not version_str:
            return None
        s = version_str.strip()
        m = re.search(r"(\d+(?:\.\d+)*)(?:-([a-zA-Z0-9_\-\.]+))?", s)
        if not m:
            return None
        num_str = m.group(1)
        prerelease = m.group(2)
        try:
            nums = tuple(int(x) for x in num_str.split("."))
            return cls(raw=version_str, parts=nums, prerelease=prerelease)
        except Exception:
            return None

    def compare(self, other: "SemVer") -> int:
        """Return -1 if self < other, 0 if self == other, 1 if self > other."""
        max_len = max(len(self.parts), len(other.parts))
        p1 = self.parts + (0,) * (max_len - len(self.parts))
        p2 = other.parts + (0,) * (max_len - len(other.parts))
        if p1 < p2:
            return -1
        if p1 > p2:
            return 1
        if self.prerelease is None and other.prerelease is not None:
            return 1
        if self.prerelease is not None and other.prerelease is None:
            return -1
        return 0


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class UpdateRecommendation:
    why: str
    breaking_relevance: str
    install_method: str
    proposed_command: str
    rollback_command: str
    validation_checks: List[str]


@dataclass
class OpenCodexDiagnostics:
    installed_version: Optional[str] = None
    health: str = "HEALTHY"
    proxy_running: bool = False
    proxy_state: str = "UNVERIFIED"
    direct_healthz: str = "UNVERIFIED"
    health_command_summary: str = "unverified"
    proxy_summary: str = "unverified"
    service_status: str = "unverified"
    shim_status: str = "unverified"
    shim_aligned: bool = False
    runtime_source: str = "unverified"
    detected_codex: Optional[str] = None
    catalog_health: str = "unverified"
    oauth_summary: str = "unverified"
    warnings: List[str] = field(default_factory=list)


@dataclass
class ToolCheckResult:
    name: str
    installed_version: Optional[str]
    install_method: str
    latest_version: Optional[str]
    latest_source: str
    status: str  # Strictly one of: CURRENT, WATCH, UPDATE, URGENT, UNKNOWN
    health: str  # HEALTHY, DEGRADED, UNVERIFIED, NOT_INSTALLED
    attention_notes: List[str] = field(default_factory=list)
    update_recommendation: Optional[UpdateRecommendation] = None
    release_url: Optional[str] = None
    is_bundled: bool = False
    runtime_type: str = "system"  # system, bundled, managed_mcp, app_package


@dataclass
class ToolchainAuditReport:
    timestamp: str
    date: str
    core_stack_health: str  # HEALTHY, DEGRADED, UNVERIFIED
    tools: List[ToolCheckResult] = field(default_factory=list)
    attention: List[str] = field(default_factory=list)
    recommended_actions: List[str] = field(default_factory=list)
    observer_path_status: str = "NORMAL"  # NORMAL, DEGRADED


# ---------------------------------------------------------------------------
# Secret Redaction
# ---------------------------------------------------------------------------

SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{16,}", re.IGNORECASE),
    re.compile(r"ocx_session_[a-zA-Z0-9_\-]+", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?([^'\"\s]{6,})['\"]?", re.IGNORECASE),
]

def redact_secrets(text: str) -> str:
    if not text:
        return text
    result = text
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


# ---------------------------------------------------------------------------
# Safe Network Fetcher & Process Execution
# ---------------------------------------------------------------------------

class NetworkFetcher:
    def __init__(self, offline: bool = False, timeout: float = 3.0, mock_data: Optional[Dict[str, Any]] = None):
        self.offline = offline
        self.timeout = timeout
        self.mock_data = mock_data or {}

    def fetch_json(self, url: str) -> Optional[Any]:
        if url in self.mock_data:
            return self.mock_data[url]
        if self.offline:
            return None
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Daily-Toolchain-Watch/1.0 (Windows NT)",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except Exception:
            return None

    def fetch_text(self, url: str) -> Optional[str]:
        """Fetch text from an authoritative endpoint with the same offline semantics."""
        if url in self.mock_data:
            value = self.mock_data[url]
            return value if isinstance(value, str) else None
        if self.offline:
            return None
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Daily-Toolchain-Watch/1.0 (Windows NT)",
                "Accept": "text/html",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception:
            return None


@dataclass(frozen=True)
class RemoteToolProvider:
    """Static upstream-version resolver metadata for one monitored tool."""

    name: str
    source: str
    resolver: str
    release_url_template: str
    fetch_order: int


# This catalog is intentionally static: it describes the existing upstream-only
# providers and dispatches only to the bounded resolver types below.
REMOTE_TOOL_PROVIDER_REGISTRY = (
    RemoteToolProvider("Codex CLI", "https://registry.npmjs.org/@openai/codex/latest", "npm_latest", "https://www.npmjs.com/package/@openai/codex/v/{version}", 0),
    RemoteToolProvider("OpenCodex", "https://registry.npmjs.org/@bitkyc08/opencodex/latest", "npm_latest", "https://www.npmjs.com/package/@bitkyc08/opencodex/v/{version}", 1),
    RemoteToolProvider("Node.js", "https://nodejs.org/dist/index.json", "node_stable", "https://nodejs.org/en/blog/release/v{version}", 4),
    RemoteToolProvider("npm", "https://registry.npmjs.org/npm/latest", "npm_latest", "https://github.com/npm/cli/releases/tag/v{version}", 2),
    RemoteToolProvider("Git", "https://api.github.com/repos/git-for-windows/git/releases/latest", "github_release", "https://github.com/git-for-windows/git/releases", 5),
    RemoteToolProvider("Wrangler", "https://registry.npmjs.org/wrangler/latest", "npm_latest", "https://www.npmjs.com/package/wrangler/v/{version}", 3),
    RemoteToolProvider("Bun", "https://api.github.com/repos/oven-sh/bun/releases/latest", "github_release", "https://github.com/oven-sh/bun/releases", 6),
)

# Kept as the public source mapping used by the remote sentinel.
REMOTE_VERSION_SOURCES = {provider.name: provider.source for provider in REMOTE_TOOL_PROVIDER_REGISTRY}


ResolverResult = Tuple[Optional[Dict[str, str]], Optional[str]]
ResolverHandler = Callable[[RemoteToolProvider, Any], ResolverResult]


def resolve_npm_latest(provider: RemoteToolProvider, data: Any) -> ResolverResult:
    """Resolve a version from an npm package's latest metadata."""
    version = str(data.get("version", "")) if isinstance(data, dict) else ""
    if SemVer.parse(version):
        return {
            "latest_version": version,
            "release_url": provider.release_url_template.format(version=version),
        }, None
    return None, "authoritative npm registry response was unavailable or invalid"


def resolve_node_stable(provider: RemoteToolProvider, data: Any) -> ResolverResult:
    """Resolve the first stable version from the Node.js release index."""
    if not isinstance(data, list):
        return None, "authoritative Node.js release index was unavailable or invalid"
    version = next(
        (
            str(item.get("version", "")).lstrip("v")
            for item in data
            if isinstance(item, dict)
            and SemVer.parse(str(item.get("version", "")).lstrip("v"))
            and "-" not in str(item.get("version", ""))
        ),
        "",
    )
    if version:
        return {
            "latest_version": version,
            "release_url": provider.release_url_template.format(version=version),
        }, None
    return None, "authoritative Node.js release index had no stable release"


def resolve_github_release(provider: RemoteToolProvider, data: Any) -> ResolverResult:
    """Resolve a version and release URL from GitHub's latest-release metadata."""
    tag = str(data.get("tag_name", "")) if isinstance(data, dict) else ""
    match = re.search(r"(\d+\.\d+\.\d+)", tag)
    if match:
        return {
            "latest_version": match.group(1),
            "release_url": str(data.get("html_url") or provider.release_url_template),
        }, None
    return None, "authoritative GitHub release response was unavailable or invalid"


# This map is intentionally fixed and in-process; remote resolvers are not plugins.
REMOTE_RESOLVER_HANDLERS: Dict[str, ResolverHandler] = {
    "npm_latest": resolve_npm_latest,
    "node_stable": resolve_node_stable,
    "github_release": resolve_github_release,
}


def validate_remote_provider_registry(
    registry: Tuple[RemoteToolProvider, ...], handlers: Dict[str, ResolverHandler]
) -> None:
    """Reject malformed static remote-provider metadata before any fetches occur."""
    names = set()
    fetch_orders = set()

    for provider in registry:
        if not provider.name:
            raise ValueError("remote provider registry contains an empty provider name")
        if provider.name in names:
            raise ValueError(f"remote provider registry contains duplicate provider name: {provider.name}")
        if provider.fetch_order in fetch_orders:
            raise ValueError(f"remote provider registry contains duplicate fetch order: {provider.fetch_order}")
        if not provider.source:
            raise ValueError(f"remote provider registry contains an empty source for: {provider.name}")
        if not provider.release_url_template:
            raise ValueError(f"remote provider registry contains an empty release URL template for: {provider.name}")
        if provider.resolver not in handlers:
            raise ValueError(f"remote provider registry contains unsupported resolver: {provider.resolver}")

        names.add(provider.name)
        fetch_orders.add(provider.fetch_order)


def resolve_remote_upstream_versions(fetcher: NetworkFetcher) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """Resolve upstream-only versions without inspecting a workstation executable or runtime."""
    validate_remote_provider_registry(REMOTE_TOOL_PROVIDER_REGISTRY, REMOTE_RESOLVER_HANDLERS)
    versions: Dict[str, Dict[str, str]] = {}
    failures: Dict[str, str] = {}

    for provider in sorted(REMOTE_TOOL_PROVIDER_REGISTRY, key=lambda item: item.fetch_order):
        data = fetcher.fetch_json(provider.source)
        handler = REMOTE_RESOLVER_HANDLERS[provider.resolver]
        resolved, failure = handler(provider, data)
        if resolved is not None:
            versions[provider.name] = resolved
        else:
            failures[provider.name] = failure or "remote resolver failed without a failure reason"
    return versions, failures


def safe_run_command(args: List[str], timeout: float = 4.0) -> Tuple[int, str, str]:
    """Run a command safely with bounded timeout, resolved binary, and no shell."""
    try:
        cmd_name = args[0]
        resolved = shutil.which(cmd_name)
        actual_args = [resolved] + args[1:] if resolved else args
        proc = subprocess.run(
            actual_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except FileNotFoundError:
        return 127, "", "Executable not found"
    except Exception as exc:
        return -2, "", str(exc)


def safe_read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Toolchain Auditor Implementation
# ---------------------------------------------------------------------------

class ToolchainAuditor:
    def __init__(
        self,
        fetcher: NetworkFetcher,
        appdata_roaming: Optional[Path] = None,
        localappdata: Optional[Path] = None,
        user_home: Optional[Path] = None,
        command_runner: Optional[Callable[[List[str], float], Tuple[int, str, str]]] = None,
    ):
        self.fetcher = fetcher
        self.user_home = user_home or Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
        self.appdata_roaming = appdata_roaming or Path(os.environ.get("APPDATA", str(self.user_home / "AppData" / "Roaming")))
        self.localappdata = localappdata or Path(os.environ.get("LOCALAPPDATA", str(self.user_home / "AppData" / "Local")))
        self.npm_global_root = self.appdata_roaming / "npm" / "node_modules"
        self.run_cmd = command_runner or safe_run_command
        self.observer_degraded = False

    @staticmethod
    def _probe_opencodex_healthz() -> bool:
        """Return True only when a direct HTTP healthz probe verifies the proxy."""
        try:
            req = urllib.request.Request("http://127.0.0.1:10100/healthz", headers={"User-Agent": "ToolchainWatch"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status != 200:
                    return False
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("status") == "ok"
        except Exception:
            return False

    # 1. OpenCodex Full Diagnostic Check
    def check_opencodex(self) -> Tuple[ToolCheckResult, OpenCodexDiagnostics]:
        name = "OpenCodex"
        installed_version = None
        install_method = "npm (@bitkyc08/opencodex)"
        latest_version = None
        latest_source = "npm registry (@bitkyc08/opencodex)"
        health = "HEALTHY"
        attention: List[str] = []
        rec: Optional[UpdateRecommendation] = None

        diag = OpenCodexDiagnostics()

        # Version check via CLI then package.json fallback
        rc_v, out_v, _ = self.run_cmd(["ocx", "--version"], 3.0)
        if rc_v == 0 and out_v:
            m = re.search(r"(\d+\.\d+\.\d+)", out_v)
            if m:
                installed_version = m.group(1)

        if not installed_version:
            pkg_path = self.npm_global_root / "@bitkyc08" / "opencodex" / "package.json"
            pkg_data = safe_read_json(pkg_path)
            if pkg_data and "version" in pkg_data:
                installed_version = str(pkg_data["version"])

        diag.installed_version = installed_version

        # ocx health & status checks
        rc_h, out_h, err_h = self.run_cmd(["ocx", "health"], 4.0)
        diag.raw_health_output = out_h

        rc_s, out_s, err_s = self.run_cmd(["ocx", "status"], 4.0)

        # Keep direct HTTP verification distinct from the ocx health report.
        probe_live = self._probe_opencodex_healthz()
        health_reports_healthy = rc_h == 0 and "Proxy healthy" in out_h
        health_reports_unhealthy = rc_h == 0 and any(
            marker in out_h.lower() for marker in ("proxy unhealthy", "proxy unreachable", "proxy not running")
        )

        if probe_live:
            diag.proxy_running = True
            diag.proxy_state = "HEALTHY"
            diag.direct_healthz = "VERIFIED"
            diag.proxy_summary = "running (port 10100; direct healthz verified)"
        elif health_reports_healthy:
            diag.proxy_running = True
            diag.proxy_state = "REPORTED_HEALTHY"
            diag.health_command_summary = "ocx health reports healthy"
            diag.proxy_summary = "ocx health reports healthy; direct healthz unverified"
            health = "UNVERIFIED"
            self.observer_degraded = True
            attention.append("OpenCodex proxy is reported healthy by ocx, but the observer could not directly verify healthz")
        elif health_reports_unhealthy:
            diag.proxy_running = False
            diag.proxy_state = "UNHEALTHY"
            diag.health_command_summary = "ocx health reports unhealthy"
            diag.proxy_summary = "ocx health reports unhealthy"
            health = "DEGRADED"
            attention.append("OpenCodex local proxy is reported unhealthy on port 10100")
        else:
            diag.proxy_running = False
            diag.proxy_state = "UNVERIFIED"
            diag.proxy_summary = "observer unable to verify proxy health"
            health = "UNVERIFIED"
            self.observer_degraded = True
            attention.append("OpenCodex proxy health is unverified because required diagnostics were unavailable or malformed")

        if rc_h == 0 and out_h:
            if diag.health_command_summary == "unverified":
                diag.health_command_summary = "ocx health completed"
        else:
            health = "UNVERIFIED" if health != "DEGRADED" else health
            self.observer_degraded = True
            diag.warnings.append("ocx health command failed or was restricted in sandbox")

        if out_s:
            m_codex = re.search(r"Codex version:\s*([0-9.]+)", out_s)
            if m_codex:
                diag.detected_codex = m_codex.group(1)

            m_srv = re.search(r"Service:\s*([^\r\n]+)", out_s)
            if m_srv:
                diag.service_status = m_srv.group(1).strip()

            m_shim = re.search(r"Codex autostart shim:\s*([^\r\n]+)", out_s)
            if m_shim:
                shim_line = m_shim.group(1).strip()
                diag.shim_status = shim_line
                if "not an opencodex shim" in shim_line or "wrapper present" in shim_line:
                    diag.shim_aligned = False
                    attention.append("OpenCodex autostart shim is bypassed by standard npm wrapper (run 'ocx codex-shim install' after updates)")
                else:
                    diag.shim_aligned = True
            else:
                diag.shim_status = "unverified"
                diag.shim_aligned = False

            m_rt = re.search(r"Runtime source:\s*([^\r\n]+)", out_s)
            if m_rt:
                diag.runtime_source = m_rt.group(1).strip()

            m_clamp = re.search(r"Catalog clamp:\s*([^\r\n]+)", out_s)
            m_plug = re.search(r"Codex bundled plugins:\s*([^\r\n]+)", out_s)
            clamp_val = m_clamp.group(1).strip() if m_clamp else "unknown"
            plug_val = m_plug.group(1).strip() if m_plug else "unknown"
            diag.catalog_health = f"clamp: {clamp_val}; plugins: {plug_val}"

            m_oauth = re.search(r"OAuth logins:(.*?)(?:\n\d+\s*\||\n\s*EPERM|\Z)", out_s, re.DOTALL)
            if m_oauth:
                login_items: List[str] = []
                for line in m_oauth.group(1).strip().splitlines():
                    line = line.strip()
                    if line and not any(k in line.lower() for k in ["token", "key", "secret", "password"]):
                        login_items.append(line)
                diag.oauth_summary = f"{len(login_items)} provider(s) checked, zero credentials exposed"
        else:
            health = "UNVERIFIED" if health != "DEGRADED" else health
            self.observer_degraded = True
            diag.warnings.append("ocx status command failed or was restricted in sandbox")
            attention.append("OpenCodex status diagnostics are unavailable or restricted; shim, routing, and catalog evidence is unverified")

        status_evidence_complete = (
            rc_s == 0
            and bool(diag.detected_codex)
            and diag.shim_status != "unverified"
            and "unknown" not in diag.catalog_health
            and diag.oauth_summary != "unverified"
        )
        if out_s and not status_evidence_complete:
            health = "UNVERIFIED" if health != "DEGRADED" else health
            self.observer_degraded = True
            diag.warnings.append("ocx status output was incomplete, malformed, or restricted")
            attention.append("OpenCodex status did not provide complete Codex-version, shim, provider/OAuth, and model-catalog evidence")

        combined_err = f"{err_h} {err_s}"
        if "EPERM" in combined_err or "permission denied" in combined_err.lower():
            self.observer_degraded = True
            health = "UNVERIFIED" if health != "DEGRADED" else health
            diag.warnings.append("Sandbox permission boundary encountered for user-restricted files (.opencodex auth/lock)")

        if not installed_version:
            definitely_missing = rc_v == 127
            result_health = "NOT_INSTALLED" if definitely_missing else "UNVERIFIED"
            if not definitely_missing:
                self.observer_degraded = True
            diag.health = result_health
            return ToolCheckResult(
                name=name,
                installed_version=None,
                install_method="unknown",
                latest_version=None,
                latest_source=latest_source,
                status="UNKNOWN",
                health=result_health,
                attention_notes=[
                    "OpenCodex package or CLI not found"
                    if definitely_missing
                    else "OpenCodex executable version could not be verified; package metadata was unavailable"
                ],
            ), diag

        latest_data = self.fetcher.fetch_json(REMOTE_VERSION_SOURCES["OpenCodex"])
        if latest_data and isinstance(latest_data, dict) and "version" in latest_data:
            latest_version = str(latest_data["version"])
        else:
            latest_source = "npm registry (unreachable)"
            latest_version = None

        v_inst = SemVer.parse(installed_version)
        v_lat = SemVer.parse(latest_version)

        if not v_lat:
            status = "UNKNOWN"
        elif v_inst and v_lat and v_inst.compare(v_lat) < 0:
            status = "UPDATE"
            rec = UpdateRecommendation(
                why=f"Newer stable release {latest_version} available (current: {installed_version})",
                breaking_relevance="Verify provider routing compatibility and model catalog preservation before upgrading.",
                install_method=install_method,
                proposed_command=f"npm install -g @bitkyc08/opencodex@{latest_version}",
                rollback_command=f"npm install -g @bitkyc08/opencodex@{installed_version}",
                validation_checks=[
                    "ocx --version",
                    "ocx health",
                    "ocx status (verify Codex version detection, shim status, provider/OAuth, and model-catalog clamp)",
                    "Invoke-RestMethod http://127.0.0.1:10100/healthz",
                ],
            )
        else:
            status = "CURRENT"

        diag.health = health
        return ToolCheckResult(
            name=name,
            installed_version=installed_version,
            install_method=install_method,
            latest_version=latest_version or "unknown",
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
            update_recommendation=rec,
        ), diag

    # 2. Codex CLI (Coupled with OpenCodex)
    def check_codex_cli(self, ocx_diag: Optional[OpenCodexDiagnostics] = None) -> ToolCheckResult:
        name = "Codex CLI"
        installed_version = None
        package_version = None
        install_method = "unknown"
        latest_version = None
        latest_source = "npm registry (@openai/codex)"
        health = "HEALTHY"
        attention: List[str] = []
        rec: Optional[UpdateRecommendation] = None

        pkg_path = self.npm_global_root / "@openai" / "codex" / "package.json"
        pkg_data = safe_read_json(pkg_path)
        if pkg_data and "version" in pkg_data:
            package_version = str(pkg_data["version"])

        codex_bin = shutil.which("codex")
        if codex_bin:
            if "WindowsApps" in codex_bin:
                install_method = f"Windows App package ({codex_bin})"
            elif package_version:
                install_method = f"npm (@openai/codex; active executable: {codex_bin})"
            else:
                install_method = f"binary ({codex_bin})"
        elif package_version:
            install_method = "npm metadata (@openai/codex; active executable unresolved)"

        # The active executable is authoritative; package metadata is supporting evidence only.
        rc, stdout, _ = self.run_cmd(["codex", "--version"], 2.0)
        if rc == 0 and stdout:
            m = re.search(r"(\d+\.\d+\.\d+)", stdout)
            if m:
                installed_version = m.group(1)

        package_mismatch = False
        if installed_version and package_version:
            active_semver = SemVer.parse(installed_version)
            package_semver = SemVer.parse(package_version)
            if active_semver and package_semver and active_semver.compare(package_semver) != 0:
                package_mismatch = True
                health = "DEGRADED"
                attention.append(
                    f"Active Codex CLI reports {installed_version}, but npm package metadata reports {package_version}; executable/package mismatch"
                )

        rc_doc, stdout_doc, _ = self.run_cmd(["codex", "doctor", "--json"], 4.0)
        if rc_doc == 0 and stdout_doc:
            try:
                doc_json = json.loads(stdout_doc)
                checks = doc_json.get("checks", {})
                runtime_chk = checks.get("runtime.install_method", {}).get("status")
                install_chk = checks.get("install.consistent", {}).get("status")
                if runtime_chk == "fail" or install_chk == "fail":
                    health = "DEGRADED"
                    attention.append("Codex Doctor reported inconsistent runtime or install state")
            except Exception:
                health = "UNVERIFIED"
                self.observer_degraded = True
                attention.append("Codex Doctor output could not be parsed as valid JSON")
        else:
            health = "UNVERIFIED"
            self.observer_degraded = True

        if not installed_version:
            definitely_missing = rc == 127 and not codex_bin and not package_version
            health = "NOT_INSTALLED" if definitely_missing else "UNVERIFIED"
            detail = "Active Codex CLI executable not found" if definitely_missing else "Active codex --version failed or returned malformed output"
            if package_version:
                detail += f"; npm metadata reports {package_version} but is not executable truth"
            return ToolCheckResult(
                name=name,
                installed_version=None,
                install_method=install_method,
                latest_version=None,
                latest_source=latest_source,
                status="UNKNOWN",
                health=health,
                attention_notes=[detail],
            )

        latest_data = self.fetcher.fetch_json(REMOTE_VERSION_SOURCES["Codex CLI"])
        if latest_data and isinstance(latest_data, dict) and "version" in latest_data:
            latest_version = str(latest_data["version"])
        else:
            latest_version = None
            latest_source = "npm registry (unreachable)"

        cache_file = self.user_home / ".codex" / "version.json"
        cache_data = safe_read_json(cache_file)
        if cache_data and "latest_version" in cache_data and cache_data["latest_version"]:
            cached_ver = str(cache_data["latest_version"])
            last_chk = str(cache_data.get("last_checked_at", ""))[:10]
            attention.append(f"Local Codex version.json cache recorded {cached_ver} on {last_chk} (informational; live registry check required for verdict)")

        v_inst = SemVer.parse(installed_version)
        v_lat = SemVer.parse(latest_version)

        coupling_mismatch = False
        if ocx_diag and ocx_diag.detected_codex:
            detected_semver = SemVer.parse(ocx_diag.detected_codex)
            if v_inst and detected_semver and v_inst.compare(detected_semver) != 0:
                coupling_mismatch = True
                health = "DEGRADED"
                attention.append(
                    f"Codex/OpenCodex version mismatch: active Codex CLI is {installed_version}, while ocx status detects {ocx_diag.detected_codex}"
                )

        ocx_risky = False
        risk_reasons: List[str] = []
        if not ocx_diag:
            ocx_risky = True
            risk_reasons.append("OpenCodex diagnostics unavailable")
        else:
            if ocx_diag.proxy_state not in ("HEALTHY",):
                ocx_risky = True
                risk_reasons.append(f"OpenCodex proxy evidence is {ocx_diag.proxy_state}")
            if not ocx_diag.shim_aligned:
                ocx_risky = True
                risk_reasons.append("OpenCodex autostart shim is currently bypassed or unverified")
            if ocx_diag.health != "HEALTHY":
                ocx_risky = True
                risk_reasons.append(f"OpenCodex health is {ocx_diag.health}")
            if not ocx_diag.detected_codex:
                ocx_risky = True
                risk_reasons.append("OpenCodex-detected Codex version is unverified")
        if coupling_mismatch:
            ocx_risky = True
            risk_reasons.append("active Codex CLI and ocx-detected Codex versions disagree")
        if package_mismatch:
            ocx_risky = True
            risk_reasons.append("active Codex CLI and npm package metadata disagree")

        if not v_lat:
            status = "UNKNOWN"
        elif v_inst and v_lat and v_inst.compare(v_lat) < 0:
            if ocx_risky:
                status = "WATCH"
                attention.append(f"Codex CLI update to {latest_version} held as WATCH: OpenCodex coupling risk ({', '.join(risk_reasons)}). Re-engage shim ('ocx codex-shim install') or coordinate updates.")
                rec = UpdateRecommendation(
                    why=f"Newer stable release {latest_version} available (current: {installed_version}) - held as WATCH due to OpenCodex coupling risk",
                    breaking_relevance=f"Updating Codex CLI via npm overwrites autostart shims (codex.cmd, codex.ps1). Coupling risk: {', '.join(risk_reasons)}.",
                    install_method=install_method,
                    proposed_command=f"npm install -g @openai/codex@{latest_version}",
                    rollback_command=f"npm install -g @openai/codex@{installed_version}",
                    validation_checks=[
                        "codex --version",
                        "codex doctor",
                        "ocx codex-shim status (verify shim alignment)",
                        "ocx status (verify model catalog preservation & clamp status)",
                        "Invoke-RestMethod http://127.0.0.1:10100/healthz (verify proxy health)",
                    ],
                )
            else:
                status = "UPDATE"
                rec = UpdateRecommendation(
                    why=f"Newer stable release {latest_version} available (current: {installed_version})",
                    breaking_relevance="Updating via npm will overwrite OpenCodex autostart shims; re-run 'ocx codex-shim install' post-update.",
                    install_method=install_method,
                    proposed_command=f"npm install -g @openai/codex@{latest_version}",
                    rollback_command=f"npm install -g @openai/codex@{installed_version}",
                    validation_checks=[
                        "codex --version",
                        "codex doctor",
                        "ocx codex-shim status (verify shim alignment)",
                        "ocx status (verify model catalog preservation & clamp status)",
                        "Invoke-RestMethod http://127.0.0.1:10100/healthz (verify proxy health)",
                    ],
                )
        else:
            status = "WATCH" if package_mismatch or coupling_mismatch else "CURRENT"

        return ToolCheckResult(
            name=name,
            installed_version=installed_version,
            install_method=install_method,
            latest_version=latest_version or "unknown",
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
            update_recommendation=rec,
        )

    # 3. Codex Desktop
    def check_codex_desktop(self) -> ToolCheckResult:
        name = "Codex Desktop"
        installed_version = None
        install_method = "Windows App package (MSIX)"
        latest_version = "unknown"
        latest_source = "Microsoft Store / WindowsApps (no public registry API)"
        health = "HEALTHY"
        attention: List[str] = []

        path_env = os.environ.get("PATH", "")
        m = re.search(r"OpenAI\.Codex_([0-9.]+)_x64", path_env, re.IGNORECASE)
        if m:
            installed_version = m.group(1)
        else:
            app_dir = self.localappdata / "OpenAI" / "Codex"
            if app_dir.is_dir():
                installed_version = "installed (MSIX bundle)"
            else:
                installed_version = None

        if not installed_version:
            health = "NOT_INSTALLED"
            status = "UNKNOWN"
        else:
            status = "UNKNOWN"

        return ToolCheckResult(
            name=name,
            installed_version=installed_version,
            install_method=install_method,
            latest_version=latest_version,
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
            runtime_type="app_package",
        )

    # 4. OpenCodex Proxy / Shims Summary Component
    def check_opencodex_proxy_shims(self, diag: OpenCodexDiagnostics) -> ToolCheckResult:
        name = "OpenCodex proxy/shims"
        if diag.proxy_state in ("HEALTHY", "REPORTED_HEALTHY"):
            installed_version = "active"
        elif diag.proxy_state == "UNHEALTHY":
            installed_version = "inactive"
        else:
            installed_version = "unverified"
        install_method = "npm bin wrappers + local background service"
        latest_version = "n/a"
        latest_source = "local service / shim alignment"
        if diag.proxy_state == "UNHEALTHY":
            health = "DEGRADED"
        elif diag.health == "HEALTHY" and diag.proxy_state == "HEALTHY":
            health = "HEALTHY"
        else:
            health = "UNVERIFIED"
        status = "CURRENT"
        attention: List[str] = []

        if diag.proxy_state == "UNHEALTHY":
            status = "WATCH"
            attention.append("OpenCodex background proxy is not reachable on port 10100")
        elif diag.proxy_state != "HEALTHY":
            status = "WATCH"
            attention.append(f"OpenCodex proxy health is not directly verified ({diag.proxy_summary})")

        if not diag.shim_aligned:
            status = "WATCH"
            attention.append("Codex autostart shim is bypassed by standard npm wrapper (run 'ocx codex-shim install' to re-engage)")

        return ToolCheckResult(
            name=name,
            installed_version=installed_version,
            install_method=install_method,
            latest_version=latest_version,
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
            runtime_type="system",
        )

    # 5. Workstation Ops / MCP
    def check_workstation_ops(self) -> ToolCheckResult:
        name = "Workstation Ops / MCP"
        installed_version = None
        configured_path = os.environ.get("WUP_WORKSTATION_OPS_PATH", "")
        install_method = "optional local repository (set WUP_WORKSTATION_OPS_PATH)"
        latest_version = "unknown"
        latest_source = "local project repository (no remote registry channel declared)"
        health = "HEALTHY"
        status = "UNKNOWN"
        attention: List[str] = []

        mcp_path = Path(configured_path) if configured_path else None
        if mcp_path is None:
            return ToolCheckResult(name=name, installed_version="unknown", install_method=install_method, latest_version=latest_version, latest_source=latest_source, status=status, health="UNVERIFIED", attention_notes=["Workstation Ops path is not configured"])
        pkg_path = mcp_path / "package.json"
        pkg_data = safe_read_json(pkg_path)
        if pkg_data and "version" in pkg_data:
            installed_version = str(pkg_data["version"])
        else:
            installed_version = "unknown"
            status = "UNKNOWN"
            health = "DEGRADED"
            attention.append("Workstation Ops package.json not accessible")

        node_mods = mcp_path / "node_modules"
        if not node_mods.is_dir():
            health = "DEGRADED"
            status = "WATCH"
            attention.append("workstation-ops-mcp node_modules missing; build dependencies require npm install")

        return ToolCheckResult(
            name=name,
            installed_version=installed_version,
            install_method=install_method,
            latest_version=latest_version,
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
            runtime_type="managed_mcp",
        )

    # 6. System Node.js & npm
    def check_system_node(self) -> ToolCheckResult:
        name = "Node.js"
        installed_version = None
        node_bin = shutil.which("node") or "C:\\Program Files\\nodejs\\node.exe"
        install_method = f"System ({node_bin})"
        latest_version = None
        latest_source = "nodejs.org release index"
        health = "HEALTHY"
        attention: List[str] = []

        rc, stdout, _ = self.run_cmd(["node", "-v"], 2.0)
        if rc == 0 and stdout:
            installed_version = stdout.lstrip("v")
        else:
            health = "NOT_INSTALLED"
            return ToolCheckResult(
                name=name,
                installed_version=None,
                install_method=install_method,
                latest_version=None,
                latest_source=latest_source,
                status="UNKNOWN",
                health=health,
                attention_notes=["Node.js binary not found in PATH"],
            )

        if "codex" in node_bin.lower() or "appdata" in node_bin.lower():
            attention.append(f"Warning: system node resolves to bundled runtime path: {node_bin}")

        dist_data = self.fetcher.fetch_json(REMOTE_VERSION_SOURCES["Node.js"])
        if dist_data and isinstance(dist_data, list):
            valid_releases = []
            for item in dist_data:
                ver = item.get("version", "").lstrip("v")
                if ver and "-" not in ver:
                    valid_releases.append(item)

            if valid_releases:
                inst_sem = SemVer.parse(installed_version)
                matching_release = None
                if inst_sem and len(inst_sem.parts) > 0:
                    inst_major = inst_sem.parts[0]
                    for r in valid_releases:
                        r_sem = SemVer.parse(r.get("version", "").lstrip("v"))
                        if r_sem and r_sem.parts[0] == inst_major:
                            matching_release = r.get("version", "").lstrip("v")
                            break

                latest_lts_item = next((r for r in valid_releases if r.get("lts")), None)
                latest_lts = latest_lts_item.get("version", "").lstrip("v") if latest_lts_item else None
                latest_current = valid_releases[0].get("version", "").lstrip("v")

                if matching_release:
                    latest_version = matching_release
                    latest_source = f"nodejs.org release index (Node {inst_sem.parts[0]} line)"
                elif latest_lts:
                    latest_version = latest_lts
                    latest_source = f"nodejs.org release index (LTS {latest_lts_item.get('lts')})"
                else:
                    latest_version = latest_current
                    latest_source = "nodejs.org release index (current)"
        else:
            latest_source = "nodejs.org (unreachable)"
            latest_version = None

        v_inst = SemVer.parse(installed_version)
        v_lat = SemVer.parse(latest_version)

        if not v_lat:
            status = "UNKNOWN"
        elif v_inst and v_lat and v_inst.compare(v_lat) < 0:
            status = "WATCH"
            attention.append(f"Newer Node.js {latest_version} available; system currently runs {installed_version}")
        else:
            status = "CURRENT"

        return ToolCheckResult(
            name=name,
            installed_version=installed_version,
            install_method=install_method,
            latest_version=latest_version or "unknown",
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
        )

    def check_system_npm(self) -> ToolCheckResult:
        name = "npm"
        installed_version = None
        npm_bin = shutil.which("npm") or "npm"
        install_method = f"System ({npm_bin})"
        latest_version = None
        latest_source = "npm registry (npm)"
        health = "HEALTHY"
        attention: List[str] = []

        rc, stdout, _ = self.run_cmd(["npm", "-v"], 2.0)
        if rc == 0 and stdout:
            installed_version = stdout.strip()
        else:
            health = "NOT_INSTALLED"
            return ToolCheckResult(
                name=name,
                installed_version=None,
                install_method=install_method,
                latest_version=None,
                latest_source=latest_source,
                status="UNKNOWN",
                health=health,
                attention_notes=["npm executable not found"],
            )

        latest_data = self.fetcher.fetch_json(REMOTE_VERSION_SOURCES["npm"])
        if latest_data and isinstance(latest_data, dict) and "version" in latest_data:
            latest_version = str(latest_data["version"])
        else:
            latest_source = "npm registry (unreachable)"
            latest_version = None

        v_inst = SemVer.parse(installed_version)
        v_lat = SemVer.parse(latest_version)

        if not v_lat:
            status = "UNKNOWN"
        elif v_inst and v_lat and v_inst.compare(v_lat) < 0:
            status = "WATCH"
        else:
            status = "CURRENT"

        return ToolCheckResult(
            name=name,
            installed_version=installed_version,
            install_method=install_method,
            latest_version=latest_version or "unknown",
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
        )

    # 7. System Bun vs OpenCodex Bundled Bun
    def check_bun(self) -> Tuple[ToolCheckResult, Optional[ToolCheckResult]]:
        bun_bin = shutil.which("bun")
        sys_result: ToolCheckResult

        rel_data = self.fetcher.fetch_json(REMOTE_VERSION_SOURCES["Bun"])
        latest_bun = None
        latest_source = "GitHub releases (oven-sh/bun)"
        if rel_data and isinstance(rel_data, dict) and "tag_name" in rel_data:
            m = re.search(r"(\d+\.\d+\.\d+)", str(rel_data["tag_name"]))
            if m:
                latest_bun = m.group(1)
        else:
            latest_source = "GitHub releases (unreachable)"

        if bun_bin:
            rc, stdout, _ = self.run_cmd(["bun", "-v"], 2.0)
            ver = stdout.strip() if rc == 0 and stdout else None
            if not ver:
                sys_result = ToolCheckResult(
                    name="Bun (System)",
                    installed_version="unverified",
                    install_method=f"System ({bun_bin})",
                    latest_version=latest_bun or "unknown",
                    latest_source=latest_source,
                    status="UNKNOWN",
                    health="UNVERIFIED",
                    attention_notes=["System Bun binary execution failed or timed out"],
                    runtime_type="system",
                )
            elif not latest_bun:
                sys_result = ToolCheckResult(
                    name="Bun (System)",
                    installed_version=ver,
                    install_method=f"System ({bun_bin})",
                    latest_version="unknown",
                    latest_source=latest_source,
                    status="UNKNOWN",
                    health="HEALTHY",
                    runtime_type="system",
                )
            else:
                v_inst = SemVer.parse(ver)
                v_lat = SemVer.parse(latest_bun)
                status = "UPDATE" if (v_inst and v_lat and v_inst.compare(v_lat) < 0) else "CURRENT"
                sys_result = ToolCheckResult(
                    name="Bun (System)",
                    installed_version=ver,
                    install_method=f"System ({bun_bin})",
                    latest_version=latest_bun,
                    latest_source=latest_source,
                    status=status,
                    health="HEALTHY",
                    runtime_type="system",
                )
        else:
            if not latest_bun:
                sys_result = ToolCheckResult(
                    name="Bun (System)",
                    installed_version="not installed",
                    install_method="none",
                    latest_version="unknown",
                    latest_source=latest_source,
                    status="UNKNOWN",
                    health="NOT_INSTALLED",
                    runtime_type="system",
                )
            else:
                sys_result = ToolCheckResult(
                    name="Bun (System)",
                    installed_version="not installed",
                    install_method="none",
                    latest_version=latest_bun,
                    latest_source=latest_source,
                    status="WATCH",
                    health="HEALTHY",
                    attention_notes=["System Bun is not installed (optional, OpenCodex uses bundled Bun)"],
                    runtime_type="system",
                )

        bundled_bun = self.npm_global_root / "@bitkyc08" / "opencodex" / "node_modules" / "bun" / "bin" / "bun.exe"
        bundled_result: Optional[ToolCheckResult] = None
        if bundled_bun.is_file():
            rc, stdout, _ = self.run_cmd([str(bundled_bun), "-v"], 2.0)
            b_ver = None
            if rc == 0 and stdout:
                m = re.search(r"(\d+\.\d+\.\d+)", stdout)
                if m:
                    b_ver = m.group(1)

            if b_ver:
                bundled_result = ToolCheckResult(
                    name="Bun (OpenCodex bundled)",
                    installed_version=b_ver,
                    install_method=f"Bundled ({bundled_bun})",
                    latest_version="managed by OpenCodex",
                    latest_source="OpenCodex package dependency",
                    status="CURRENT",
                    health="HEALTHY",
                    is_bundled=True,
                    runtime_type="bundled",
                )

            else:
                bundled_result = ToolCheckResult(
                    name="Bun (OpenCodex bundled)",
                    installed_version="unknown",
                    install_method=f"Bundled ({bundled_bun})",
                    latest_version="managed by OpenCodex",
                    latest_source="OpenCodex package dependency",
                    status="UNKNOWN",
                    health="UNVERIFIED",
                    attention_notes=["Bundled Bun binary present but execution or version check failed"],
                    is_bundled=True,
                    runtime_type="bundled",
                )

        if not bun_bin and bundled_result and bundled_result.health == "HEALTHY":
            sys_result.status = "UNKNOWN"
            sys_result.health = "NOT_INSTALLED"
            sys_result.install_method = "none (optional; bundled Bun in use)"
            sys_result.attention_notes = []

        return sys_result, bundled_result

    # 8. System Python
    def check_system_python(self) -> ToolCheckResult:
        name = "Python"
        py_bin = shutil.which("python") or sys.executable
        install_method = f"System ({py_bin})"
        installed_version = None
        latest_version = None
        latest_source = "docs.python.org versioned documentation"
        health = "HEALTHY"
        attention: List[str] = []

        rc_py, out_py, _ = self.run_cmd([py_bin, "-V"], 2.0)
        if rc_py == 0 and out_py:
            m = re.search(r"(\d+\.\d+\.\d+)", out_py)
            if m:
                installed_version = m.group(1)

        if not installed_version:
            health = "UNVERIFIED"
            self.observer_degraded = True
            return ToolCheckResult(
                name=name,
                installed_version="unverified",
                install_method=install_method,
                latest_version="unknown",
                latest_source=latest_source,
                status="UNKNOWN",
                health=health,
                attention_notes=["Python executable -V execution failed or timed out"],
            )

        if "codex-runtimes" in py_bin.lower():
            attention.append(f"Warning: active python resolves to bundled cache runtime: {py_bin}")

        inst_sem = SemVer.parse(installed_version)
        cycle = None
        if inst_sem and len(inst_sem.parts) >= 2:
            cycle = f"{inst_sem.parts[0]}.{inst_sem.parts[1]}"

        docs_url = f"https://docs.python.org/{cycle}/" if cycle else None
        docs_text = self.fetcher.fetch_text(docs_url) if docs_url else None
        if docs_text and cycle:
            match = re.search(
                r"(?:Python\s+)?(\d+\.\d+\.\d+)\s+[Dd]ocumentation",
                docs_text,
            )
            if match and match.group(1).startswith(f"{cycle}."):
                latest_version = match.group(1)
                latest_source = f"docs.python.org/{cycle} official documentation"
        if not latest_version:
            latest_source = f"docs.python.org/{cycle} (unreachable or unparseable)" if cycle else "docs.python.org (cycle unavailable)"

        v_inst = SemVer.parse(installed_version)
        v_lat = SemVer.parse(latest_version)

        if not v_lat:
            status = "UNKNOWN"
        elif v_inst and v_lat and v_inst.compare(v_lat) < 0:
            status = "WATCH"
            attention.append(f"Python cycle update {latest_version} available (current: {installed_version})")
        else:
            status = "CURRENT"

        return ToolCheckResult(
            name=name,
            installed_version=installed_version,
            install_method=install_method,
            latest_version=latest_version or "unknown",
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
        )

    # 9. Git
    def check_git(self) -> ToolCheckResult:
        name = "Git"
        git_bin = shutil.which("git") or "C:\\Program Files\\Git\\cmd\\git.exe"
        install_method = f"System ({git_bin})"
        installed_version = None
        latest_version = None
        latest_source = "GitHub releases (git-for-windows/git)"
        health = "HEALTHY"
        attention: List[str] = []

        rc, stdout, _ = self.run_cmd(["git", "--version"], 2.0)
        if rc == 0 and stdout:
            m = re.search(r"(\d+\.\d+\.\d+)", stdout)
            if m:
                installed_version = m.group(1)
        else:
            health = "NOT_INSTALLED"
            return ToolCheckResult(
                name=name,
                installed_version=None,
                install_method=install_method,
                latest_version=None,
                latest_source=latest_source,
                status="UNKNOWN",
                health=health,
                attention_notes=["Git executable not found"],
            )

        if "codex-runtimes" in git_bin.lower():
            attention.append(f"Warning: active git points to bundled runtime: {git_bin}")

        rel_data = self.fetcher.fetch_json(REMOTE_VERSION_SOURCES["Git"])
        if rel_data and isinstance(rel_data, dict) and "tag_name" in rel_data:
            tag = rel_data["tag_name"].lstrip("v")
            m = re.search(r"(\d+\.\d+\.\d+)", tag)
            if m:
                latest_version = m.group(1)
        else:
            latest_source = "GitHub releases (unreachable)"
            latest_version = None

        v_inst = SemVer.parse(installed_version)
        v_lat = SemVer.parse(latest_version)

        if not v_lat:
            status = "UNKNOWN"
        elif v_inst and v_lat and v_inst.compare(v_lat) < 0:
            status = "WATCH"
        else:
            status = "CURRENT"

        return ToolCheckResult(
            name=name,
            installed_version=installed_version,
            install_method=install_method,
            latest_version=latest_version or "unknown",
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
        )

    # 10. LM Studio
    def check_lm_studio(self) -> ToolCheckResult:
        name = "LM Studio"
        installed_version = None
        install_method = "Desktop application + CLI (lms)"
        latest_version = "unknown"
        latest_source = "Vendor update channel"
        health = "HEALTHY"
        status = "UNKNOWN"
        attention: List[str] = []

        vinfo_path = self.user_home / ".lmstudio" / ".internal" / "historical-version-info.json"
        vinfo_data = safe_read_json(vinfo_path)
        if vinfo_data and "targetHistories" in vinfo_data:
            for t in vinfo_data["targetHistories"]:
                if t.get("target") == "lmstudio" and t.get("lastRecordedAppVersion"):
                    installed_version = t["lastRecordedAppVersion"]
                    break

        if not installed_version:
            rc, stdout, _ = self.run_cmd(["lms", "version"], 2.0)
            if rc == 0 and stdout:
                m = re.search(r"(\d+\.\d+\.\d+)", stdout)
                if m:
                    installed_version = m.group(1)
                elif "CLI commit" in stdout:
                    installed_version = "installed (CLI commit detected)"

        server_live = False
        try:
            req = urllib.request.Request("http://127.0.0.1:1234/v1/models", headers={"User-Agent": "ToolchainWatch"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    server_live = True
        except Exception:
            server_live = False

        if not server_live:
            health = "DEGRADED"
            attention.append("LM Studio local inference server is not active on port 1234")

        return ToolCheckResult(
            name=name,
            installed_version=installed_version or "installed",
            install_method=install_method,
            latest_version=latest_version,
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
        )

    # 11. Wrangler
    def check_wrangler(self) -> ToolCheckResult:
        name = "Wrangler"
        installed_version = None
        install_method = "npm (wrangler)"
        latest_version = None
        latest_source = "npm registry (wrangler)"
        health = "HEALTHY"
        attention: List[str] = []
        rec: Optional[UpdateRecommendation] = None

        pkg_path = self.npm_global_root / "wrangler" / "package.json"
        pkg_data = safe_read_json(pkg_path)
        if pkg_data and "version" in pkg_data:
            installed_version = str(pkg_data["version"])
        else:
            rc, stdout, _ = self.run_cmd(["wrangler", "--version"], 2.0)
            if rc == 0 and stdout:
                m = re.search(r"(\d+\.\d+\.\d+)", stdout)
                if m:
                    installed_version = m.group(1)

        if not installed_version:
            return ToolCheckResult(
                name=name,
                installed_version=None,
                install_method="unknown",
                latest_version=None,
                latest_source=latest_source,
                status="UNKNOWN",
                health="NOT_INSTALLED",
                attention_notes=["Wrangler package not found in global npm root"],
            )

        latest_data = self.fetcher.fetch_json(REMOTE_VERSION_SOURCES["Wrangler"])
        if latest_data and isinstance(latest_data, dict) and "version" in latest_data:
            latest_version = str(latest_data["version"])
        else:
            latest_source = "npm registry (unreachable)"
            latest_version = None

        v_inst = SemVer.parse(installed_version)
        v_lat = SemVer.parse(latest_version)

        if not v_lat:
            status = "UNKNOWN"
        elif v_inst and v_lat and v_inst.compare(v_lat) < 0:
            status = "UPDATE"
            rec = UpdateRecommendation(
                why=f"Newer stable release {latest_version} available (current: {installed_version})",
                breaking_relevance="Review Cloudflare Workers CLI changelog before upgrading.",
                install_method=install_method,
                proposed_command=f"npm install -g wrangler@{latest_version}",
                rollback_command=f"npm install -g wrangler@{installed_version}",
                validation_checks=["wrangler --version"],
            )
        else:
            status = "CURRENT"

        return ToolCheckResult(
            name=name,
            installed_version=installed_version,
            install_method=install_method,
            latest_version=latest_version or "unknown",
            latest_source=latest_source,
            status=status,
            health=health,
            attention_notes=attention,
            update_recommendation=rec,
        )

    @staticmethod
    def release_url_for(result: ToolCheckResult) -> Optional[str]:
        """Return only deterministic, authoritative release metadata for actionable findings."""
        if result.status not in ("URGENT", "UPDATE", "WATCH"):
            return None
        version = result.latest_version
        parsed = SemVer.parse(version)
        if not parsed or not version:
            return None
        normalized = ".".join(str(part) for part in parsed.parts[:3])
        package_pages = {
            "Codex CLI": "@openai/codex",
            "OpenCodex": "@bitkyc08/opencodex",
            "Wrangler": "wrangler",
        }
        if result.name in package_pages:
            return f"https://www.npmjs.com/package/{package_pages[result.name]}/v/{normalized}"
        if result.name == "Node.js":
            if "nodejs.org release index" in result.latest_source:
                return f"https://nodejs.org/en/blog/release/v{normalized}"
            return f"https://nodejs.org/dist/v{normalized}/"
        if result.name == "Python" and len(parsed.parts) >= 2:
            return f"https://www.python.org/downloads/release/python-{normalized.replace('.', '')}/"
        if result.name == "npm":
            if "npm registry" in result.latest_source:
                return f"https://github.com/npm/cli/releases/tag/v{normalized}"
            return "https://www.npmjs.com/package/npm"
        if result.name == "Git":
            return "https://github.com/git-for-windows/git/releases"
        return None

    # Full audit runner
    def audit_all(self) -> ToolchainAuditReport:
        now = datetime.datetime.now(datetime.timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        timestamp_str = now.isoformat()

        tools: List[ToolCheckResult] = []

        ocx_res, ocx_diag = self.check_opencodex()
        codex_res = self.check_codex_cli(ocx_diag=ocx_diag)
        desktop_res = self.check_codex_desktop()
        shims_res = self.check_opencodex_proxy_shims(ocx_diag)
        mcp_res = self.check_workstation_ops()
        node_res = run_local_probe(self, "system_node")
        npm_res = run_local_probe(self, "system_npm")
        bun_sys, bun_bundled = self.check_bun()
        python_res = run_local_probe(self, "system_python")
        git_res = run_local_probe(self, "git")
        lms_res = run_local_probe(self, "lm_studio")
        wrangler_res = run_local_probe(self, "wrangler")

        tools.append(codex_res)
        tools.append(ocx_res)
        tools.append(desktop_res)
        tools.append(shims_res)
        tools.append(mcp_res)
        tools.append(node_res)
        tools.append(npm_res)
        tools.append(bun_sys)
        if bun_bundled:
            tools.append(bun_bundled)
        tools.append(python_res)
        tools.append(git_res)
        tools.append(lms_res)
        tools.append(wrangler_res)

        enabled = {name.strip() for name in os.environ.get("WUP_ENABLED_TOOLS", "").split(",") if name.strip()}
        if enabled:
            tools = [tool for tool in tools if tool.name in enabled]

        for tool in tools:
            tool.release_url = self.release_url_for(tool)

        core_health = "HEALTHY"
        if codex_res.health == "DEGRADED" or ocx_res.health == "DEGRADED" or shims_res.health == "DEGRADED":
            core_health = "DEGRADED"
        elif codex_res.health == "UNVERIFIED" or ocx_res.health == "UNVERIFIED":
            core_health = "UNVERIFIED"

        attention: List[str] = []
        for t in tools:
            for note in t.attention_notes:
                attention.append(f"[{t.name}] {note}")

        recommendations: List[str] = []
        for t in tools:
            if t.status in ("UPDATE", "URGENT") and t.update_recommendation:
                rec = t.update_recommendation
                recommendations.append(
                    f"{t.name}: {rec.proposed_command} (Reason: {rec.why}; Precaution: {rec.breaking_relevance})"
                )

        observer_status = "DEGRADED" if self.observer_degraded else "NORMAL"

        if not recommendations:
            unresolved = (
                observer_status != "NORMAL"
                or core_health != "HEALTHY"
                or bool(attention)
                or any(t.status in ("WATCH", "UPDATE", "URGENT", "UNKNOWN") for t in tools)
                or any(t.health in ("DEGRADED", "UNVERIFIED") for t in tools)
            )
            if unresolved:
                recommendations.append("No immediate update recommended; unresolved toolchain findings remain.")
            else:
                recommendations.append("No immediate updates required. Workstation toolchain is stable.")

        return ToolchainAuditReport(
            timestamp=timestamp_str,
            date=date_str,
            core_stack_health=core_health,
            tools=tools,
            attention=attention,
            recommended_actions=recommendations,
            observer_path_status=observer_status,
        )


# These probes are independent installed-version checks.  The more tightly
# coupled Codex/OpenCodex and Bun checks retain their existing direct paths.
LocalProbeHandler = Callable[[ToolchainAuditor], ToolCheckResult]
LOCAL_PROBE_HANDLERS: Dict[str, LocalProbeHandler] = {
    "system_node": ToolchainAuditor.check_system_node,
    "system_npm": ToolchainAuditor.check_system_npm,
    "system_python": ToolchainAuditor.check_system_python,
    "git": ToolchainAuditor.check_git,
    "lm_studio": ToolchainAuditor.check_lm_studio,
    "wrangler": ToolchainAuditor.check_wrangler,
}


def run_local_probe(auditor: ToolchainAuditor, probe_key: str) -> ToolCheckResult:
    """Run one supported local installed-version probe from the fixed registry."""
    handler = LOCAL_PROBE_HANDLERS.get(probe_key)
    if handler is None:
        raise ValueError(f"unsupported local probe: {probe_key}")
    return handler(auditor)


@dataclass(frozen=True)
class MonitoredToolEntry:
    name: str
    local_probe: Optional[str] = None
    remote_provider: Optional[str] = None
    local_exempt_reason: Optional[str] = None
    remote_exempt_reason: Optional[str] = None


MONITORED_TOOL_CATALOG: Tuple[MonitoredToolEntry, ...] = (
    MonitoredToolEntry("Codex CLI", local_probe="check_codex_cli", remote_provider="Codex CLI"),
    MonitoredToolEntry("OpenCodex", local_probe="check_opencodex", remote_provider="OpenCodex"),
    MonitoredToolEntry("Codex Desktop", local_probe="check_codex_desktop", remote_exempt_reason="no_public_registry"),
    MonitoredToolEntry("OpenCodex Proxy & Shims", local_probe="check_opencodex_proxy_shims", remote_exempt_reason="local_daemon_and_shims"),
    MonitoredToolEntry("Workstation Ops / MCP", local_probe="check_workstation_ops", remote_exempt_reason="local_repository_only"),
    MonitoredToolEntry("Node.js", local_probe="system_node", remote_provider="Node.js"),
    MonitoredToolEntry("npm", local_probe="system_npm", remote_provider="npm"),
    MonitoredToolEntry("Bun", local_probe="check_bun", remote_provider="Bun"),
    MonitoredToolEntry("Python", local_probe="system_python", remote_exempt_reason="docs_cycle_only"),
    MonitoredToolEntry("Git", local_probe="git", remote_provider="Git"),
    MonitoredToolEntry("LM Studio", local_probe="lm_studio", remote_exempt_reason="vendor_cli_only"),
    MonitoredToolEntry("Wrangler", local_probe="wrangler", remote_provider="Wrangler"),
)


def validate_catalog_consistency(
    catalog: Tuple[MonitoredToolEntry, ...] = MONITORED_TOOL_CATALOG,
    remote_registry: Tuple[RemoteToolProvider, ...] = REMOTE_TOOL_PROVIDER_REGISTRY,
    local_handlers: Dict[str, LocalProbeHandler] = LOCAL_PROBE_HANDLERS,
    auditor_cls: Any = ToolchainAuditor,
) -> None:
    """Verify that monitored tool catalog, remote providers, and local probes do not drift."""
    seen_names = set()
    catalog_remote_providers = set()
    catalog_local_probes = set()

    for entry in catalog:
        if not entry.name or not entry.name.strip():
            raise ValueError("monitored tool catalog contains an empty tool name")
        if entry.name in seen_names:
            raise ValueError(f"monitored tool catalog contains duplicate tool name: {entry.name}")
        seen_names.add(entry.name)

        if entry.local_probe is not None:
            if not entry.local_probe.strip():
                raise ValueError(f"catalog entry '{entry.name}' has an empty local probe reference")
            has_handler = entry.local_probe in local_handlers
            has_auditor_method = hasattr(auditor_cls, entry.local_probe) and callable(getattr(auditor_cls, entry.local_probe))
            if not (has_handler or has_auditor_method):
                raise ValueError(f"catalog entry '{entry.name}' references unknown local probe: {entry.local_probe}")
            catalog_local_probes.add(entry.local_probe)
        else:
            if not entry.local_exempt_reason or not entry.local_exempt_reason.strip():
                raise ValueError(f"required monitored tool '{entry.name}' is missing local probe coverage without an explicit exemption")

        remote_provider_names = {provider.name for provider in remote_registry}
        if entry.remote_provider is not None:
            if not entry.remote_provider.strip():
                raise ValueError(f"catalog entry '{entry.name}' has an empty remote provider reference")
            if entry.remote_provider not in remote_provider_names:
                raise ValueError(f"catalog entry '{entry.name}' references unknown remote provider: {entry.remote_provider}")
            catalog_remote_providers.add(entry.remote_provider)
        else:
            if not entry.remote_exempt_reason or not entry.remote_exempt_reason.strip():
                raise ValueError(f"required monitored tool '{entry.name}' is missing remote provider coverage without an explicit exemption")

    for provider in remote_registry:
        if provider.name not in catalog_remote_providers:
            raise ValueError(f"remote provider registry contains provider '{provider.name}' not associated with any catalog entry")

    for probe_key in local_handlers:
        if probe_key not in catalog_local_probes:
            raise ValueError(f"local probe registry contains probe '{probe_key}' not associated with any catalog entry")


validate_catalog_consistency()


# ---------------------------------------------------------------------------
# Output Formatters
# ---------------------------------------------------------------------------

def format_text_report(report: ToolchainAuditReport) -> str:
    lines: List[str] = []
    lines.append(f"Daily Toolchain Watch - {report.date}")
    lines.append("")

    lines.append(f"{'Tool':<26} {'Installed':<15} {'Latest':<15} {'Status':<10}")
    lines.append("-" * 68)

    for t in report.tools:
        inst = t.installed_version or "not found"
        lat = t.latest_version or "unknown"
        lines.append(f"{t.name:<26} {inst:<15} {lat:<15} {t.status:<10}")

    lines.append("")
    lines.append(f"Core stack health:")
    lines.append(report.core_stack_health)
    lines.append("")

    lines.append("Attention:")
    if report.attention:
        for item in report.attention:
            lines.append(f"* {item}")
    else:
        lines.append("* All inspected components are in nominal operating state.")
    lines.append("")

    lines.append("Recommended action:")
    for action in report.recommended_actions:
        lines.append(f"* {action}")
    lines.append("")

    updates_present = [t for t in report.tools if t.status in ("UPDATE", "URGENT") and t.update_recommendation]
    if updates_present:
        lines.append("Update Details (Read-Only Proposal):")
        for t in updates_present:
            rec = t.update_recommendation
            if not rec:
                continue
            lines.append(f"--- {t.name} Update Plan ---")
            lines.append(f"  Why worthwhile:        {rec.why}")
            lines.append(f"  Compatibility note:    {rec.breaking_relevance}")
            lines.append(f"  Detected method:       {rec.install_method}")
            lines.append(f"  Proposed update cmd:   {rec.proposed_command}")
            lines.append(f"  Rollback command:      {rec.rollback_command}")
            lines.append(f"  Validation checks:")
            for chk in rec.validation_checks:
                lines.append(f"    - {chk}")
            lines.append("")

    return redact_secrets("\n".join(lines))


def format_json_report(report: ToolchainAuditReport) -> str:
    data = asdict(report)
    json_str = json.dumps(data, indent=2)
    return redact_secrets(json_str)


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Daily Toolchain Watch V1")
    parser.add_argument("--json", action="store_true", help="Emit report in machine-readable JSON format")
    parser.add_argument("--offline", action="store_true", help="Force offline mode (skip all network requests)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Network timeout in seconds (default 3.0)")
    parser.add_argument("--mock-data", type=str, default=None, help="Path to JSON file with mocked network responses")
    args = parser.parse_args()

    mock_data = None
    if args.mock_data:
        try:
            with open(args.mock_data, "r", encoding="utf-8") as f:
                mock_data = json.load(f)
        except Exception as e:
            print(f"Error loading mock data: {e}", file=sys.stderr)
            return 1

    fetcher = NetworkFetcher(offline=args.offline, timeout=args.timeout, mock_data=mock_data)
    auditor = ToolchainAuditor(fetcher=fetcher)
    report = auditor.audit_all()

    if args.json:
        print(format_json_report(report))
    else:
        print(format_text_report(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
