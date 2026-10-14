#!/usr/bin/env python3
"""Minimal WUP-owned Telegram transport with a narrow optional env-file boundary."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 10
TOKEN = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
ALLOWED_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def load_credentials(environment: dict[str, str] | None = None, env_file: str | None = None) -> dict[str, str]:
    """Load Telegram credentials exclusively from Windows CurrentUser DPAPI."""
    from telegram_secrets import load_telegram_secrets, SecretLoadError
    try:
        values = load_telegram_secrets()
        if set(values) != set(ALLOWED_KEYS) or not all(values.values()):
            return {key: "" for key in ALLOWED_KEYS}
        return values
    except (SecretLoadError, OSError, ValueError, UnicodeError):
        return {key: "" for key in ALLOWED_KEYS}


def send(message: str, environment: dict[str, str] | None = None) -> bool:
    credentials = load_credentials(environment)
    token, chat_id = credentials["TELEGRAM_BOT_TOKEN"], credentials["TELEGRAM_CHAT_ID"]
    if not TOKEN.match(token) or not chat_id:
        return False
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": message, "disable_web_page_preview": True}).encode("utf-8"),
        headers={"content-type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300 and bool(json.loads(response.read().decode("utf-8")).get("ok"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", required=True)
    if send(parser.parse_args().message): return 0
    print("Telegram credentials unavailable or delivery failed", file=sys.stderr)
    return 1


if __name__ == "__main__": raise SystemExit(main())
