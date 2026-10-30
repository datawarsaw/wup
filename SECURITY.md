# Security Policy

## Reporting a vulnerability

Please do not publish credentials, secret values, exploit details, or sensitive workstation data in a public issue.

If GitHub private vulnerability reporting is enabled for this repository, use the repository's **Security → Report a vulnerability** flow.

If that private flow is not available, open a minimal public issue that says you have a security-sensitive report and need a private contact path. Do not include the vulnerability details in that issue.

## Scope

Security reports are especially useful for:

- credential or environment-variable disclosure;
- unsafe logging or error output;
- unintended disclosure of local audit, history, or status data;
- subprocess behavior in the explicit update path that could execute unintended commands;
- incorrect separation between locally observed versions and public upstream release facts.

## Current security posture

WUP is read-only by default. It does not automatically install or upgrade monitored software. Configuration examples contain no credentials, and local change history is kept outside the source tree. The optional update command is explicit, foreground-only, and limited to supported npm-managed tools.

The project is early-stage. Security fixes may change interfaces when necessary to preserve a safe default.
