"""Ticket file attachments — storage, extraction, and AI prompt formatting."""

from __future__ import annotations

import base64
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_TICKET = 10
MAX_ATTACHMENTS_PER_CHAT_MESSAGE = 5
MAX_EXTRACTED_CHARS = 8000
ATTACHMENT_PREFIX = "ticket_attachments/"
CHAT_ATTACHMENT_PREFIX = "chat_attachments/"

IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}

TEXT_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
}

ALLOWED_MIME_TYPES = IMAGE_MIME_TYPES | TEXT_MIME_TYPES | {"application/pdf"}

EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
}

SCREENSHOT_PROMPT = """You are reading a screenshot attached to a product ticket.

Describe what the image shows so another AI can implement the ticket without seeing the image.

Focus on:
- Visible UI text, labels, errors, and copy (quote exactly)
- Layout and what the user is pointing at or highlighting
- Bugs, broken states, missing pieces, or the intended change if obvious
- Annotations, arrows, circles, or handwritten notes

Be concrete and complete. Do not speculate beyond what is visible.
Do not start with "The image shows" — just describe the screen."""

CHAT_SCREENSHOT_PROMPT = """You are reading a screenshot attached to a chat with an AI teammate.

Describe what the image shows so the assistant can answer without seeing the image.

Focus on:
- Visible UI text, labels, errors, and copy (quote exactly)
- Layout and what the user is pointing at or highlighting
- Bugs, broken states, missing pieces, or the question the screenshot implies
- Annotations, arrows, circles, or handwritten notes

Be concrete and complete. Do not speculate beyond what is visible.
Do not start with "The image shows" — just describe the screen."""

ImageDescriber = Callable[[bytes, str, str], str]
_image_describer: Optional[ImageDescriber] = None

_blob_store: Any = None
_blob_lock = threading.Lock()


class AttachmentError(ValueError):
    """User-facing attachment validation or storage error."""


def set_image_describer(fn: Optional[ImageDescriber]) -> None:
    """Override screenshot interpretation (tests)."""
    global _image_describer
    _image_describer = fn


def sanitize_filename(filename: str) -> str:
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name, flags=re.UNICODE).strip("._ ")
    return (name or "attachment")[:180]


def guess_content_type(filename: str, content_type: Optional[str] = None) -> str:
    raw = (content_type or "").split(";", 1)[0].strip().lower()
    if raw in ALLOWED_MIME_TYPES or raw == "image/jpg":
        return "image/jpeg" if raw == "image/jpg" else raw
    ext = os.path.splitext((filename or "").lower())[1]
    guessed = EXT_TO_MIME.get(ext)
    if guessed:
        return guessed
    raise AttachmentError(
        "Unsupported file type. Use a screenshot (png/jpg/webp/gif), PDF, or text file."
    )


def attachment_blob_name(ticket_id: str, attachment_id: str, filename: str) -> str:
    return f"{ATTACHMENT_PREFIX}{ticket_id}/{attachment_id}/{sanitize_filename(filename)}"


def chat_attachment_blob_name(thread_id: str, attachment_id: str, filename: str) -> str:
    return f"{CHAT_ATTACHMENT_PREFIX}{thread_id}/{attachment_id}/{sanitize_filename(filename)}"


def clip_extracted_text(text: str, *, limit: int = MAX_EXTRACTED_CHARS) -> str:
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    return body[: limit - 3].rstrip() + "..."


def format_ticket_attachments(
    attachments: List[Dict[str, Any]],
    *,
    max_chars_each: int = MAX_EXTRACTED_CHARS,
) -> str:
    """Format stored attachments for Research / Design / Implement prompts."""
    lines: List[str] = []
    for item in attachments or []:
        name = (item.get("filename") or "attachment").strip() or "attachment"
        kind = (item.get("content_type") or "").strip() or "file"
        extracted = clip_extracted_text(
            str(item.get("extracted_text") or ""),
            limit=max_chars_each,
        )
        if extracted:
            lines.append(f"### {name} ({kind})\n{extracted}")
        else:
            lines.append(f"### {name} ({kind})\n(no extracted text)")
    return "\n\n".join(lines) if lines else "(none)"


def attachments_text_for_issue(jira: Any, issue_key: str) -> str:
    list_fn = getattr(jira, "list_attachments", None)
    if not callable(list_fn):
        return "(none)"
    try:
        items = list_fn(issue_key) or []
    except Exception:
        logger.warning("Failed to load attachments for %s", issue_key, exc_info=True)
        return "(none)"
    return format_ticket_attachments(items)


def message_text_for_llm(message: Dict[str, Any]) -> str:
    """User/assistant text plus attachment interpretations for the chat LLM."""
    from bigas.chat.timestamps import prefix_llm_message

    text = str(message.get("content") or "").strip()
    meta = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    attachments = list((meta or {}).get("attachments") or [])
    att_block = format_ticket_attachments(attachments)
    if attachments and att_block != "(none)":
        text = f"{text}\n\n## Attachments\n{att_block}" if text else f"## Attachments\n{att_block}"
    return prefix_llm_message(text, message.get("created_at"))


