# WUP project context

## Purpose

WUP is a reusable read-only developer toolchain monitor: local installed
version/runtime audit plus optional remote upstream-release awareness.

## Architecture

`check_toolchain.py` audits local tools. `run_notifier.py` filters/deduplicates
actionable findings and calls optional transports. `workstation_snapshot.py`
derives a minimal installed-version snapshot from the same successful audit and
can publish it best-effort with the scheduled user's authenticated GitHub CLI.
`remote_version_sentinel.py` observes public versions and enriches genuine
upstream-change Telegram messages with a valid snapshot.

## State, configuration, and security

Local dedupe state is outside Git (default `%LOCALAPPDATA%\WUP`). The isolated
`toolchain-remote-state` branch holds only `remote-version-state.json` and
`workstation-snapshot.json`, never implementation source or credentials.
`wup.toml` is non-secret configuration. Telegram credentials are environment
variables; email is an optional external command. Snapshot publishing requires
explicit repository identity and existing GitHub CLI authentication.

## Decisions and non-goals

WUP is read-only, Windows scheduler-first, and has no daemon/service. The
local audit remains authoritative; remote monitoring is upstream-only. WUP is
not a package manager, fleet inventory, generic notification inbox, credential
store, or automatic updater. GitHub Actions scheduling/secrets/state activation
is a reviewed cutover concern, not part of Pass A.
