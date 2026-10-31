# Changelog

All notable WUP changes will be recorded here until a release-based changelog is established.

## Unreleased

- Extracted the proven Toolchain Update Watch into the standalone WUP repository.
- Refocused WUP on local, on-demand commands. The local audit remains `check_toolchain.py`; update planning, explicit supported updates, diagnostics, history, and HTML reporting run only when invoked.
- Removed notification delivery and configuration, including Telegram, the external email adapter, alert deduplication, and notifier commands.
- Removed Windows Task Scheduler integration, the GitHub Actions Remote Version Sentinel, remote state, and workstation-snapshot publishing.
- Narrowed configuration to `[tools]` and `[local]`. Legacy `[notifications]` and `[remote]` sections are ignored.
- Removed `--state-path` and `--skip-scheduler` from `doctor.py` and `preflight.py`. Doctor diagnostics now check repository integrity, configuration validity, and runtime prerequisites only.
- Fixed two `check_toolchain.py` CLI regressions: an explicitly supplied `--config` path that does not exist now fails with a clear error instead of silently using default settings, and module execution (`python -m scripts.check_toolchain`) is supported next to direct script execution.
- Added the WUP logo, public-facing README, MIT license, contribution guide, security policy, and CI baseline.
