# Managed Toolchain Baseline & Audit Policy

## 1. Scope & Objective

The Daily Toolchain Watch provides a deterministic, strictly read-only audit of the AI workstation toolchain. It answers:

> Are the important AI workstation tools installed, healthy, and on the latest appropriate stable version?

This capability is NOT a general Windows package inventory or an autonomous updater. It adheres strictly to the observation-only principle: no automatic installations, updates, restarts, or configuration mutations.

---

## 2. Priority Order & Managed Tools

### 1. Codex CLI
- **Component**: Official OpenAI Codex command-line interface.
- **Install Method**: Global npm package (`@openai/codex`) or standalone binary in PATH.
- **Installed Version Detection**: Package metadata at `%APPDATA%\npm\node_modules\@openai\codex\package.json` or `codex --version`.
- **Health Verification**: `codex doctor --json` runtime checks (executable resolution, disk space, install consistency). Note that sandbox-induced auth/reachability warnings in offline environments are classified as observer limitations, not host failures.
- **Latest Stable Source**: Primary: npm registry (`https://registry.npmjs.org/@openai/codex/latest`). Authoritative local cache fallback: `~/.codex/version.json` (tool-native update metadata). If network is unreachable and cache is unavailable, classify latest as `UNKNOWN`.
- **Coupling & OpenCodex Compatibility**: Codex CLI and OpenCodex are tightly coupled. Upgrading Codex CLI overwrites npm wrappers (`codex.cmd`, `codex.ps1`), which deactivates the OpenCodex autostart shim until re-installed. When OpenCodex shim is unverified or bypassed, hold Codex CLI update as `WATCH` rather than immediately recommending update.
- **Proposed Update Command**: `npm install -g @openai/codex@<version>`
- **Rollback Command**: `npm install -g @openai/codex@<installed_version>`
- **Validation Steps**: Run `codex --version`, `codex doctor`, and verify OpenCodex proxy/shim status.

### 2. OpenCodex
- **Component**: Multi-provider proxy and shim wrapper for Codex CLI.
- **Install Method**: Global npm package (`@bitkyc08/opencodex`).
- **Installed Version Detection**: Package metadata at `%APPDATA%\npm\node_modules\@bitkyc08\opencodex\package.json` or `ocx --version`.
- **Health Verification**: HTTP probe to `http://127.0.0.1:10100/healthz` and `ocx health`.
- **Shim Verification**: Verify whether `%APPDATA%\npm\codex.opencodex-real.cmd` exists and whether `%APPDATA%\npm\codex.cmd` acts as an active OpenCodex shim or has reverted to a standard npm wrapper.
- **Latest Stable Source**: npm registry (`https://registry.npmjs.org/@bitkyc08/opencodex/latest`). If unreachable, classify latest as `UNKNOWN`.
- **Proposed Update Command**: `npm install -g @bitkyc08/opencodex@<version>`
- **Rollback Command**: `npm install -g @bitkyc08/opencodex@<installed_version>`
- **Validation Steps**: Verify `ocx --version`, `ocx health`, `ocx status`, and `http://127.0.0.1:10100/healthz`.

### 3. Codex Desktop
- **Component**: OpenAI Codex Desktop application (packaged Windows app / MSIX).
- **Install Method**: Windows App package (`OpenAI.Codex_*`) registered in PATH and `%LOCALAPPDATA%\OpenAI\Codex`.
- **Installed Version Detection**: Parsed from the active WindowsApps resource path in PATH (e.g. `OpenAI.Codex_26.818.5229.0_x64__...` -> version `26.818.5229.0`) or executable version metadata.
- **Latest Stable Source**: No public unauthenticated registry feed exists for Microsoft Store packages.
- **Classification**: Always classify as `UNKNOWN` for latest version (Do NOT guess). Status remains `UNKNOWN`.

### 4. OpenCodex Proxy & Shims
- **Component**: OpenCodex local background service and autostart CLI wrapper.
- **Inspection**:
  - Proxy process listening on port 10100.
  - Shim wrapper files: `codex.cmd`, `codex.ps1`, `codex` vs `codex.opencodex-real.*`.
- **Classification**:
  - `CURRENT` if proxy is live and shims are aligned.
  - `WATCH` if proxy is running but shims are in bypass mode (e.g. after a Codex npm install), or if proxy is offline.

