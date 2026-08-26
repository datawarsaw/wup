---
name: toolchain-update-watch
description: Read-only daily audit of AI workstation toolchain health, installed versions, and latest stable releases across Codex, OpenCodex, runtimes, and local AI tooling.
metadata:
  short-description: Audit workstation toolchain versions and health
---

# Daily Toolchain Watch

Perform deterministic, strictly read-only audits of the core AI workstation toolchain. Determine whether essential coding tools, runtimes, and proxies are installed, operational, and aligned with appropriate latest stable releases.

## Core Principles

1. **Strictly Read-Only:** Never automatically install packages, upgrade dependencies, rewrite configs, modify shims, restart services, or push/merge git changes.
2. **Coupled Component Awareness:** Treat Codex CLI and OpenCodex as coupled components. An available Codex upgrade is not automatically recommended if it would invalidate OpenCodex autostart shims or break proxy routing.
3. **No Guessing / Authoritative Lookups:** Use primary sources (npm registry, official GitHub releases, official vendor channels, or local tool-native version cache). Network or lookup failures must classify as \`UNKNOWN\`, never false \`CURRENT\`.
4. **Observer Resilience:** Differentiate observer limitations (e.g. sandbox permissions, dumb terminal, offline certificate boundaries) from actual workstation host failures.
5. **Secret Redaction:** Never output tokens, OAuth sessions, or credentials.

---

## Operating Workflow

### 1. Execute Audit
Run the standard library Python audit script:

\`\`\`bash
# Standard human-readable summary
python toolchain-update-watch/scripts/check_toolchain.py

# Machine-readable JSON output
python toolchain-update-watch/scripts/check_toolchain.py --json

# Offline mode (skips network fetches, tests disconnected resilience)
python toolchain-update-watch/scripts/check_toolchain.py --offline
\`\`\`

### 2. Inspect Priority Components
Audited in explicit priority order:
1. **Codex CLI**: \`@openai/codex\` version, \`codex doctor\` health, local version cache.
2. **OpenCodex**: \`@bitkyc08/opencodex\` version, \`http://127.0.0.1:10100/healthz\` proxy probe.
3. **Codex Desktop**: Windows App package (MSIX) version discovery. Latest is classified \`UNKNOWN\`.
4. **OpenCodex Proxy & Shims**: Port 10100 service health and npm wrapper vs shim alignment (\`codex.cmd\` vs \`codex.opencodex-real.cmd\`).
5. **Workstation Ops / MCP**: Local \`C:\\AI\\workstation-ops-mcp\` repository state.
6. **System Node.js & npm**: Distinguish system runtimes from bundled runtimes.
7. **Bun**: System Bun (PATH) vs OpenCodex bundled Bun (\`bun.exe\`).
8. **System Python**: System Python interpreter vs bundled cache Python.
9. **Git**: System Git vs bundled cache Git.
10. **LM Studio**: Local server status (\`http://127.0.0.1:1234/v1/models\`) and CLI commit/version.
11. **Wrangler**: Cloudflare Workers CLI global npm package.

For detailed criteria and detection paths, see [references/managed-tools.md](./references/managed-tools.md).

### 3. Classification Framework

Each tool receives exactly one status:
- **CURRENT**: Installed version matches latest stable release; runtime health is verified.
- **WATCH**: Minor revision delta, older supported LTS, or operational note (e.g. shim bypass, port warning).
- **UPDATE**: Newer stable release verified from primary source and recommended.
- **URGENT**: Critical update available (security fix) or broken component requiring immediate fix.
- **UNKNOWN**: Latest version lookup failed (network unreachable / no public feed) or local observation was unavailable.

### 4. Bounded Update Proposals
When a tool is classified as \`UPDATE\` or \`URGENT\`:
- State why the update is worthwhile.
- Explain breaking change and compatibility relevance (especially OpenCodex shim/proxy alignment).
- Provide the exact proposed command (e.g. \`npm install -g @openai/codex@0.149.1\`).
- Provide the exact rollback command.
- Provide post-update validation checks (e.g. \`codex --version\`, \`ocx codex-shim status\`, healthz probe).
- **Do not execute the update command autonomously.**

