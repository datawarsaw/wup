# WUP project context

## Purpose and architecture

WUP is a reusable read-only developer toolchain monitor. The local watcher
audits installed versions/runtime health and deduplicates actionable findings.
The manual-only GitHub Actions sentinel observes public upstream releases.

`workstation_snapshot.py` derives a minimal snapshot from the already successful
local audit. It best-effort publishes that JSON to the private
`WUP_WORKSTATION_SNAPSHOT` Actions secret using the scheduled user's existing
authenticated `gh` CLI and stdin. The public `toolchain-remote-state` branch
contains only `remote-version-state.json`, the upstream release dedupe authority.

## State and security

Local dedupe state is outside Git (default `%LOCALAPPDATA%\WUP`). Its version-2
`last-alerted.json` schema remains compatible with the prior deployment. The
snapshot schema is unchanged and contains only version, measured_at, tool names,
and installed_version. It is never committed to the public repository.

`wup.toml` is non-secret. Telegram credentials are process environment values
or a narrow configured env file limited to the two Telegram keys. Email is an
optional external-command adapter. Scheduled task actions pass an explicit WUP
config path and contain no credentials.

## Decisions and non-goals

WUP is Windows scheduler-first, has no daemon/service, creates no PAT, and
never updates packages. Public state does not carry local workstation facts.
Enabling schedules, setting secrets, migrating state, and replacing the prior
deployment are reviewed cutover operations outside Pass B1.
