<p align="center"><img src="docs/wup-logo.jpg" width="720" alt="WUP logo" /></p>
<p align="center"><img src="https://github.com/datawarsaw/wup/actions/workflows/ci.yml/badge.svg" alt="CI" /> <img src="https://img.shields.io/github/license/datawarsaw/wup" alt="MIT license" /></p>

# WUP

WUP is a local-first, read-only developer toolchain watcher with optional
off-PC upstream release monitoring. It never installs, upgrades, restarts, or
reconfigures monitored tools.

## Quick start (Windows)

```powershell
git clone https://github.com/YOUR-ACCOUNT/wup.git
cd wup
Copy-Item wup.example.toml wup.toml
python -m unittest discover -s tests -p 'test_*.py'
python scripts/run_notifier.py --dry-run --config wup.toml
```

## Architecture

```text
Local WUP audit -> local dedupe -> Telegram / optional external email
                -> private WUP_WORKSTATION_SNAPSHOT Actions secret
GitHub Actions -> public upstream release check -> Telegram when changed
```

The local watcher is authoritative for installed versions and runtime health.
The remote sentinel observes public upstream releases only; it never claims a
workstation is currently installed or healthy.

## Configuration and notifications

Copy `wup.example.toml` to an untracked `wup.toml`. It supports tool selection,
local state-directory override, Telegram enablement, optional external email,
and the repository identity used by snapshot publishing. Do not put credentials
in TOML.

Telegram reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from the process
environment. A same-user scheduled task may configure a local untracked
`env_file` containing only those two keys; process environment values win.
Email is optional and uses an external command accepting
`send --subject TEXT --text TEXT --html TEXT`.

## Remote monitoring and privacy

The manual-only [Remote Version Sentinel](.github/workflows/toolchain-remote-version-sentinel.yml)
uses the public `toolchain-remote-state` branch for
`remote-version-state.json` only. The optional sanitized workstation snapshot
uses the same documented schema but is private in GitHub Actions secret
`WUP_WORKSTATION_SNAPSHOT`. Local publishing uses the existing authenticated
`gh` CLI with JSON on stdin; WUP creates no PAT. Snapshot changes never trigger
release alerts and never participate in upstream deduplication.

The local dedupe schema remains compatible with `last-alerted.json` version 2.
Default local state is `%LOCALAPPDATA%\WUP\last-alerted.json`.

## Windows scheduler

Create `wup.toml` before installation. The manager records its explicit config
path in the task action so scheduled execution never depends on its current
working directory. Task definitions contain executable and configuration paths,
never credentials.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-scheduled-task.ps1 -Action Install
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-scheduled-task.ps1 -Action Status
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-scheduled-task.ps1 -Action Remove
```

## Security and privacy

No secret, local state, or env file is committed. The public branch never
contains workstation snapshots, paths, usernames, tokens, account IDs, or
command output. See [SECURITY.md](SECURITY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

WUP is available under the [MIT License](LICENSE).
