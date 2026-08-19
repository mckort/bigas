"""Chat email allowlist (does not import the Flask app)."""
from __future__ import annotations

from bigas.chat.auth import is_chat_allowed


def test_allowlist_skipped_in_dev_mode(monkeypatch):
    monkeypatch.setenv("CHAT_AUTH_MODE", "dev")
    monkeypatch.setenv("CHAT_ALLOWED_EMAILS", "mckort@gmail.com")
    assert is_chat_allowed({"email": "anyone@example.com"}) is True


def test_allowlist_accepts_listed_email(monkeypatch):
    monkeypatch.setenv("CHAT_AUTH_MODE", "firebase")
    monkeypatch.setenv("CHAT_ALLOWED_EMAILS", "mckort@gmail.com, other@example.com")
    monkeypatch.delenv("CHAT_ADMIN_EMAILS", raising=False)
    assert is_chat_allowed({"email": "mckort@gmail.com"}) is True
    assert is_chat_allowed({"email": "MCKORT@gmail.com"}) is True
    assert is_chat_allowed({"email": "intruder@gmail.com"}) is False
    assert is_chat_allowed({"email": ""}) is False


def test_allowlist_falls_back_to_admin_emails(monkeypatch):
    monkeypatch.setenv("CHAT_AUTH_MODE", "firebase")
    monkeypatch.delenv("CHAT_ALLOWED_EMAILS", raising=False)
    monkeypatch.setenv("CHAT_ADMIN_EMAILS", "mckort@gmail.com")
    assert is_chat_allowed({"email": "mckort@gmail.com"}) is True
    assert is_chat_allowed({"email": "intruder@gmail.com"}) is False


def test_allowlist_open_when_unset(monkeypatch):
    monkeypatch.setenv("CHAT_AUTH_MODE", "firebase")
    monkeypatch.delenv("CHAT_ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("CHAT_ADMIN_EMAILS", raising=False)
    assert is_chat_allowed({"email": "anyone@example.com"}) is True
