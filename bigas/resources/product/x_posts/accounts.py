"""Map X handles to portfolio products that have their own account."""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Sequence

from bigas.portfolio import DEFAULT_PROJECT_ALIASES


def parse_account_project_map(raw: Optional[str] = None) -> Dict[str, List[str]]:
    """Parse ``X_ACCOUNT_PROJECT_MAP=bigasmyaiteam:BIG,vcfieldassistan:VFA``."""
    value = (raw if raw is not None else os.environ.get("X_ACCOUNT_PROJECT_MAP") or "").strip()
    out: Dict[str, List[str]] = {}
    if not value:
        return out
    for part in value.split(","):
        item = part.strip()
        if not item or ":" not in item:
            continue
        account, keys_raw = item.split(":", 1)
        account = account.strip().lstrip("@").lower()
        keys = [
            k.strip().upper()
            for k in re.split(r"[+|]", keys_raw)
            if k.strip()
        ]
        if account and keys:
            out[account] = keys
    return out


def project_keys_for_x_account(account: str) -> List[str]:
    """Return Jira project keys owned by this X handle.

    Env ``X_ACCOUNT_PROJECT_MAP`` wins. Otherwise the handle is matched against
    portfolio aliases (``bigasmyaiteam`` → BIG, ``vcfieldassistan`` → VFA).
    Unmapped accounts get no draft.
    """
    name = (account or "").strip().lstrip("@")
    if not name:
        return []
    folded = name.lower()
    env_map = parse_account_project_map()
    if folded in env_map:
        return list(env_map[folded])

    matches: List[str] = []
    for key, aliases in DEFAULT_PROJECT_ALIASES.items():
        needles = [str(a).lower().replace(" ", "") for a in aliases if str(a).strip()]
        if any(n and n in folded for n in needles):
            matches.append(key)
    return matches


def resolve_account_projects(
    account: str,
    *,
    explicit_keys: Optional[Sequence[str]] = None,
) -> List[str]:
    """Mapped keys for an account, optionally filtered by a request override."""
    mapped = project_keys_for_x_account(account)
    explicit = [
        str(k).strip().upper()
        for k in (explicit_keys or [])
        if k is not None and str(k).strip()
    ]
    if mapped and explicit:
        wanted = set(explicit)
        return [k for k in mapped if k in wanted]
    return list(mapped)
