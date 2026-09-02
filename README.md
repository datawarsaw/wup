<p align="center">
  <img src="docs/wup-logo.jpg" width="720" alt="WUP logo" />
</p>

<h1 align="center">WUP</h1>

<p align="center">
  <img src="https://github.com/datawarsaw/wup/actions/workflows/ci.yml/badge.svg" alt="CI" />
  <img src="https://img.shields.io/github/license/datawarsaw/wup" alt="License" />
  <img src="https://img.shields.io/badge/platform-Windows%20first-0078D4" alt="Windows first" />
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB" alt="Python 3.10+" />
</p>

<p align="center"><strong>Know when your developer toolchain needs attention — locally, and optionally while your workstation is off.</strong></p>

WUP is a small, local-first developer toolchain watcher. It audits installed tools and runtime health, classifies meaningful version changes, deduplicates alerts, and can notify through Telegram or an optional email adapter. Its optional GitHub Actions sentinel watches public upstream releases without pretending it can inspect an offline workstation.

WUP is read-only by default: it does not install, upgrade, restart, or reconfigure the software it monitors.

## Why WUP

Developer machines accumulate CLIs, runtimes, package managers and local services that drift at different speeds. WUP keeps the boring part visible without becoming another always-on platform:

- local audit of installed versions and selected health signals;
- deterministic `CURRENT`, `WATCH`, `UPDATE`, `URGENT`, and `UNKNOWN` semantics;
- alert deduplication so unchanged findings stay quiet;
- Telegram notifications with optional external email delivery;
- remote upstream release awareness through GitHub Actions;
- an optional private workstation snapshot that can enrich remote alerts with the last locally observed version and age.

There is no WUP-hosted account system, VPS, daemon, database, or control plane required.

## What it monitors

The current Windows-first catalog includes probes for tools such as Codex CLI, OpenCodex, Codex Desktop, Node.js/npm, Bun, Python, Git, LM Studio and Wrangler, plus selected local integration/runtime checks. Detection rules and authoritative upstream sources are documented in [`references/managed-tools.md`](references/managed-tools.md).

The catalog is intentionally conservative: if WUP cannot obtain trustworthy evidence, it reports `UNKNOWN` rather than inventing a clean bill of health.

## How it works

```mermaid
flowchart LR
    A[Local workstation] --> B[WUP audit]
    B --> C[Installed versions + health]
    B --> D[Local dedupe]
    D --> E[Telegram]
    D --> F[Optional email adapter]
    B --> G[Sanitized workstation snapshot]
    H[GitHub Actions] --> I[Public upstream releases]
    I --> J[Remote version state]
    G -. WUP_WORKSTATION_SNAPSHOT .-> H
    J --> E
```

The local watcher is authoritative for what is installed. The remote sentinel is authoritative only for what upstream has released. A stale workstation snapshot is shown as stale context; it is never presented as a fresh inspection.

## Quick start (Windows)

```powershell
git clone https://github.com/datawarsaw/wup.git
cd wup
Copy-Item wup.example.toml wup.toml
python -m unittest discover -s tests -p 'test_*.py'
python scripts/check_toolchain.py --offline
python scripts/run_notifier.py --dry-run --config wup.toml
```

Python 3.10+ is required. Node.js is needed only for Node-based checks or an external email adapter.

`--dry-run` renders notifications without sending them or advancing deduplication state. Use it before enabling any scheduler or external notification channel.

## Diagnostics (Doctor)

Run the strictly read-only diagnostics to verify repository integrity, configuration validity, runtime prerequisites, and state readability without making any changes:

```powershell
python scripts/doctor.py
python scripts/doctor.py --json
```

`doctor.py` is strictly non-mutating: it never creates, edits, or deletes configuration, state files, or scheduled tasks.
Its default output is human-readable; use `--json` for deterministic local automation output. Exit code `0` means healthy or optional-only degradation, `1` means an unhealthy required check, and `2` means no report could be produced. Credentials and configuration values are never printed.

## Setup preflight

Check whether this machine is ready to activate WUP without installing, repairing, configuring, or activating anything:

```powershell
python scripts/preflight.py
python scripts/preflight.py --json
```

The preflight is a read-only projection of the existing doctor checks: required unhealthy checks are blockers, while optional degraded checks remain warnings. Exit code `0` means `READY`, `1` means `NOT_READY`, and `2` means a report could not be produced. JSON output includes only check names and safe messages; it never prints credentials, configuration values, or diagnostic details.

## Update Plan

Generate a deterministic, descriptive update plan from audit results without executing any updates:

```powershell
python scripts/check_toolchain.py --json | python scripts/update_plan.py
python scripts/update_plan.py --input-report <path-to-report.json> --json
```

`update_plan.py` is strictly descriptive and read-only: it describes known update state using existing WUP findings, performs no installation or mutation, and never executes planned commands.

The PLAN also records a declarative update mechanism, automation mode, inert planned instruction, and post-update verification guidance where the repository has an unambiguous mechanism. `AUTOMATABLE` is limited to supported npm-managed tools; Codex CLI falls back to `MANUAL` or `UNKNOWN` for Windows App/MSIX, binary, metadata-only, or otherwise ambiguous installations. Unsupported tools remain `UNKNOWN` without guessed commands. These fields are data only; PLAN never executes them and provides no APPLY operation.

## Status snapshot

