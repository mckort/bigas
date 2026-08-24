"""Tests for chat message attachments used by the conversation LLM."""

from __future__ import annotations

import io
import json
import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from flask import Flask

from bigas.chat import db as chat_db
from bigas.resources.chat.endpoints import chat_bp
from bigas.tickets.attachments import (
    AttachmentError,
    get_attachment_blob_store,
    message_text_for_llm,
    process_chat_files,
    reset_attachment_blob_store_for_tests,
    set_image_describer,
)


@pytest.fixture(autouse=True)
def _reset_stores():
    chat_db._store = None
    reset_attachment_blob_store_for_tests()
    set_image_describer(None)
    yield
    chat_db._store = None
    reset_attachment_blob_store_for_tests()
    set_image_describer(None)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(chat_bp)
    with app.test_client() as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-dev-token"}


def test_message_text_for_llm_includes_screenshot():
    text = message_text_for_llm(
        {
            "content": "What's broken here?",
            "metadata": {
                "attachments": [
                    {
                        "filename": "checkout.png",
                        "content_type": "image/png",
                        "extracted_text": "Red banner: Card declined",
                    }
                ]
            },
        }
    )
    assert "What's broken here?" in text
    assert "## Attachments" in text
    assert "Card declined" in text
    assert message_text_for_llm({"content": "hello"}) == "hello"


def test_process_chat_files_interprets_screenshot():
    set_image_describer(lambda data, mime, name: f"Modal cut off ({name})")
    records = process_chat_files(
        [("iphone.png", "image/png", b"\x89PNG")],
        thread_id="thread-1",
        uploaded_by="dev-user",
    )
    assert len(records) == 1
    assert "iphone.png" in records[0]["extracted_text"]
    assert "Modal cut off" in records[0]["extracted_text"]
    assert records[0]["storage_path"].startswith("chat_attachments/thread-1/")


def test_process_chat_files_validates_before_storage():
    set_image_describer(lambda data, mime, name: "ok")
    with pytest.raises(AttachmentError):
        process_chat_files(
            [
                ("good.png", "image/png", b"\x89PNG"),
                ("bad.bin", "application/octet-stream", b"data"),
            ],
            thread_id="thread-orphan",
            uploaded_by="dev-user",
        )
    store = get_attachment_blob_store()
    assert not store._data


def test_rejects_too_many_multipart_files(client, monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.chat.endpoints.handle_chat_message",
        lambda **kwargs: {"status": "complete"},
    )
    thread_id = client.post(
        "/api/chat/threads",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        data=json.dumps({"agent_id": "chief"}),
    ).get_json()["thread"]["thread_id"]

    resp = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers=_auth_headers(),
        data={
            "content": "too many",
            "file": [
                (io.BytesIO(b"a"), "a.txt"),
                (io.BytesIO(b"b"), "b.txt"),
                (io.BytesIO(b"c"), "c.txt"),
                (io.BytesIO(b"d"), "d.txt"),
                (io.BytesIO(b"e"), "e.txt"),
                (io.BytesIO(b"f"), "f.txt"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "5 attachments" in resp.get_json()["error"]


def test_send_chat_message_with_screenshot(client, monkeypatch):
    captured = {}

    def mock_handle(**kwargs):
        captured.update(kwargs)
        from bigas.chat.db import get_chat_store

        store = get_chat_store()
        store.add_message(
            kwargs["thread_id"],
            role="user",
            content=kwargs["user_message"],
            metadata={
                "client_id": kwargs.get("client_id"),
                "attachments": kwargs.get("attachments") or [],
            },
        )
        msg = store.add_message(
            kwargs["thread_id"],
            role="assistant",
            content="I see the cutoff modal.",
        )
        return {"status": "complete", "message": msg}

    monkeypatch.setattr("bigas.resources.chat.endpoints.handle_chat_message", mock_handle)
    set_image_describer(lambda data, mime, name: "Confirm delete is cut off on iPhone.")

    thread_id = client.post(
        "/api/chat/threads",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        data=json.dumps({"agent_id": "chief"}),
    ).get_json()["thread"]["thread_id"]

    resp = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers=_auth_headers(),
        data={
            "content": "What's wrong on mobile?",
            "file": (io.BytesIO(b"\x89PNG fake"), "iphone.png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert captured["user_message"] == "What's wrong on mobile?"
    assert captured["attachments"]
    assert "Confirm delete" in captured["attachments"][0]["extracted_text"]

    listed = client.get(
        f"/api/chat/threads/{thread_id}/messages",
        headers={**_auth_headers(), "Content-Type": "application/json"},
    ).get_json()["messages"]
    user = next(m for m in listed if m["role"] == "user")
    assert user["content"] == "What's wrong on mobile?"
    attachment = user["metadata"]["attachments"][0]
    downloaded = client.get(
        f"/api/chat/threads/{thread_id}/attachments/{attachment['id']}",
        headers=_auth_headers(),
    )
    assert downloaded.status_code == 200
    assert downloaded.data.startswith(b"\x89PNG")


def test_chat_history_includes_prior_attachments(client, monkeypatch):
    histories = []

    def mock_handle(**kwargs):
        histories.append(kwargs.get("history") or [])
        from bigas.chat.db import get_chat_store

        store = get_chat_store()
        store.add_message(
            kwargs["thread_id"],
            role="user",
            content=kwargs["user_message"],
            metadata={"attachments": kwargs.get("attachments") or []},
        )
        msg = store.add_message(kwargs["thread_id"], role="assistant", content="Noted")
        return {"status": "complete", "message": msg}

    monkeypatch.setattr("bigas.resources.chat.endpoints.handle_chat_message", mock_handle)
    set_image_describer(lambda data, mime, name: "Primary button overflows")

    thread_id = client.post(
        "/api/chat/threads",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        data=json.dumps({"agent_id": "chief"}),
    ).get_json()["thread"]["thread_id"]

    first = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers=_auth_headers(),
        data={"content": "Look", "file": (io.BytesIO(b"\x89PNG"), "bug.png")},
        content_type="multipart/form-data",
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        data=json.dumps({"content": "How should we fix it?"}),
    )
    assert second.status_code == 200
    prior = histories[-1]
    joined = "\n".join(item["content"] for item in prior)
    assert "Primary button overflows" in joined
    assert "Look" in joined


def test_json_chat_still_requires_content(client, monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.chat.endpoints.handle_chat_message",
        lambda **kwargs: {"status": "complete"},
    )
    thread_id = client.post(
        "/api/chat/threads",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        data=json.dumps({"agent_id": "chief"}),
    ).get_json()["thread"]["thread_id"]
    empty = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        data=json.dumps({"content": "   "}),
    )
    assert empty.status_code == 400
