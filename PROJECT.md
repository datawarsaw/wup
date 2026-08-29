# Toolchain Update Watch — Project Context

## Purpose and scope

Toolchain Update Watch provides a durable, read-only daily audit for the AI workstation toolchain and reports changed actionable findings. It is a component inside the Agent Platform `code-skills` repository, not a standalone repository or an update manager.

## Architecture and data flow

`scripts/check_toolchain.py` observes installed tools, local health, and primary release channels, then emits a text or JSON report. `scripts/run_notifier.py` consumes the JSON report, keeps only `URGENT`, `UPDATE`, and `WATCH` findings, compares their status/version/health signatures with local alert state, and delivers only changed findings. It uses atomic state replacement after a fully successful delivery.

## Components and dependencies

- This component: audit policy, checker, notifier, and deterministic Python tests.
- `../telegram-notify`: Telegram delivery and its local secret boundary.
- `C:\AI\workstation-ops-mcp`: Cloudflare Email helper (`dist/cloudflare-email-cli.js`) for a fixed configured recipient.

The helper path may be overridden with `WORKSTATION_OPS_EMAIL_HELPER`; the default is intentionally explicit for the current workstation layout.

## Notification architecture

Telegram and Cloudflare Email receive the same changed actionable findings. Email additionally renders deterministic authoritative release metadata when it is available; Telegram remains compact. Release URLs are informational and excluded from the deduplication signature. Telegram is attempted first. If email fails after Telegram succeeds, the notifier records the Telegram signatures as pending while retaining the previous full-success state; a later retry avoids duplicate Telegram delivery and retries email. An audit failure can emit a Telegram failure signal, but does not overwrite good deduplication state.

GitHub Actions also runs a separate Remote Version Sentinel: public upstream resolver -> `toolchain-remote-state` branch -> compact Telegram upstream-release notice. It has no workstation observation and does not use the local notifier, Cloudflare Email, or local deduplication state. Its first successful observation is silent; only version changes are notified and committed. GitHub Actions secrets are the only permitted remote Telegram configuration.

## Runtime state

Runtime deduplication state is `%LOCALAPPDATA%\WhiteGull\toolchain-update-watch\last-alerted.json`. It is machine-local operational data, not Git-tracked project state. The component owns a small PowerShell manager for the same-user `WhiteGull Toolchain Update Watch` Windows Scheduled Task; its registered definition contains only executable and repository paths, never credentials.

## Security boundaries

The component must not print credentials, OAuth/session content, or external alert configuration. Telegram secrets are owned by the `telegram-notify` local configuration. Cloudflare tokens/account IDs are external environment values; sender and recipient settings belong to local Workstation Ops configuration or environment overrides. No recipient can be supplied to the helper command.

## Source of truth

Git code and durable documentation are the implementation and architecture truth. Linear `MIC-86` and child issues are the backlog, roadmap, priority, and execution-state truth. Runtime state describes only machine-local notification deduplication. Chat history is not required to understand or operate this component.

## Design decisions

- Read-only is absolute: reports may propose exact manual commands but never execute them.
- Primary-source lookup failures yield `UNKNOWN`, never a false `CURRENT` verdict.
- Codex/OpenCodex coupling can hold a nominal update at `WATCH` when shim, proxy, or version evidence is unsafe.
- Notification deduplication is based on a small semantic signature rather than report timestamps.
- Windows Task Scheduler drives the notifier daily at 08:00: scheduler -> audit/deduplication -> Telegram and Cloudflare Email. The task uses `StartWhenAvailable`, wake-to-run, a bounded execution time, and ignores overlapping instances.
- External notification configuration keeps secrets and workstation policy out of Git. Telegram remains a same-user local secret; Cloudflare token is Workstation Ops CurrentUser DPAPI and account/email configuration remains Workstation Ops local config.

## Non-goals

It is not a package manager, fleet inventory, background service, credential store, Cloudflare routing/DNS manager, or a replacement for Linear.

## Current maturity

The audit and notifier have deterministic unit coverage for classification, redaction, offline/observer handling, dry-run behavior, changed-finding delivery, and partial notification failures. Scheduler management is a reproducible same-user Windows capability and is validated through Task Scheduler itself.