### 5. Workstation Ops / MCP
- **Component**: Local workstation operations MCP server.
- **Install Method**: Local repository at `C:\AI\workstation-ops-mcp`.
- **Installed Version Detection**: Read from `C:\AI\workstation-ops-mcp\package.json`.
- **Latest Version Channel**: No external release registry declared (`n/a`).
- **Classification**: `CURRENT` when local worktree and dependencies are healthy; `WATCH` if build or dependencies require manual attention.

### 6. System Node.js & npm
- **Component**: System-wide JavaScript runtime and package manager.
- **Separation Rule**: Must remain distinct from bundled runtimes (e.g. Codex desktop bundled Node or CUA node).
- **Executable Resolution**: Checked at `C:\Program Files\nodejs\node.exe` and `npm.cmd`.
- **Installed Version**: `node -v` and `npm -v`.
- **Latest Stable Source**: Node.js distribution index (`https://nodejs.org/dist/index.json`) for active LTS/Current; npm registry for npm. If unreachable: `UNKNOWN`.

### 7. System Bun vs OpenCodex Bundled Bun
- **Component**: Bun JavaScript/TypeScript runtime.
- **Separation Rule**:
  - **System Bun**: Resolves from system PATH. If not installed in PATH: if latest is unavailable, classify `UNKNOWN`; if latest is verified, classify `WATCH`. Never false `CURRENT`.
  - **OpenCodex Bundled Bun**: Resolves inside OpenCodex node_modules (`%APPDATA%\npm\node_modules\@bitkyc08\opencodex\node_modules\bun\bin\bun.exe`). If command fails, report `UNKNOWN`/unverified without inventing versions.

### 8. System Python
- **Component**: System Python interpreter.
- **Separation Rule**: Must remain distinct from application-bundled Python runtimes.
- **Executable Resolution**: Evaluates resolved system executable (`python -V`), not the auditor process's `sys.version_info`.
- **Latest Stable Source**: `https://endoflife.date/api/python.json` or `python.org`. If unreachable: `UNKNOWN`.

### 9. Git
- **Component**: System Git for Windows.
- **Executable Resolution**: `C:\Program Files\Git\cmd\git.exe`.
- **Installed Version**: `git --version`.
- **Latest Stable Source**: GitHub releases (`git-for-windows/git`). If unreachable: `UNKNOWN`.

### 10. LM Studio
- **Component**: Local LLM inference server and developer CLI (`lms`).
- **Installed Version Detection**: `~/.lmstudio/.internal/historical-version-info.json` or `lms` CLI output.
- **Health Verification**: Local server endpoint probe at `http://127.0.0.1:1234/v1/models`. Healthy server operation is nominal and not reported as daily attention noise.
- **Latest Stable Source**: Vendor channel. If unauthenticated/unreachable: `UNKNOWN`.

### 11. Wrangler
- **Component**: Cloudflare Workers CLI.
- **Installed Version Detection**: `%APPDATA%\npm\node_modules\wrangler\package.json` or `wrangler --version`.
- **Latest Stable Source**: npm registry (`https://registry.npmjs.org/wrangler/latest`). If unreachable: `UNKNOWN`.

---

## 3. Classification Framework

Each tool is classified as exactly one of:

| Status | Definition |
|---|---|
| **CURRENT** | Installed version is verified identical to the latest appropriate stable release, and health checks pass. |
| **WATCH** | Installed version is acceptable / minor revision behind, an older active LTS line, or an operational hold applies (e.g. OpenCodex shim bypass, coupled update hold). |
| **UPDATE** | A newer stable release is verified available from primary sources and recommended for upgrade. |
| **URGENT** | A verified critical update is available (security patch) or package failure requires replacement. |
| **UNKNOWN** | Latest version cannot be determined (e.g., network failure, lack of public feed, offline run) OR installed version cannot be observed. **Never default to CURRENT on lookup failure.** |

*Note: Component health (HEALTHY, DEGRADED, UNVERIFIED, NOT_INSTALLED) is evaluated separately from tool update status.*

---

## 4. Observer Failure vs Host Failure

- **Observer Path Degraded**: When a check encounters sandbox restrictions (e.g. EPERM on user-restricted files, TLS certificate boundaries inside offline sandboxes, dumb terminal warnings), record the observer path as degraded or unverified.
- **Host State Integrity**: Never report a healthy workstation tool as broken because the auditor lacked observation permissions. Use `UNVERIFIED` or `UNKNOWN`.

---

## 5. Security & Secret Redaction

The toolchain auditor must never print or log:
- API keys (e.g. `sk-...`, bearer tokens)
- OAuth tokens or contents of `auth.json`
- Password hashes or secrets in configuration files
All output must be deterministic and secret-safe.
