"""Structured review links on internal-board tickets.

A ticket that produced a PR (or other deliverable) stores clickable review
targets on the ticket itself so chat and the board do not have to reconstruct
them from comments.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

from bigas.github_refs import parse_github_pr
from bigas.portfolio import site_urls_for_project
from bigas.resources.product.jira_automation.config import BIGAS_COMMENT_MARKER

logger = logging.getLogger(__name__)

_SKIP_DIR_PREFIXES = (
    "assets/",
    "css/",
    "js/",
    "static/",
    "src/",
    "tests/",
    "bigas/",
    "frontend/",
    "docs/",
    ".github/",
)
_SKIP_NAMES = {
    "robots.txt",
    "sitemap.xml",
    "llms.txt",
    "_redirects",
    "package.json",
    "package-lock.json",
}

_CURSOR_AGENT_RE = re.compile(
    r"https?://(?:www\.)?cursor\.com/agents/[A-Za-z0-9._-]+",
    re.I,
)


def empty_review() -> Dict[str, Any]:
    return {"pr_url": "", "pr_title": "", "items": []}


def cursor_agent_url(agent_url: str = "", agent_id: str = "") -> str:
    url = (agent_url or "").strip()
    if url:
        return url
    aid = (agent_id or "").strip()
    if aid.startswith("http"):
        return aid
    if aid:
        return f"https://cursor.com/agents/{aid}"
    return ""


def infer_agent_url(ticket: Optional[Dict[str, Any]]) -> str:
    """Prefer the stored field, then comments (stuck In Progress cards)."""
    raw = ticket or {}
    stored = cursor_agent_url(str(raw.get("agent_url") or ""), str(raw.get("agent_id") or ""))
    if stored:
        return stored
    review = raw.get("review") if isinstance(raw.get("review"), dict) else {}
    stored = cursor_agent_url(str(review.get("agent_url") or ""), str(review.get("agent_id") or ""))
    if stored:
        return stored
    for comment in reversed(list(raw.get("comments") or [])):
        if not isinstance(comment, dict):
            continue
        match = _CURSOR_AGENT_RE.search(str(comment.get("body") or ""))
        if match:
            return match.group(0)
    return ""


def attach_implement_agent(
    issue_key: str,
    *,
    agent_url: str = "",
    agent_id: str = "",
) -> Dict[str, Any]:
    """Persist the Cursor implement agent URL on the internal-board ticket."""
    from bigas.tickets.store import get_ticket_store

    key = (issue_key or "").strip().upper()
    url = cursor_agent_url(agent_url, agent_id)
    aid = (agent_id or "").strip()
    if aid.startswith("http"):
        aid = ""
    if not key or not url:
        return {}
    store = get_ticket_store()
    ticket = store.get_ticket_by_key(key)
    if not ticket:
        return {}
    payload: Dict[str, Any] = {"agent_url": url}
    if aid:
        payload["agent_id"] = aid
    store.update_ticket(ticket["ticket_id"], **payload)
    return payload


def normalize_review(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    items: List[Dict[str, str]] = []
    seen = set()
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        kind = str(item.get("kind") or "page").strip() or "page"
        label = str(item.get("label") or url).strip() or url
        items.append({"kind": kind, "label": label, "url": url})
    return {
        "pr_url": str(raw.get("pr_url") or "").strip(),
        "pr_title": str(raw.get("pr_title") or "").strip(),
        "items": items,
    }


def merge_review(
    existing: Any,
    *,
    pr_url: str = "",
    pr_title: str = "",
    items: Optional[Sequence[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    current = normalize_review(existing)
    url = (pr_url or "").strip()
    title = (pr_title or "").strip()
    if url:
        current["pr_url"] = url
    if title:
        current["pr_title"] = title
    if items:
        current = normalize_review({**current, "items": list(current["items"]) + list(items)})
    return current


def public_page_url(path: str, *, site_base: str) -> Optional[str]:
    """Map a repo file path to a public site URL when the file is a static page."""
    rel = (path or "").replace("\\", "/").lstrip("./")
    if not rel or rel.startswith("../"):
        return None
    name = rel.rsplit("/", 1)[-1].lower()
    if name in _SKIP_NAMES:
        return None
    if any(rel.startswith(prefix) or f"/{prefix}" in f"/{rel}" for prefix in _SKIP_DIR_PREFIXES):
        if not rel.endswith(".html"):
            return None
        if rel.startswith("assets/") or "/assets/" in rel:
            return None
    if not rel.endswith(".html"):
        return None
    slug = rel[:-5]
    if slug in {"index", ""}:
        page = "/"
    elif slug.endswith("/index"):
        page = "/" + slug[: -len("index")]
    else:
        page = "/" + slug
    page = re.sub(r"/+", "/", page)
    if not page.endswith("/") and page.count("/") == 1 and page.strip("/") == "":
        page = "/"
    base = (site_base or "").rstrip("/")
    if not base:
        return None
    return f"{base}{page}" if page != "/" else f"{base}/"


def _label_from_path(path: str) -> str:
    slug = (path or "").replace("\\", "/").rsplit("/", 1)[-1]
    if slug.endswith(".html"):
        slug = slug[:-5]
    if slug in {"index", ""}:
        return "Home"
    return slug.replace("-", " ").replace("_", " ").strip() or slug


def public_page_items(
    file_paths: Iterable[str],
    *,
    project_key: Optional[str] = None,
    site_base: Optional[str] = None,
) -> List[Dict[str, str]]:
    base = (site_base or "").strip()
    if not base:
        urls = site_urls_for_project(project_key)
        base = (urls[0] if urls else "").rstrip("/")
        if base and "://" in base:
            parsed = urlparse(base)
            host = parsed.netloc
            if host and not host.startswith("www."):
                base = f"{parsed.scheme}://www.{host}"
    items: List[Dict[str, str]] = []
    seen = set()
    for path in file_paths:
        url = public_page_url(str(path), site_base=base)
        if not url or url in seen:
            continue
        seen.add(url)
        items.append({"kind": "page", "label": _label_from_path(str(path)), "url": url})
    return items


def list_pr_file_paths(pr_url: str, *, github_token: str = "") -> List[str]:
    parsed = parse_github_pr(pr_url)
    token = (github_token or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not parsed or not token:
        return []
    repo, number = parsed
    owner, name = repo.split("/", 1)
    try:
        from bigas.resources.cto.pr_review.github_client import GitHubPRCommentClient

        return GitHubPRCommentClient(token=token).list_pr_files(owner, name, number)
    except Exception:
        logger.warning("Could not list files for %s", pr_url, exc_info=True)
        return []


def format_review_comment(review: Dict[str, Any]) -> str:
    data = normalize_review(review)
    lines = [f"{BIGAS_COMMENT_MARKER} Ready for review"]
    pr_url = data.get("pr_url") or ""
    pr_title = data.get("pr_title") or "Pull request"
    if pr_url:
        lines.append(f"PR: [{pr_title}]({pr_url})")
    items = data.get("items") or []
    if items:
        lines.append("Pages:")
        for item in items:
            lines.append(f"- [{item['label']}]({item['url']})")
    return "\n".join(lines)


def attach_review_from_pr(
    issue_key: str,
    *,
    pr_url: str,
    pr_title: str = "",
    file_paths: Optional[Sequence[str]] = None,
    project_key: Optional[str] = None,
    github_token: str = "",
    comment: bool = True,
) -> Dict[str, Any]:
    """Persist PR + inferred page URLs on the ticket and optionally comment."""
    from bigas.tickets.store import get_ticket_store

    key = (issue_key or "").strip().upper()
    url = (pr_url or "").strip()
    if not key or not url:
        return empty_review()

    store = get_ticket_store()
    ticket = store.get_ticket_by_key(key)
    if not ticket:
        return empty_review()

    paths = list(file_paths) if file_paths is not None else list_pr_file_paths(url, github_token=github_token)
    proj = (
        (project_key or "").strip().upper()
        or str(ticket.get("project_key") or "").strip().upper()
        or key.split("-", 1)[0]
    )
    pages = public_page_items(paths, project_key=proj)
    review = merge_review(ticket.get("review"), pr_url=url, pr_title=pr_title, items=pages)
    store.update_ticket(ticket["ticket_id"], review=review)
    if comment:
        store.add_comment(
            ticket["ticket_id"],
            format_review_comment(review),
            author_name="Bigas",
        )
    return review