`status_snapshot.py` is a pure projection for a future local static report. It consumes an already-produced audit report and exposes only a tool's name, installed/latest versions, status, health, existing release/docs URL, and fixed provenance (`LOCAL` for installed version, `REMOTE` for latest version). It does not run probes, use the network, read or write state, or retain diagnostics, environment data, credentials, or raw provider payloads. WUP currently has only an audit-report timestamp; the snapshot preserves that explicitly as `audit_report_timestamp` and reports independent per-tool local and remote observation timestamps as JSON `null` / text `unknown` rather than fabricating freshness.

## Local change history

Record an existing JSON audit report in a local, append-only JSONL history:

```powershell
python scripts/check_toolchain.py --json | python scripts/change_history.py
python scripts/change_history.py --input-report <path-to-report.json>
```

By default the file is `%LOCALAPPDATA%\WUP\change-history.jsonl` (or under the configured `local.state_dir`). The first observation is an explicit `baseline` record, not a change from nothing. Later runs append a `change` record only when a tool's installed version, latest version, status, or health changes; identical observations add no lines. Records contain only those fields, the tool name, changed field names, and an ISO-8601 UTC timestamp. This history is local only: WUP sends it nowhere and it does not affect notifications, scheduler behavior, or remote state.

View recent local history without reading raw JSONL or changing it:

```powershell
python scripts/change_history.py --view
python scripts/change_history.py --view --limit 10
python scripts/change_history.py --view --json
```

The viewer uses the same default path and never creates, repairs, rewrites, or appends history. Baseline output displays the persisted whitelisted tool snapshot; change output displays stored before/after values for changed fields. A missing or empty file reports no history and exits `0`; malformed history fails closed with exit `1` and remains unchanged. `--limit N` selects the most recent N records but displays them in chronological order. Invalid CLI usage exits `2`.

## Configuration

Copy [`wup.example.toml`](wup.example.toml) to `wup.toml`. Configuration covers:

- monitored tool selection;
- local state-directory override;
- optional local Workstation Ops/MCP repository path for its audit check;
- Telegram enablement;
- optional external email command;
- remote repository/state-branch identity used by the snapshot bridge.

The TOML file is non-secret. Do not put tokens or credentials in it.

### Telegram

Telegram is a valid standalone notification path. Enable it in configuration and provide:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

through the process environment. For same-user scheduled tasks, configuration
may instead point `env_file` at a local untracked file containing only those
two keys; process environment values win. WUP owns a small bounded Telegram
transport and does not commit these values.

### Email

Email is optional. WUP can call an external command that accepts:

```text
send --subject TEXT --text TEXT --html TEXT
```

This keeps provider-specific email credentials and policy outside WUP. A Telegram-only installation is fully supported.

## Remote monitoring

[`WUP Remote Version Sentinel`](.github/workflows/toolchain-remote-version-sentinel.yml) can observe public upstream releases through GitHub Actions while the workstation is off.

It keeps public upstream runtime state on the isolated `toolchain-remote-state`
branch:

- `remote-version-state.json` — upstream release dedupe only.

An optional sanitized workstation snapshot uses the same documented schema but
is stored privately as GitHub Actions secret `WUP_WORKSTATION_SNAPSHOT`. Local
publishing uses the existing authenticated `gh` CLI with the JSON supplied on
standard input; WUP creates no PAT. A snapshot by itself never triggers a
release alert and never participates in upstream release deduplication.

A first observation is silent. Repeated identical observations are silent. A workstation snapshot by itself never triggers a release alert.

The workflow ships manual-only in the initial public repository. Scheduling and repository secrets are opt-in deployment steps rather than hidden defaults.

## Finding semantics

| Status | Meaning |
| --- | --- |
| `CURRENT` | Installed version matches the verified target and relevant checks pass. |
| `WATCH` | Worth attention, but WUP deliberately stops short of recommending an update. |
| `UPDATE` | A newer stable release is verified and a manual update is recommended. |
| `URGENT` | A critical update or broken monitored component needs prompt attention. |
| `UNKNOWN` | WUP could not obtain trustworthy evidence. Never treated as `CURRENT`. |

Health is tracked separately from version status so a current version can still surface a runtime problem.

## Windows scheduler

WUP includes a same-user, non-elevated Task Scheduler manager. The default task runs daily at 08:00 local time, can start at the next availability after a missed run, may wake the PC, ignores overlapping runs, and has a bounded execution time.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-scheduled-task.ps1 -Action Install
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-scheduled-task.ps1 -Action Status
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-scheduled-task.ps1 -Action Remove
```

Create an untracked `wup.toml` before installation. The manager records its
explicit path in the task action, so scheduled execution never depends on a
working directory. Task arguments contain executable/configuration paths only
— never credentials.

## Privacy and security

WUP stores local deduplication state outside Git (by default under `%LOCALAPPDATA%\WUP`). Configuration examples contain no secrets. Public remote state contains upstream release facts only; optional workstation snapshot context is held only as an Actions secret.

External network access is limited to the public release/version sources and notification integrations you choose to configure. WUP does not require a hosted WUP service.

See [`SECURITY.md`](SECURITY.md) for vulnerability reporting guidance.

## Project status

WUP is an early public project extracted from a tool that already runs in production for its original author. The standalone repository is intentionally conservative: Windows first, read-only by default, and focused on making the existing behavior portable before adding larger features.

Planned ideas include easier installation/diagnostics, a safe update `PLAN -> APPLY` assistant, and a lightweight local status/history view. These are roadmap ideas, not promises or hidden runtime behavior.

## Contributing

Small, focused contributions are welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request.

## License

WUP is available under the [MIT License](LICENSE).
