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
- **Installed Version Detection**: Always resolve the active executable and execute `codex --version`; this is authoritative. Package metadata at `%APPDATA%\npm\node_modules\@openai\codex\package.json` is supporting evidence only, and a mismatch prevents a clean `CURRENT`/`UPDATE` verdict.
- **Health Verification**: `codex doctor --json` runtime checks (executable resolution, disk space, install consistency). Note that sandbox-induced auth/reachability warnings in offline environments are classified as observer limitations, not host failures.
- **Latest Stable Source**: Primary: npm registry (`https://registry.npmjs.org/@openai/codex/latest`). `~/.codex/version.json` is informational only and never substitutes for a failed live lookup. If the registry is unreachable, classify latest as `UNKNOWN`.
- **Coupling & OpenCodex Compatibility**: Codex CLI and OpenCodex are tightly coupled. Upgrading Codex CLI overwrites npm wrappers (`codex.cmd`, `codex.ps1`), which deactivates the OpenCodex autostart shim until re-installed. When OpenCodex shim/health evidence is unverified or bypassed, or `ocx status` detects a different Codex version than the active CLI, hold an available Codex CLI update as `WATCH` rather than immediately recommending it.
- **Proposed Update Command**: `npm install -g @openai/codex@<version>`
- **Rollback Command**: `npm install -g @openai/codex@<installed_version>`
- **Validation Steps**: Run `codex --version`, `codex doctor`, and verify OpenCodex proxy/shim status.

### 2. OpenCodex
- **Component**: Multi-provider proxy and shim wrapper for Codex CLI.
- **Install Method**: Global npm package (`@bitkyc08/opencodex`).
- **Installed Version Detection**: Package metadata at `%APPDATA%\npm\node_modules\@bitkyc08\opencodex\package.json` or `ocx --version`.
- **Health Verification**: Keep a successful direct HTTP probe to `http://127.0.0.1:10100/healthz` distinct from `ocx health` reporting healthy. A blocked, failed, or malformed required diagnostic leaves the affected evidence `UNVERIFIED` and the observer path degraded; it does not prove a host failure or a direct healthz success.
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
  - `CURRENT` if the direct proxy probe is verified and shims are aligned.
  - `WATCH` if proxy evidence is unverified, shims are in bypass mode (e.g. after a Codex npm install), or the proxy is explicitly reported unhealthy.

### 5. Workstation Ops / MCP
- **Component**: Local workstation operations MCP server.
- **Install Method**: Optional local repository configured by `WUP_WORKSTATION_OPS_PATH`.
- **Installed Version Detection**: Read from that configured repository's `package.json` when available.
- **Latest Version Channel**: No reliable external release registry is declared, so latest remains `UNKNOWN`.
- **Classification**: Report local health separately. Use `UNKNOWN` without a reliable latest channel, or `WATCH` for a verified local health concern.

### 6. System Node.js & npm
- **Component**: System-wide JavaScript runtime and package manager.
- **Separation Rule**: Must remain distinct from bundled runtimes (e.g. Codex desktop bundled Node or CUA node).
- **Executable Resolution**: Checked at `C:\Program Files\nodejs\node.exe` and `npm.cmd`.
- **Installed Version**: `node -v` and `npm -v`.
- **Latest Stable Source**: Node.js distribution index (`https://nodejs.org/dist/index.json`) for active LTS/Current; npm registry for npm. If unreachable: `UNKNOWN`.

### 7. System Bun vs OpenCodex Bundled Bun
- **Component**: Bun JavaScript/TypeScript runtime.
- **Separation Rule**:
  - **System Bun**: Resolves from system PATH. When it is absent but a healthy OpenCodex-bundled Bun is in use and no system Bun requirement exists, retain the system row as non-actionable `UNKNOWN`/`NOT_INSTALLED` without recurring attention. Never label an absent system runtime `CURRENT`.
  - **OpenCodex Bundled Bun**: Resolves inside OpenCodex node_modules (`%APPDATA%\npm\node_modules\@bitkyc08\opencodex\node_modules\bun\bin\bun.exe`). If command fails, report `UNKNOWN`/unverified without inventing versions.

### 8. System Python
- **Component**: System Python interpreter.
- **Separation Rule**: Must remain distinct from application-bundled Python runtimes.
- **Executable Resolution**: Evaluates resolved system executable (`python -V`), not the auditor process's `sys.version_info`.
- **Latest Stable Source**: The official versioned documentation at `https://docs.python.org/<major.minor>/`, parsed for that maintained cycle's published patch release. If unreachable or unparseable: `UNKNOWN`.

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
