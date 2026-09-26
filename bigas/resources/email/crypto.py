"""At-rest obfuscation for board SMTP credentials (uses server secret)."""
from __future__ import annotations

import hashlib
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode


def _secret_material() -> bytes:
    raw = (
        (os.environ.get("BOARD_EMAIL_SECRET") or "").strip()
        or (os.environ.get("MCP_OAUTH_TOKEN_SECRET") or "").strip()
        or ((os.environ.get("BIGAS_ACCESS_KEYS") or "").split(",")[0].strip())
    )
    if not raw:
        return b"bigas-dev-email-secret"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    key = _secret_material()
    data = plaintext.encode("utf-8")
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return "enc:" + urlsafe_b64encode(out).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    text = (ciphertext or "").strip()
    if not text:
        return ""
    if not text.startswith("enc:"):
        return text
    key = _secret_material()
    raw = urlsafe_b64decode(text[4:].encode("ascii"))
    plain = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return plain.decode("utf-8")


def mask_secret(_: str) -> str:
    return "********"
