# WUP

WUP is a small, read-only developer toolchain monitor. It checks local tools,
deduplicates meaningful findings, and can notify through Telegram and optional
email. Its optional GitHub Actions sentinel tracks public upstream releases
while the workstation is off. WUP never installs, upgrades, restarts, or
reconfigures monitored software.

## Quick start (Windows)

```powershell
git clone https://github.com/YOUR-ACCOUNT/wup.git
cd wup
Copy-Item wup.example.toml wup.toml
python -m unittest discover -s tests -p 'test_*.py'
python scripts/check_toolchain.py --offline
python scripts/run_notifier.py --dry-run --config wup.toml
```

Python 3.10+ is required. Node.js is needed only for Node-based checks or an
external email adapter.

## How it works

```text
Local PC: WUP audit -> local dedupe -> Telegram / optional email
                                  -> sanitized snapshot
GitHub Actions: public releases -> isolated state branch -> Telegram
```

The local watcher is authoritative for installed versions and runtime health.
The remote sentinel reports only upstream releases; it never claims what is
currently installed on the workstation.

## Configuration and notifications

Copy `wup.example.toml` to `wup.toml`. It supports tool selection, a local
state-directory override, Telegram enablement, an optional email command, and
remote repository/state-branch identity. TOML contains no secrets.

Telegram is a valid standalone installation. Enable it in TOML and provide
`TELEGRAM_BOT_TOKEN` plus `TELEGRAM_CHAT_ID` in the process environment.
WUP owns a minimal bounded Telegram transport. Email is optional: configure an
external command that accepts `send --subject TEXT --text TEXT --html TEXT`.
For example, an independently installed Cloudflare Email CLI can fill this
role; WUP neither requires nor copies it.

Default local dedupe state is `%LOCALAPPDATA%\WUP\last-alerted.json`. Only
changed `URGENT`, `UPDATE`, and `WATCH` findings alert; `CURRENT` and `UNKNOWN`
are silent.

## Remote monitoring

`.github/workflows/toolchain-remote-version-sentinel.yml` uses only the
`toolchain-remote-state` branch. It stores `remote-version-state.json` for
upstream dedupe and `workstation-snapshot.json` for the last sanitized local
installed-version observation. First observation is silent. A snapshot alone
does not alert; a valid snapshot only enriches a genuine upstream change.

Pass A leaves this workflow manual-only. Do not add secrets, schedule it, or
create remote state until a reviewed cutover to WUP.

## Windows scheduler and uninstall

WUP includes a same-user non-elevated Task Scheduler manager. It creates `WUP
Toolchain Update Watch` daily at 08:00 local time, starts once when available
after a missed run, may wake the PC, ignores overlaps, and limits execution to
20 minutes.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-scheduled-task.ps1 -Action Install
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-scheduled-task.ps1 -Action Status
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-scheduled-task.ps1 -Action Remove
```

Task definitions contain executable/repository paths only, never credentials.
To uninstall, remove the task, delete the clone, and optionally delete the
chosen local state directory.

## License and provenance

WUP was extracted from a proven internal component using Git subtree history.
The standalone Telegram transport is a small adaptation of that proven
delivery boundary. Licensing remains pending a provenance review; see
[LICENSE.md](LICENSE.md). This repository is private during Pass A.
