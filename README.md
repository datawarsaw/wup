# Toolchain Update Watch

Toolchain Update Watch is a self-contained, read-only workstation audit. It checks the core AI development toolchain, reports installed and latest stable versions where a primary source is available, and can send changed actionable findings to Telegram and Cloudflare Email. It never installs, upgrades, restarts, reconfigures, or otherwise changes the monitored toolchain.

## What it monitors

The audit covers Codex CLI, OpenCodex and its proxy/shims, Codex Desktop, Workstation Ops MCP, system Node.js/npm, system and bundled Bun, system Python, Git, LM Studio, and Wrangler. The detailed detection rules and authoritative release sources are in [references/managed-tools.md](./references/managed-tools.md).

## Finding semantics

| Status | Meaning |
| --- | --- |
| `CURRENT` | The installed version matches the appropriate verified stable version and health checks pass. |
| `WATCH` | Attention is warranted, but an automatic recommendation is deliberately held: for example a minor delta, supported older LTS, or Codex/OpenCodex coupling risk. |
| `UPDATE` | A newer stable release is verified from a primary source and a manual update is recommended. |
| `URGENT` | A critical update or a broken component needs immediate manual attention. |
| `UNKNOWN` | The latest version or local state could not be observed. This is never treated as `CURRENT`. |

Health (`HEALTHY`, `DEGRADED`, `UNVERIFIED`, or `NOT_INSTALLED`) is separate from the update status. Observer limitations, such as a sandbox restriction, are reported as unverified rather than as a workstation failure.

## Run an audit

From the `code-skills` repository root:

```powershell
python toolchain-update-watch/scripts/check_toolchain.py
python toolchain-update-watch/scripts/check_toolchain.py --json
python toolchain-update-watch/scripts/check_toolchain.py --offline
```

The notifier reruns the JSON audit, filters to `URGENT`, `UPDATE`, and `WATCH`, and only alerts on new or materially changed findings:

```powershell
python toolchain-update-watch/scripts/run_notifier.py --dry-run
python toolchain-update-watch/scripts/run_notifier.py
```

`--dry-run` renders both notification payloads without sending an alert or writing state. Use it before enabling any external scheduler.

## Alerts and deduplication

The notifier sends the same changed finding set to:

- Telegram, through the sibling [`telegram-notify`](../telegram-notify/) component.
- Cloudflare Email, through the built helper at `C:\AI\workstation-ops-mcp\dist\cloudflare-email-cli.js` by default. Set `WORKSTATION_OPS_EMAIL_HELPER` only when the helper is built elsewhere.

The notifier saves successful alert signatures at `%LOCALAPPDATA%\WhiteGull\toolchain-update-watch\last-alerted.json`. It alerts only for a new finding or a changed status/version/health signature. If Telegram succeeds and email fails, it records pending Telegram delivery while retaining the last fully successful state, so a retry does not duplicate Telegram but still retries email.

Email includes an authoritative release, package, or changelog link when the audit can derive one deterministically from its verified release source. Node.js, Python, and npm prefer their direct version-specific release content; package or download pages are retained only as deterministic fallbacks. These links are informational and are not part of the deduplication signature, so a corrected link alone never creates another alert. Telegram remains compact and does not include per-tool links.

## Remote Version Sentinel

The GitHub Actions **Remote Version Sentinel** observes public upstream releases even while this PC is off. It tracks Codex CLI, OpenCodex, Node.js, npm, Git, Wrangler, and Bun using the same authoritative public source definitions as the local checker. It reports upstream release changes only; it never claims an installed workstation version, runtime health, proxy/shim status, or LM Studio state.

The workflow runs best-effort at 06:30 UTC (about 08:30 CEST / 07:30 CET) and can start late under GitHub scheduling. Its machine-managed `toolchain-remote-state` branch contains only `remote-version-state.json`, never implementation source, secrets, or local runtime state. The first successful run creates a silent baseline. Later runs commit only version changes; resolver failures preserve the last good version and are visible in workflow output.

## Local Snapshot Bridge V1