def process_chat_files(
    files: List[Tuple[str, Optional[str], bytes]],
    *,
    thread_id: str,
    uploaded_by: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Validate, store, and interpret files attached to a chat message."""
    uploads = list(files or [])
    if len(uploads) > MAX_ATTACHMENTS_PER_CHAT_MESSAGE:
        raise AttachmentError(
            f"At most {MAX_ATTACHMENTS_PER_CHAT_MESSAGE} attachments per message"
        )
    validated: List[Tuple[str, str, bytes]] = []
    for index, (filename, content_type, data) in enumerate(uploads):
        safe_name, mime = validate_upload(
            filename=filename,
            content_type=content_type,
            size_bytes=len(data or b""),
            existing_count=index,
            max_count=MAX_ATTACHMENTS_PER_CHAT_MESSAGE,
            entity="message",
        )
        validated.append((safe_name, mime, data or b""))

    records: List[Dict[str, Any]] = []
    blobs = get_attachment_blob_store()
    for safe_name, mime, data in validated:
        extracted = extract_attachment_text(
            data=data,
            filename=safe_name,
            content_type=mime,
            image_prompt=CHAT_SCREENSHOT_PROMPT,
        )
        record = build_attachment_record(
            filename=safe_name,
            content_type=mime,
            size_bytes=len(data),
            storage_path="",
            extracted_text=extracted,
            uploaded_by=uploaded_by,
        )
        path = chat_attachment_blob_name(thread_id, record["id"], safe_name)
        record["storage_path"] = path
        blobs.put(path, data, mime)
        records.append(record)
    return records


def find_message_attachment(
    messages: List[Dict[str, Any]],
    attachment_id: str,
) -> Optional[Dict[str, Any]]:
    aid = (attachment_id or "").strip()
    if not aid:
        return None
    for message in messages or []:
        meta = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        for item in list((meta or {}).get("attachments") or []):
            if (item.get("id") or "") == aid:
                return dict(item)
    return None


def read_upload_body(stream: Any, *, max_bytes: int = MAX_ATTACHMENT_BYTES) -> bytes:
    """Read an upload stream without loading more than max_bytes into memory."""
    if stream is None:
        return b""
    content_length = getattr(stream, "content_length", None)
    if content_length is not None and content_length > max_bytes:
        raise AttachmentError("File is larger than 10 MB")
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AttachmentError("File is larger than 10 MB")
    return data


def image_interpretation_pending_text(filename: str) -> str:
    return (
        f"[Screenshot: {sanitize_filename(filename)}] "
        "Interpreting image for AI. Refresh in a moment if this ticket moves to an AI step."
    )


def extract_attachment_text(
    *,
    data: bytes,
    filename: str,
    content_type: str,
    defer_image_llm: bool = False,
    image_prompt: Optional[str] = None,
) -> str:
    if content_type in IMAGE_MIME_TYPES:
        if defer_image_llm:
            return image_interpretation_pending_text(filename)
        return clip_extracted_text(
            describe_image(data, content_type, filename, prompt=image_prompt)
        )
    if content_type in TEXT_MIME_TYPES:
        return clip_extracted_text(_decode_text_bytes(data))
    if content_type == "application/pdf":
        return clip_extracted_text(_extract_pdf_text(data, filename))
    return ""


def describe_image(
    data: bytes,
    content_type: str,
    filename: str,
    *,
    prompt: Optional[str] = None,
) -> str:
    if _image_describer is not None:
        try:
            return (_image_describer(data, content_type, filename) or "").strip()
        except Exception:
            logger.warning("Custom image describer failed for %s", filename, exc_info=True)
            return _image_fallback(filename, "interpretation failed")
    try:
        return _describe_image_with_llm(data, content_type, filename, prompt=prompt)
    except Exception as exc:
        logger.warning("Screenshot interpretation failed for %s: %s", filename, exc)
        return _image_fallback(filename, str(exc) or "vision unavailable")


def _image_fallback(filename: str, reason: str) -> str:
    return (
        f"[Screenshot: {sanitize_filename(filename)}] "
        f"Could not interpret the image ({reason}). "
        "Describe what it shows in a comment if the next AI step needs it."
    )


def _decode_text_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pdf_text(data: bytes, filename: str) -> str:
    from io import BytesIO

    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return (
            f"[PDF: {sanitize_filename(filename)}] Text extraction is not available. "
            "Summarize the relevant pages in the ticket description or a comment."
        )
    try:
        reader = PdfReader(BytesIO(data))
        pages = []
        for page in reader.pages[:15]:
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(text)
        if pages:
            return "\n\n".join(pages)
    except Exception:
        logger.warning("PDF extract failed for %s", filename, exc_info=True)
    return f"[PDF: {sanitize_filename(filename)}] No extractable text found."


def _describe_image_with_llm(
    data: bytes,
    content_type: str,
    filename: str,
    *,
    prompt: Optional[str] = None,
) -> str:
    from bigas.llm.factory import get_llm_client
    from bigas.llm.gemini_client import GeminiLLMClient
    from bigas.llm.openai_client import OpenAILLMClient

    client, _model = get_llm_client(feature="ticket_attachments")
    inner = getattr(client, "_inner", client)
    body = (prompt or SCREENSHOT_PROMPT).strip() or SCREENSHOT_PROMPT
    full_prompt = f"{body}\n\nFilename: {sanitize_filename(filename)}"

    if isinstance(inner, GeminiLLMClient):
        response = inner._model.generate_content(
            [full_prompt, {"mime_type": content_type, "data": data}],
        )
        text = (getattr(response, "text", None) or "").strip()
        if text:
            return text
        raise RuntimeError("Gemini returned empty screenshot description")

    if isinstance(inner, OpenAILLMClient):
        encoded = base64.b64encode(data).decode("ascii")
        completion = inner._client.chat.completions.create(
            model=inner.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": full_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{content_type};base64,{encoded}"},
                        },
                    ],
                }
            ],
            max_tokens=1500,
            temperature=0.2,
        )
        text = ((completion.choices[0].message.content) or "").strip()
        if text:
            return text
        raise RuntimeError("OpenAI returned empty screenshot description")

    raise RuntimeError(f"Unsupported vision client: {type(inner).__name__}")


class InMemoryAttachmentBlobStore:
    def __init__(self) -> None:
        self._data: Dict[str, bytes] = {}

    def put(self, path: str, data: bytes, content_type: str) -> str:
        self._data[path] = data
        return path

    def get(self, path: str) -> Optional[bytes]:
        return self._data.get(path)

    def delete(self, path: str) -> None:
        self._data.pop(path, None)


class GcsAttachmentBlobStore:
    """Object-only GCS access — does not call buckets.get / create."""

    def __init__(
        self,
        *,
        bucket_name: Optional[str] = None,
        bucket: Any = None,
    ) -> None:
        self._bucket = bucket
        if self._bucket is None:
            name = (bucket_name or os.environ.get("STORAGE_BUCKET_NAME") or "").strip()
            if not name:
                raise RuntimeError("STORAGE_BUCKET_NAME is required to store ticket attachments")
            from google.cloud import storage as gcs

            self._bucket = gcs.Client().bucket(name)

    def put(self, path: str, data: bytes, content_type: str) -> str:
        blob = self._bucket.blob(path)
        blob.upload_from_string(data, content_type=content_type or "application/octet-stream")
        return path

    def get(self, path: str) -> Optional[bytes]:
        blob = self._bucket.blob(path)
        try:
            if not blob.exists():
                return None
            return blob.download_as_bytes()
        except Exception:
            logger.warning("Failed to download attachment %s", path, exc_info=True)
            return None

    def delete(self, path: str) -> None:
        blob = self._bucket.blob(path)
        try:
            blob.delete()
        except Exception:
            logger.warning("Failed to delete attachment %s", path, exc_info=True)


def get_attachment_blob_store() -> Any:
    global _blob_store
    with _blob_lock:
        if _blob_store is not None:
            return _blob_store
        storage_mode = (os.environ.get("CHAT_STORAGE_MODE") or "").strip().lower()
        bucket = (os.environ.get("STORAGE_BUCKET_NAME") or "").strip()
        if storage_mode == "memory" or not bucket:
            _blob_store = InMemoryAttachmentBlobStore()
        else:
            try:
                _blob_store = GcsAttachmentBlobStore(bucket_name=bucket)
            except Exception as exc:
                raise RuntimeError(
                    f"STORAGE_BUCKET_NAME is set but GCS attachment store failed: {exc}"
                ) from exc
        return _blob_store


def delete_attachment_blobs(attachments: List[Dict[str, Any]]) -> None:
    """Best-effort delete of stored attachment bytes."""
    paths = [
        (item.get("storage_path") or "").strip()
        for item in (attachments or [])
        if (item.get("storage_path") or "").strip()
    ]
    if not paths:
        return
    blobs = get_attachment_blob_store()
    for path in paths:
        blobs.delete(path)


def reset_attachment_blob_store_for_tests() -> None:
    global _blob_store
    with _blob_lock:
        _blob_store = None


def validate_upload(
    *,
    filename: str,
    content_type: Optional[str],
    size_bytes: int,
    existing_count: int,
    max_count: int = MAX_ATTACHMENTS_PER_TICKET,
    entity: str = "ticket",
) -> Tuple[str, str]:
    limit = max(1, int(max_count))
    if existing_count >= limit:
        raise AttachmentError(f"At most {limit} attachments per {entity}")
    if size_bytes <= 0:
        raise AttachmentError("File is empty")
    if size_bytes > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("File is larger than 10 MB")
    safe_name = sanitize_filename(filename)
    mime = guess_content_type(safe_name, content_type)
    return safe_name, mime


def build_attachment_record(
    *,
    filename: str,
    content_type: str,
    size_bytes: int,
    storage_path: str,
    extracted_text: str,
    uploaded_by: Optional[str] = None,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "filename": filename,
        "content_type": content_type,
        "size_bytes": int(size_bytes),
        "storage_path": storage_path,
        "extracted_text": extracted_text or "",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    uid = (uploaded_by or "").strip()
    if uid:
        record["uploaded_by"] = uid
    return record
