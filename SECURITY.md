# Security Policy

## Reporting a vulnerability

Please do not publish credentials, secret values, exploit details, or sensitive workstation data in a public issue.

If GitHub private vulnerability reporting is enabled for this repository, use the repository's **Security → Report a vulnerability** flow.

If that private flow is not available, open a minimal public issue that says you have a security-sensitive report and need a private contact path. Do not include the vulnerability details in that issue.

## Scope

Security reports are especially useful for:

- credential or environment-variable disclosure;
- unsafe logging or error output;
- unintended state-branch data exposure;
- command injection through configuration or notification adapters;
- scheduler or subprocess behavior that could execute unintended commands;
- incorrect trust boundaries between local workstation facts and remote upstream observations.

## Current security posture

WUP is read-only by default. It does not automatically install or upgrade monitored software. Configuration examples contain no credentials, and runtime/deduplication state is kept outside the source tree.

The project is early-stage. Security fixes may change interfaces when necessary to preserve a safe default.
