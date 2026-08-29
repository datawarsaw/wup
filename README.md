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

## Configuration and secrets

This repository contains no alert credentials or runtime state. Telegram configuration belongs to `telegram-notify`'s local secret boundary. Cloudflare Email reads its API token and account ID from the existing external `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` environment values; its configured sender and fixed recipient come from local Workstation Ops configuration or `WORKSTATION_OPS_EMAIL_FROM` / `WORKSTATION_OPS_EMAIL_TO`. The helper does not accept a recipient override.

The default state is outside Git, under `%LOCALAPPDATA%`. Do not commit it, `.env` files, Cloudflare credentials, or Telegram credentials.

## Scheduler status

No scheduler configuration is tracked or managed by this component. Run the notifier manually until a workstation owner creates an external Windows Task Scheduler task. That task should invoke the built notifier, inherit the required external Telegram/Cloudflare configuration, and be tested first with `--dry-run`.

## Troubleshooting

- Run `check_toolchain.py --offline` to distinguish local observation from network lookups.
- `UNKNOWN` means the tool did not obtain trustworthy evidence; inspect the individual tool notes instead of assuming the workstation is current.
- Verify `C:\AI\workstation-ops-mcp\dist\cloudflare-email-cli.js` exists after building Workstation Ops. Override its location with `WORKSTATION_OPS_EMAIL_HELPER` if needed.
- Run `node telegram-notify/scripts/self-test.mjs` from the repository root to validate Telegram configuration without sending a normal lifecycle notification.
- Inspect `%LOCALAPPDATA%\WhiteGull\toolchain-update-watch\last-alerted.json` only to diagnose deduplication; deleting it causes the next actionable findings to be treated as new.

## Guarantee

This component is an observer and notifier only. Every proposed update remains a manual decision. It has no auto-update path.
