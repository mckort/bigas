"""Split a branch compare diff into review slices and merge the results.

One model pass over a large develop-vs-production diff reports only the most
salient findings. Prepare staging reviews each slice, then combines them.
"""
from __future__ import annotations

import re
from typing import Callable, List, Sequence, Tuple

from bigas.resources.cto.autofix.heuristics import (
    _section_bodies,
    review_findings_by_section,
    review_is_ready_to_merge,
)

# Keep each model call on a slice it can actually finish.
DEFAULT_SLICE_CHARS = 60_000
# Nothing is dropped. Extra slices are packed onto the last one.
MAX_SLICES = 20

_LOCK_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "npm-shrinkwrap.json",
    "cargo.lock",
    "poetry.lock",
    "composer.lock",
    "gemfile.lock",
}
_NOISE_DIRS = {"static", "staticfiles", "dist", "node_modules"}
_HASHED_ASSET = re.compile(r"\.[0-9a-f]{8,}\.(?:js|css|map)$", re.I)
_DIFF_HEADER = re.compile(r"(?m)^diff --git ")

SLICE_INSTRUCTIONS = """
This is one slice of a prepare-staging release gate.
Report a Blocker only for data loss, a security hole, a broken import this slice
proves, or a staging/deploy script that would fail.
Report an Important finding only for a real behavior bug this slice proves.
Leave Minor as None. Do not report CSS, theme, ARIA, copy,
enum alias spelling, workflow style, unused imports, or third-party URL swaps.
Do not write "ready to merge". That verdict is decided after every slice is combined.
Report a missing import or NameError only when this slice shows the file's import
block and the symbol is neither imported nor defined there. If the import block is
not in the slice, do not report an undefined name.
""".strip()

POST_AUTOFIX_SLICE_INSTRUCTIONS = """
This is one slice of the branch diff after a prepare-staging autofix merged.
Verify previous Blockers and Important items only when this slice shows the relevant code.
If a previous finding is not in this slice, leave it out. Another slice covers it.
Report a new Blocker or Important item only when this slice shows the autofix
introduced data loss, a security hole, a proven broken import, a broken
staging/deploy script, or a real behavior bug.
Leave Minor as None. Do not report new nits, style, enum aliases,
or third-party URL swaps.
Do not write "ready to merge". That verdict is decided after every slice is combined.
Report a missing import or NameError only when this slice shows the file's import
block and the symbol is neither imported nor defined there. If the import block is
not in the slice, do not report an undefined name.
""".strip()

_CLEAN_REVIEW = """### Blockers
None.

### Important
None.

### Minor
None.

The candidate is ready to merge.
""".strip()


def is_noise_path(path: str) -> bool:
    """Lockfiles and built assets drown a review without carrying product bugs."""
    name = (path or "").rsplit("/", 1)[-1].lower()
    if name in _LOCK_NAMES or name == "webpack-stats.json":
        return True
    parts = [part.lower() for part in path.split("/") if part]
    if any(part in _NOISE_DIRS for part in parts[:-1]):
        return True
    if name.endswith(".min.js") or name.endswith(".min.css"):
        return True
    return bool(_HASHED_ASSET.search(path or ""))


def _b_path(header: str) -> str:
    rest = header[len("diff --git ") :].strip()
    if rest.startswith('"'):
        parts: List[str] = []
        i = 0
        while i < len(rest) and rest[i] == '"':
            j = i + 1
            buf: List[str] = []
            while j < len(rest):
                if rest[j] == "\\" and j + 1 < len(rest):
                    buf.append(rest[j + 1])
                    j += 2
                    continue
                if rest[j] == '"':
                    j += 1
                    break
                buf.append(rest[j])
                j += 1
            parts.append("".join(buf))
            while j < len(rest) and rest[j] == " ":
                j += 1
            i = j
        raw = parts[-1] if parts else rest
    else:
        idx = rest.rfind(" b/")
        raw = rest[idx + 1 :] if idx != -1 else rest
    if raw.startswith("b/"):
        raw = raw[2:]
    return raw


