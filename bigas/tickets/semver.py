"""Product versioning: X.Y.Z — major, product release, bugfix."""
from __future__ import annotations

import re
from typing import Optional, Tuple

_SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$",
    re.I,
)


class SemverError(ValueError):
    pass


def normalize_version_name(name: str) -> str:
    text = (name or "").strip()
    match = _SEMVER_RE.match(text)
    if not match:
        raise SemverError(
            f"Invalid version {name!r}. Use X.Y.Z (e.g. 0.9.0). "
            "Y is the product release; Z is bugfix only."
        )
    return f"{int(match.group('major'))}.{int(match.group('minor'))}.{int(match.group('patch'))}"


def parse_semver(name: str) -> Tuple[int, int, int]:
    normalized = normalize_version_name(name)
    major, minor, patch = normalized.split(".")
    return int(major), int(minor), int(patch)


def version_from_git_ref(ref: str) -> Optional[str]:
    """Return X.Y.Z if ref is a semver tag (v0.9.0, refs/tags/v0.9.0)."""
    text = (ref or "").strip()
    if text.startswith("refs/tags/"):
        text = text[len("refs/tags/") :]
    try:
        return normalize_version_name(text)
    except SemverError:
        return None


def next_product_release(name: str) -> str:
    """Bump the product release (middle number). 0.9.0 / 0.9.1 → 0.10.0; 1.0.0 → 1.1.0."""
    major, minor, _patch = parse_semver(name)
    return f"{major}.{minor + 1}.0"
