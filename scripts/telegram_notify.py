#!/usr/bin/env python3
"""Minimal WUP-owned Telegram transport; environment credentials only."""
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


def send(message: str) -> bool:
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
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
    return 0 if send(parser.parse_args().message) else 1


if __name__ == "__main__": raise SystemExit(main())