The local watcher remains the authoritative workstation audit. Pass A can build a sanitized `workstation-snapshot.json` from that successful audit's installed-version results for Codex CLI, OpenCodex, Node.js, npm, Git, Wrangler, and Bun. The optional bridge publishes only that file to `toolchain-remote-state` through the current user's authenticated GitHub CLI; it retries a content conflict once and never replaces the branch tree. Snapshot publishing is best-effort and is disabled unless explicitly activated after review. It cannot change local notifier delivery or deduplication.

Remote alerts may use a valid snapshot only as context: `Last locally observed` plus its measurement timestamp/age. A valid stale snapshot still enriches the alert; a partial snapshot enriches matching tools and leaves the rest upstream-only. Missing or malformed snapshots use the full V0 upstream-only fallback. Snapshot freshness never suppresses a genuine release alert, and a snapshot update never triggers one.

Telegram delivery uses only GitHub Actions secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Secrets are not enrolled by this repository. A manual `workflow_dispatch` run can set `send_test_notification` to send one labelled connectivity test without observing or changing state.

## Configuration and secrets

This repository contains no alert credentials or runtime state. Telegram configuration belongs to `telegram-notify`'s local secret boundary. Cloudflare Email is owned by Workstation Ops: its API token may come from the current process environment or its Windows DPAPI CurrentUser fallback; account ID, sender, and fixed recipient belong in local Workstation Ops configuration. The helper does not accept a recipient override.

The default state is outside Git, under `%LOCALAPPDATA%`. Do not commit it, `.env` files, Cloudflare credentials, or Telegram credentials.

## Windows scheduled task

The component manages one same-user Windows task, **WhiteGull Toolchain Update Watch**. It runs daily at 08:00 local time, starts once at the next availability after a missed run, may wake the PC, ignores a new trigger while a prior run is active, and has a 20-minute execution limit. It is interactive-user and non-elevated by design; after a full reboot it runs after that user next logs in.

Install or update it from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File toolchain-update-watch/scripts/manage-scheduled-task.ps1 -Action Install
```

Inspect, trigger, validate the same-user Cloudflare context, or remove it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File toolchain-update-watch/scripts/manage-scheduled-task.ps1 -Action Status
powershell -NoProfile -ExecutionPolicy Bypass -File toolchain-update-watch/scripts/manage-scheduled-task.ps1 -Action Run
powershell -NoProfile -ExecutionPolicy Bypass -File toolchain-update-watch/scripts/manage-scheduled-task.ps1 -Action ValidateContext
powershell -NoProfile -ExecutionPolicy Bypass -File toolchain-update-watch/scripts/manage-scheduled-task.ps1 -Action Remove
```

Task actions contain executable and repository paths only—never credentials. Telegram reads its same-user local secret file. Cloudflare Email reads its Workstation Ops DPAPI/configuration boundary. The task invokes the normal notifier; it never updates packages.

## Troubleshooting

- Run `check_toolchain.py --offline` to distinguish local observation from network lookups.
- `UNKNOWN` means the tool did not obtain trustworthy evidence; inspect the individual tool notes instead of assuming the workstation is current.
- Verify `C:\AI\workstation-ops-mcp\dist\cloudflare-email-cli.js` exists after building Workstation Ops. Override its location with `WORKSTATION_OPS_EMAIL_HELPER` if needed.
- Run `node telegram-notify/scripts/self-test.mjs` from the repository root to validate Telegram configuration without sending a normal lifecycle notification.
- Use `ValidateContext` after changing Workstation Ops Cloudflare configuration; it creates and removes a short-lived same-user validation task without sending email or Telegram.
- If Task Scheduler reports a nonzero last result, run the notifier with `--dry-run` and check that Python, Node, Workstation Ops, and Telegram local configuration are available to the same Windows user.
- Inspect `%LOCALAPPDATA%\WhiteGull\toolchain-update-watch\last-alerted.json` only to diagnose deduplication; deleting it causes the next actionable findings to be treated as new.

## Guarantee

This component is an observer and notifier only. Every proposed update remains a manual decision. It has no auto-update path.
