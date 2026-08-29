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
    environment = environment or os.environ
    values = {key: environment.get(key, "") for key in ALLOWED_KEYS}
    path = env_file if env_file is not None else environment.get("WUP_TELEGRAM_ENV_FILE", "")
    if not path or all(values.values()): return values
    try:
        with open(path, encoding="utf-8") as stream:
            for raw in stream:
                line = raw.strip()
                if "=" not in line or line.startswith("#"): continue
                key, value = line.split("=", 1)
                if key in ALLOWED_KEYS and not values[key]: values[key] = value.strip()
    except OSError:
        return values
    return values


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
