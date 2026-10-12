"""Show declarations that sit just above a diff hunk.

GitHub's pull diff keeps three lines of context. A prop or type field a few
lines higher is invisible, and the review then reports it as missing. This
prepends unchanged lines from the file at the PR head.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from bigas.resources.cto.pr_review.chunks import is_noise_path, split_unified_diff

logger = logging.getLogger(__name__)

DEFAULT_BEFORE = 25
MAX_FILES = 15
MAX_FILE_CHARS = 200_000

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def expand_review_diff(
    diff: str,
    read_file: Callable[[str], Optional[str]],
    *,
    before: int = DEFAULT_BEFORE,
) -> str:
    """Return the diff with up to `before` unchanged lines added above each hunk."""
    text = (diff or "").replace("\r\n", "\n")
    if not text.strip():
        return diff or ""
    parts = split_unified_diff(text)
    if not parts or (len(parts) == 1 and not parts[0][0]):
        return text if text.endswith("\n") else text + "\n"

    rendered: list[str] = []
    fetched = 0
    for path, chunk in parts:
        piece = chunk if chunk.endswith("\n") else chunk + "\n"
        if not _can_expand(path, chunk) or fetched >= MAX_FILES:
            rendered.append(piece)
            continue
        fetched += 1
        try:
            body = read_file(path)
        except Exception:
            logger.warning("Could not read %s for review context", path, exc_info=True)
            rendered.append(piece)
            continue
        if not body or len(body) > MAX_FILE_CHARS or "\0" in body:
            rendered.append(piece)
            continue
        rendered.append(_expand_file_chunk(piece, body.splitlines(), before=before))
    return "".join(rendered)


def expand_pr_diff(client, owner: str, repo: str, pr_number: int, diff: str) -> str:
    """Fetch head files and expand hunk context. The original diff is kept on failure."""
    if not (diff or "").strip():
        return diff or ""
    try:
        pr = client.get_pull_request(owner, repo, pr_number)
        sha = ((pr.get("head") or {}).get("sha") or "").strip()
    except Exception:
        logger.warning("Could not resolve PR head for review context", exc_info=True)
        return diff
    if not sha:
        return diff

    def read_file(path: str) -> Optional[str]:
        return client.get_text_file(owner, repo, path, sha)

    try:
        return expand_review_diff(diff, read_file)
    except Exception:
        logger.warning("Could not expand review diff context", exc_info=True)
        return diff


def _can_expand(path: str, chunk: str) -> bool:
    if not path or is_noise_path(path):
        return False
    if "new file mode" in chunk or "deleted file mode" in chunk:
        return False
    if "--- /dev/null" in chunk or "+++ /dev/null" in chunk:
        return False
    if "GIT binary patch" in chunk or chunk.startswith("Binary files"):
        return False
    return True


def _expand_file_chunk(chunk: str, lines: list[str], *, before: int) -> str:
    raw = chunk.split("\n")
    if raw and raw[-1] == "":
        raw = raw[:-1]
    header: list[str] = []
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in raw:
        if line.startswith("@@"):
            if current:
                hunks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
        else:
            header.append(line)
    if current:
        hunks.append(current)
    if not hunks:
        return chunk if chunk.endswith("\n") else chunk + "\n"

    prev_new_end = 1
    rendered = list(header)
    for hunk in hunks:
        match = _HUNK_HEADER.match(hunk[0])
        if not match:
            rendered.extend(hunk)
            continue
        old_start = int(match.group(1))
        old_len = int(match.group(2) or "1")
        new_start = int(match.group(3))
        new_len = int(match.group(4) or "1")
        tail = match.group(5) or ""
        if old_start < 1 or new_start < 1:
            rendered.extend(hunk)
            prev_new_end = max(prev_new_end, new_start + new_len)
            continue
        prepend_start = max(prev_new_end, new_start - max(before, 0), 1)
        max_by_old = old_start - 1
        earliest = new_start - max_by_old
        if prepend_start < earliest:
            prepend_start = earliest
        count = new_start - prepend_start
        slice_start = prepend_start - 1
        slice_end = new_start - 1
        if (
            count <= 0
            or slice_start < 0
            or slice_start >= len(lines)
            or slice_end > len(lines)
        ):
            rendered.extend(hunk)
        else:
            extra = lines[slice_start:slice_end]
            rendered.append(
                f"@@ -{old_start - count},{old_len + count} "
                f"+{new_start - count},{new_len + count} @@{tail}"
            )
            rendered.extend(f" {line}" for line in extra)
            rendered.extend(hunk[1:])
        prev_new_end = new_start + new_len
    return "\n".join(rendered) + "\n"
