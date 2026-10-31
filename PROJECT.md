# WUP project context

## Purpose and architecture

WUP is a reusable, local, on-demand developer toolchain monitor. The audit in
`check_toolchain.py` observes installed versions/runtime health and public
latest-release sources. Supporting commands provide diagnostics, update plans,
explicit updates for supported npm-managed tools, local history, and a local
HTML status report.

Every command runs in the foreground only when the user invokes it. No WUP
daemon, scheduler, notifier, remote sentinel, or hosted component exists.

## State and security

Local change history is outside Git (default `%LOCALAPPDATA%\WUP`). WUP does
not use former alert-deduplication state or remote state.

`wup.toml` is non-secret and contains only `[tools]` and `[local]` settings.
Legacy `[notifications]` and `[remote]` sections are ignored.

## Decisions and non-goals

WUP is local-first and on-demand. It has no daemon, scheduler, notification
channel, hosted service, or remote state. `update_apply.py` is the only
execution surface and is explicit, foreground-only, allowlisted, and never
invoked by WUP automatically.