def split_unified_diff(diff: str) -> List[Tuple[str, str]]:
    """Return (new path, file diff) pairs. A diff without headers is one slice."""
    text = (diff or "").replace("\r\n", "\n")
    if not text.strip():
        return []
    matches = list(_DIFF_HEADER.finditer(text))
    if not matches:
        chunk = text if text.endswith("\n") else text + "\n"
        return [("", chunk)]
    files: List[Tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end]
        if not chunk.endswith("\n"):
            chunk += "\n"
        header = chunk.split("\n", 1)[0]
        files.append((_b_path(header), chunk))
    return files


def review_slices(
    diff: str,
    *,
    max_chars: int = DEFAULT_SLICE_CHARS,
    max_slices: int = MAX_SLICES,
) -> List[str]:
    """Group non-noise file diffs into slices. Every kept file is included."""
    kept = [text for path, text in split_unified_diff(diff) if not is_noise_path(path)]
    if not kept:
        return []
    slices: List[str] = []
    current: List[str] = []
    size = 0
    for text in kept:
        piece = text if len(text) <= max_chars else (
            text[:max_chars] + "\n\n... (file diff truncated for length)\n"
        )
        if current and size + len(piece) > max_chars:
            slices.append("".join(current))
            current = []
            size = 0
        current.append(piece)
        size += len(piece)
    if current:
        slices.append("".join(current))
    if max_slices > 0 and len(slices) > max_slices:
        head = slices[: max_slices - 1]
        tail = "".join(slices[max_slices - 1 :])
        slices = head + [tail]
    return slices


def _finding_chunks(text: str) -> List[str]:
    body = (text or "").strip()
    if not body:
        return []
    return [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]


def _dedupe(items: Sequence[str]) -> List[str]:
    seen = set()
    unique: List[str] = []
    for item in items:
        for chunk in _finding_chunks(item):
            key = re.sub(r"\s+", " ", chunk)
            if key in seen:
                continue
            seen.add(key)
            unique.append(chunk)
    return unique


def merge_slice_reviews(parts: Sequence[str]) -> str:
    """Combine slice reviews into one Blockers / Important / Minor document."""
    grouped = {"blockers": [], "important": [], "minor": []}
    for part in parts:
        text = (part or "").strip()
        if not text:
            continue
        found = review_findings_by_section(text)
        if found:
            for name, body in found.items():
                grouped[name].append(body)
            continue
        if review_is_ready_to_merge(text):
            continue
        if _section_bodies(text):
            continue
        grouped["important"].append(text)

    blockers = _dedupe(grouped["blockers"])
    important = _dedupe(grouped["important"])
    minor = _dedupe(grouped["minor"])
    if not blockers and not important and not minor:
        return _CLEAN_REVIEW

    def section(title: str, items: Sequence[str]) -> str:
        body = "None." if not items else "\n\n".join(items)
        return f"### {title}\n{body}"

    return "\n\n".join(
        [
            section("Blockers", blockers),
            section("Important", important),
            section("Minor", minor),
            "Fix these findings before staging.",
        ]
    )


def review_compare_diff(
    diff: str,
    *,
    review_slice: Callable[[str, str], str],
    max_chars: int = DEFAULT_SLICE_CHARS,
    max_slices: int = MAX_SLICES,
    phase: str = "initial",
) -> str:
    """Review each slice, then merge. An empty kept diff is ready to merge."""
    slices = review_slices(diff, max_chars=max_chars, max_slices=max_slices)
    if not slices:
        return _CLEAN_REVIEW
    total = len(slices)
    guidance = (
        POST_AUTOFIX_SLICE_INSTRUCTIONS
        if phase in {"post_autofix", "prepare_staging_post"}
        else SLICE_INSTRUCTIONS
    )
    parts: List[str] = []
    for index, slice_diff in enumerate(slices, start=1):
        instructions = (
            f"Slice {index} of {total} of a prepare-staging branch diff.\n"
            f"{guidance}"
        )
        parts.append(review_slice(slice_diff, instructions) or "")
    return merge_slice_reviews(parts)
