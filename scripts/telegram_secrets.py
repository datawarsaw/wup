"""Read the two Telegram credentials from Windows CurrentUser DPAPI.

The file format is PowerShell ConvertFrom-SecureString (without -Key):
hex-encoded DPAPI ciphertext, whose plaintext is UTF-16LE.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

SECRET_FILENAMES = {
    "TELEGRAM_BOT_TOKEN": "telegram-bot-token.dpapi",
    "TELEGRAM_CHAT_ID": "telegram-chat-id.dpapi",
}


class SecretLoadError(RuntimeError):
    """Credentials could not be loaded; never include secret data in errors."""


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _decrypt(ciphertext: bytes) -> bytes:
    if os.name != "nt":
        raise SecretLoadError("Windows DPAPI is required")
    if not ciphertext:
        raise SecretLoadError("Empty encrypted credential")

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buffer = (ctypes.c_ubyte * len(ciphertext)).from_buffer_copy(ciphertext)
    encrypted = DATA_BLOB(len(ciphertext), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    decrypted = DATA_BLOB()
    # CRYPTPROTECT_UI_FORBIDDEN, no optional entropy; current Windows user only.
    if not crypt32.CryptUnprotectData(ctypes.byref(encrypted), None, None, None, None, 1, ctypes.byref(decrypted)):
        raise SecretLoadError("DPAPI decrypt failed for current Windows user")
    try:
        return ctypes.string_at(decrypted.pbData, decrypted.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(decrypted.pbData, ctypes.c_void_p))


def load_telegram_secrets(directory: Path | None = None) -> dict[str, str]:
    """Return credentials; fail closed if any file is missing/invalid."""
    if directory is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise SecretLoadError("LOCALAPPDATA is unavailable")
        directory = Path(local_app_data) / "WhiteGull" / "secure-secrets"
    result: dict[str, str] = {}
    for name, filename in SECRET_FILENAMES.items():
        path = directory / filename
        try:
            encoded = path.read_text(encoding="ascii").strip()
            if not encoded or len(encoded) % 2:
                raise ValueError("Invalid hexadecimal length")
            plaintext = _decrypt(bytes.fromhex(encoded))
            value = plaintext.decode("utf-16-le", errors="strict")
            if not value or "\x00" in value:
                raise ValueError("Invalid credential value")
            result[name] = value
        except (OSError, ValueError, UnicodeError, SecretLoadError) as exc:
            raise SecretLoadError(f"Unable to load Telegram secret: {name}") from None
    return result

def main():
    import json
    import sys
    try:
        secrets = load_telegram_secrets()
        sys.stdout.write(json.dumps(secrets, separators=(",", ":")))
    except Exception:
        print("TELEGRAM_DPAPI_LOAD_FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
