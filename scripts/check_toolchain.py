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

        # HTTP healthz probe
        probe_live = False
        try:
            req = urllib.request.Request("http://127.0.0.1:10100/healthz", headers={"User-Agent": "ToolchainWatch"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") == "ok":
                        probe_live = True
        except Exception:
            probe_live = False

        if probe_live or (rc_h == 0 and "Proxy healthy" in out_h):
            diag.proxy_running = True
            diag.proxy_summary = "running (port 10100, healthz verified)"
        else:
            diag.proxy_running = False
            diag.proxy_summary = "unreachable (port 10100)"
            health = "DEGRADED"
            attention.append("OpenCodex local proxy is unreachable on port 10100")

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
            if rc_s != 0:
                self.observer_degraded = True
                diag.warnings.append("ocx status command failed or was restricted in sandbox")

        combined_err = f"{err_h} {err_s}"
        if "EPERM" in combined_err or "permission denied" in combined_err.lower():
            self.observer_degraded = True
            diag.warnings.append("Sandbox permission boundary encountered for user-restricted files (.opencodex auth/lock)")

        if not installed_version:
            return ToolCheckResult(
                name=name,
                installed_version=None,
                install_method="unknown",
                latest_version=None,
                latest_source=latest_source,
                status="UNKNOWN",
                health="NOT_INSTALLED",
                attention_notes=["OpenCodex package or CLI not found"],
            ), diag

        latest_data = self.fetcher.fetch_json("https://registry.npmjs.org/@bitkyc08/opencodex/latest")
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
        install_method = "unknown"
        latest_version = None
        latest_source = "npm registry (@openai/codex)"
        health = "HEALTHY"
        attention: List[str] = []
        rec: Optional[UpdateRecommendation] = None

        pkg_path = self.npm_global_root / "@openai" / "codex" / "package.json"
        pkg_data = safe_read_json(pkg_path)
        if pkg_data and "version" in pkg_data:
            installed_version = str(pkg_data["version"])
            install_method = "npm (@openai/codex)"
        else:
            codex_bin = shutil.which("codex")
            if codex_bin:
                if "WindowsApps" in codex_bin:
                    install_method = f"Windows App package ({codex_bin})"
                else:
                    install_method = f"binary ({codex_bin})"
            rc, stdout, _ = self.run_cmd(["codex", "--version"], 2.0)
            if rc == 0 and stdout:
                m = re.search(r"(\d+\.\d+\.\d+)", stdout)
                if m:
                    installed_version = m.group(1)

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
            return ToolCheckResult(
                name=name,
                installed_version=None,
                install_method=install_method,
                latest_version=None,
                latest_source=latest_source,
                status="UNKNOWN",
                health="NOT_INSTALLED",
                attention_notes=["Codex CLI executable or package not found"],
            )

        latest_data = self.fetcher.fetch_json("https://registry.npmjs.org/@openai/codex/latest")
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

        if not v_lat:
            status = "UNKNOWN"
        elif v_inst and v_lat and v_inst.compare(v_lat) < 0:
            ocx_risky = False
            risk_reasons: List[str] = []
            if not ocx_diag:
                ocx_risky = True
                risk_reasons.append("OpenCodex diagnostics unavailable")
            else:
                if not ocx_diag.proxy_running:
                    ocx_risky = True
                    risk_reasons.append("OpenCodex proxy is not running")
                if not ocx_diag.shim_aligned:
                    ocx_risky = True
                    risk_reasons.append("OpenCodex autostart shim is currently bypassed")
                if ocx_diag.health != "HEALTHY":
                    ocx_risky = True
                    risk_reasons.append(f"OpenCodex health is {ocx_diag.health}")

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
        installed_version = "active" if diag.proxy_running else "inactive"
        install_method = "npm bin wrappers + local background service"
        latest_version = "n/a"
        latest_source = "local service / shim alignment"
        health = "HEALTHY" if diag.proxy_running else "DEGRADED"
        status = "CURRENT"
        attention: List[str] = []

        if not diag.proxy_running:
            status = "WATCH"
            attention.append("OpenCodex background proxy is not reachable on port 10100")

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
        install_method = "local repository (C:\\AI\\workstation-ops-mcp)"
        latest_version = "unknown"
        latest_source = "local project repository (no remote registry channel declared)"
        health = "HEALTHY"
        status = "UNKNOWN"
        attention: List[str] = []

        mcp_path = Path("C:/AI/workstation-ops-mcp")
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

        dist_data = self.fetcher.fetch_json("https://nodejs.org/dist/index.json")
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

        latest_data = self.fetcher.fetch_json("https://registry.npmjs.org/npm/latest")
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

        rel_data = self.fetcher.fetch_json("https://api.github.com/repos/oven-sh/bun/releases/latest")
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
                    health="HEALTHY",
                    attention_notes=["System Bun is not installed; latest version unavailable"],
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

        rel_data = self.fetcher.fetch_json("https://api.github.com/repos/git-for-windows/git/releases/latest")
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

        latest_data = self.fetcher.fetch_json("https://registry.npmjs.org/wrangler/latest")
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
        node_res = self.check_system_node()
        npm_res = self.check_system_npm()
        bun_sys, bun_bundled = self.check_bun()
        python_res = self.check_system_python()
        git_res = self.check_git()
        lms_res = self.check_lm_studio()
        wrangler_res = self.check_wrangler()

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

        if not recommendations:
            recommendations.append("No immediate updates required. Workstation toolchain is stable.")

        observer_status = "DEGRADED" if self.observer_degraded else "NORMAL"

        return ToolchainAuditReport(
            timestamp=timestamp_str,
            date=date_str,
            core_stack_health=core_health,
            tools=tools,
            attention=attention,
            recommended_actions=recommendations,
            observer_path_status=observer_status,
        )


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
