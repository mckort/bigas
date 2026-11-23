"""Map usage events to Bigas board project keys (VFA, AURA, …)."""
from __future__ import annotations

import os
import re
from typing import Dict, Mapping, Optional
from urllib.parse import urlparse

from bigas.portfolio import parse_csv_map, repo_map
from bigas.providers.usage.base import UsageEvent

# GCP project id → board key (or SHARED_VM for the multi-site webstack).
DEFAULT_GCP_PROJECT_TO_BOARD: Dict[str, str] = {
    "vcfieldassistant": "VFA",
    "bigas-503008": "BIG",
    "auralearn-491309": "AURA",
    "multiple-websites-491407": "SHARED_VM",
}

# Infra / deploy repos that are not a product board key.
DEFAULT_INFRA_REPO_TO_BOARD: Dict[str, str] = {
    "mckort/gcp-single-vm-webstack": "SHARED_VM",
}

# Product LLM / search features that belong to VFA even when logged under Bigas.
_VFA_FEATURE_PREFIXES = (
    "tavily",
    "llm.living_analysis",
    "llm.analysis",
    "exit_scenario",
    "deal_memo",
    "mote.",
    "workspace_chat",
)

_REPO_FROM_AGENT_RE = re.compile(
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#\d+"
)


def gcp_project_to_board_map() -> Dict[str, str]:
    """``BIGAS_USAGE_GCP_PROJECT_MAP=projectId:KEY,…`` overlays the defaults."""
    out = dict(DEFAULT_GCP_PROJECT_TO_BOARD)
    parsed = parse_csv_map(os.environ.get("BIGAS_USAGE_GCP_PROJECT_MAP") or "")
    for gcp_id, key in parsed.items():
        pid = (gcp_id or "").strip()
        board = (key or "").strip().upper()
        if pid and board:
            out[pid] = board
    return out


def repo_to_board_map() -> Dict[str, str]:
    """owner/repo (lower) → board key, including infra → SHARED_VM."""
    out: Dict[str, str] = {
        path.lower(): board
        for path, board in DEFAULT_INFRA_REPO_TO_BOARD.items()
    }
    for key, repo in repo_map().items():
        r = (repo or "").strip()
        if r:
            out[r.lower()] = (key or "").strip().upper()
    return out


def _repo_from_pr_url(pr_url: str) -> str:
    try:
        path = urlparse(pr_url).path.strip("/")
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    except Exception:
        pass
    return ""


def _repo_from_meta(meta: Mapping[str, object]) -> str:
    repo = str(meta.get("repo") or "").strip()
    if repo:
        return repo
    pr_url = str(meta.get("pr_url") or "").strip()
    if pr_url:
        found = _repo_from_pr_url(pr_url)
        if found:
            return found
    agent_name = str(meta.get("agent_name") or "").strip()
    if agent_name:
        m = _REPO_FROM_AGENT_RE.search(agent_name)
        if m:
            return m.group("repo")
    return ""


def _feature_board(feature: str) -> Optional[str]:
    f = (feature or "").strip().lower()
    if not f:
        return None
    for prefix in _VFA_FEATURE_PREFIXES:
        if f == prefix or f.startswith(prefix):
            return "VFA"
    return None


def board_project_for_event(ev: UsageEvent) -> str:
    """Best-effort board key for one usage event.

    Resolution order:
    1. GitHub repo (Cursor autofix / PR meta) via portfolio repo map
    2. GCP project id via ``BIGAS_USAGE_GCP_PROJECT_MAP`` / defaults
    3. Known VFA product features (Tavily, living analysis, …)
    4. ``app=vcfieldassistant`` → VFA
    5. Else BIG (shared board ops: PR review, chat, marketing, …)
    """
    meta = ev.meta if isinstance(ev.meta, dict) else {}
    repos = repo_to_board_map()

    repo = _repo_from_meta(meta)
    if repo:
        key = repos.get(repo.lower())
        if key:
            return key

    gcp = str(meta.get("gcp_project") or "").strip()
    if gcp:
        mapped = gcp_project_to_board_map().get(gcp)
        if mapped:
            return mapped

    feature_key = _feature_board(str(ev.feature or ""))
    if feature_key:
        return feature_key

    app = str(meta.get("app") or "").strip().lower()
    if app in {"vcfieldassistant", "vfa"}:
        return "VFA"

    return "BIG"
