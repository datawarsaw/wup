# Contributing to WUP

Thanks for helping improve WUP.

WUP is intentionally small and conservative. The best contributions keep the core deterministic, local-first, read-only by default, and easy to understand from a clean checkout.

## Before you start

For larger changes, open an issue first so the scope and product boundary are clear. Small fixes, documentation improvements, tests, and narrowly scoped tool-detection improvements can go straight to a pull request.

## Development setup

On Windows with Python 3.10+:

```powershell
git clone https://github.com/datawarsaw/wup.git
cd wup
Copy-Item wup.example.toml wup.toml
python -m unittest discover -s tests -p 'test_*.py'
python scripts/check_toolchain.py --offline
python scripts/run_notifier.py --dry-run --config wup.toml
```

Do not use real notification credentials in tests or examples.

## Pull-request expectations

Please keep changes focused and include deterministic tests for behavior changes.

Before opening a PR:

```powershell
python -m unittest discover -s tests -p 'test_*.py'
```

Also verify that:

- no tokens, account IDs, personal machine paths, `.env` files, or runtime state were added;
- dry-run paths do not send notifications or mutate dedupe state;
- release lookup failures fail closed to `UNKNOWN` rather than optimistic `CURRENT`;
- local installed-version facts and remote upstream facts remain clearly separated;
- no update/install command is executed automatically.

## Product boundaries

WUP is not currently a package manager, generic notification inbox, fleet-management platform, hosted service, or unattended auto-updater.

A new dependency, service, database, daemon, hosted component, or privileged update path needs an explicit design discussion before implementation.

## Adding or changing monitored tools

Prefer authoritative public version sources and deterministic local probes. Document the source/detection rule in `references/managed-tools.md` and add coverage for unavailable, malformed, and offline evidence.

## Security issues

Do not post credentials, exploit details, or sensitive environment data in a public issue. Follow [`SECURITY.md`](SECURITY.md) instead.
