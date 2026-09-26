"""At-rest encryption for board SMTP credentials (uses server secret)."""
from __future__ import annotations

import hashlib
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode

from cryptography.fernet import Fernet, InvalidToken

_FERNET_PREFIX = "f1:"
_LEGACY_PREFIX = "enc:"
_DEV_FALLBACK = b"bigas-dev-email-secret"


def _configured_secret_raw() -> str:
    return (
        (os.environ.get("BOARD_EMAIL_SECRET") or "").strip()
        or (os.environ.get("MCP_OAUTH_TOKEN_SECRET") or "").strip()
        or ((os.environ.get("BIGAS_ACCESS_KEYS") or "").split(",")[0].strip())
    )


def _is_dev_encryption_allowed() -> bool:
    mode = (os.environ.get("CHAT_AUTH_MODE") or "").strip().lower()
    if mode == "dev":
        return True
    flask_env = (os.environ.get("FLASK_ENV") or "").strip().lower()
    return flask_env in ("development", "dev")


def _secret_material(*, allow_dev_fallback: bool) -> bytes:
    raw = _configured_secret_raw()
    if not raw:
        if allow_dev_fallback and _is_dev_encryption_allowed():
            return hashlib.sha256(_DEV_FALLBACK).digest()
        raise RuntimeError(
            "SMTP credential encryption requires BOARD_EMAIL_SECRET, "
            "MCP_OAUTH_TOKEN_SECRET, or BIGAS_ACCESS_KEYS to be configured."
        )
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _fernet(*, allow_dev_fallback: bool = False) -> Fernet:
    key = urlsafe_b64encode(_secret_material(allow_dev_fallback=allow_dev_fallback))
    return Fernet(key)


def _legacy_xor_decrypt(ciphertext: str) -> str:
    key = _secret_material(allow_dev_fallback=True)
    raw = urlsafe_b64decode(ciphertext[len(_LEGACY_PREFIX) :].encode("ascii"))
    plain = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return plain.decode("utf-8")


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    allow_dev = _is_dev_encryption_allowed()
    if not _configured_secret_raw() and not allow_dev:
        raise RuntimeError(
            "Cannot store SMTP passwords without BOARD_EMAIL_SECRET "
            "(or MCP_OAUTH_TOKEN_SECRET / BIGAS_ACCESS_KEYS) in this environment."
        )
    token = _fernet(allow_dev_fallback=allow_dev).encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _FERNET_PREFIX + token


def decrypt_secret(ciphertext: str) -> str:
    text = (ciphertext or "").strip()
    if not text:
        return ""
    if text.startswith(_FERNET_PREFIX):
        try:
            allow_dev = _is_dev_encryption_allowed()
            return _fernet(allow_dev_fallback=allow_dev).decrypt(
                text[len(_FERNET_PREFIX) :].encode("ascii")
            ).decode("utf-8")
        except InvalidToken:
            return ""
    if text.startswith(_LEGACY_PREFIX):
        try:
            return _legacy_xor_decrypt(text)
        except Exception:
            return ""
    return text


def mask_secret(_: str) -> str:
    return "********"


def is_masked_password(value: object) -> bool:
    if value is None:
        return False
    return str(value).strip() == mask_secret("")
