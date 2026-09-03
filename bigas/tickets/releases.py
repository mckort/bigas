"""Board release lifecycle: create/delete, close on ship/deploy, carry-forward."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bigas.resources.product.github_release import GitHubReleaseError, create_github_release
from bigas.resources.product.jira_automation.config import JiraAutomationConfig
from bigas.resources.product.release_workflow import normalize_semver_tag
from bigas.tickets.constants import is_in_release_cut
from bigas.tickets.release_store import get_release_store
from bigas.tickets.semver import (
    SemverError,
    next_product_release,
    normalize_version_name,
    parse_semver,
    version_from_git_ref,
    versions_match,
)
from bigas.tickets.store import get_ticket_store

logger = logging.getLogger(__name__)


class ReleaseError(RuntimeError):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_releases(project_key: str) -> List[Dict[str, Any]]:
    return get_release_store().list_releases(project_key)


def create_release(project_key: str, *, name: str, is_default: bool = False) -> Dict[str, Any]:
    try:
        return get_release_store().create_release(
            project_key, name=name, is_default=is_default
        )
    except SemverError as exc:
        raise ReleaseError(str(exc)) from exc
    except ValueError as exc:
        raise ReleaseError(str(exc)) from exc


def delete_release(project_key: str, release_id: str) -> bool:
    store = get_release_store()
    item = store.get_release(release_id)
    if not item:
        return False
    if (item.get("project_key") or "").upper() != (project_key or "").strip().upper():
        return False
    return store.delete_release(release_id)


def set_default_release(project_key: str, release_id: str, is_default: bool = True) -> Dict[str, Any]:
    store = get_release_store()
    item = store.get_release(release_id)
    if not item or (item.get("project_key") or "").upper() != (project_key or "").strip().upper():
        raise ReleaseError("Release not found")
    if item.get("released") and is_default:
        raise ReleaseError("A released version cannot be the default")
    updated = store.update_release(release_id, is_default=bool(is_default))
    if not updated:
        raise ReleaseError("Release not found")
    return updated


def default_fix_version(project_key: str) -> Optional[str]:
    item = get_release_store().get_default_release(project_key)
    if item:
        return (item.get("name") or "").strip() or None
    from bigas.resources.product.release_workflow import active_fix_version_from_env

    return active_fix_version_from_env(project_key)


def tickets_on_version(project_key: str, version: str) -> List[Dict[str, Any]]:
    """All board tickets assigned to a release, Done and open."""
    store = get_ticket_store()
    tickets = store.list_tickets_by_project(project_key)
    wanted = normalize_version_name(version)
    return [
        ticket
        for ticket in tickets
        if versions_match(ticket.get("fix_version"), wanted)
    ]


def _open_tickets_on_version(project_key: str, version: str) -> List[Dict[str, Any]]:
    return [
        ticket
        for ticket in tickets_on_version(project_key, version)
        if not is_in_release_cut(ticket.get("status") or "", project_key=project_key)
    ]


def _next_existing_unreleased(project_key: str, released_name: str) -> Optional[str]:
    """Lowest unreleased version greater than released_name, or None."""
    try:
        released = parse_semver(released_name)
    except SemverError:
        return None
    candidates: List[tuple] = []
    for item in get_release_store().list_releases(project_key):
        if item.get("released"):
            continue
        name = (item.get("name") or "").strip()
        try:
            ver = parse_semver(name)
        except SemverError:
            continue
        if ver > released:
            candidates.append((ver, name))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _ensure_next_minor(project_key: str, released_name: str) -> str:
    nxt = next_product_release(released_name)
    store = get_release_store()
    existing = store.get_release_by_name(project_key, nxt)
    if existing:
        return existing["name"]
    created = store.create_release(project_key, name=nxt, is_default=False)
    return created["name"]


def _move_open_tickets(project_key: str, from_version: str, to_version: str) -> List[Dict[str, str]]:
    store = get_ticket_store()
    moved: List[Dict[str, str]] = []
    for ticket in _open_tickets_on_version(project_key, from_version):
        store.update_ticket(ticket["ticket_id"], fix_version=to_version)
        try:
            store.add_comment(
                ticket["ticket_id"],
                (
                    f"{from_version} was released without this ticket. "
                    f"Moved to {to_version}."
                ),
                author_name="Bigas",
            )
        except Exception:
            logger.warning("Could not comment on %s after release carry-forward", ticket.get("key"))
        moved.append({"key": ticket.get("key") or "", "from": from_version, "to": to_version})
    return moved


def _clear_open_ticket_versions(project_key: str, from_version: str) -> List[Dict[str, str]]:
    store = get_ticket_store()
    cleared: List[Dict[str, str]] = []
    for ticket in _open_tickets_on_version(project_key, from_version):
        store.update_ticket(ticket["ticket_id"], fix_version=None)
        try:
            store.add_comment(
                ticket["ticket_id"],
                (
                    f"{from_version} was released without this ticket. "
                    "Version assignment removed (no next release exists)."
                ),
                author_name="Bigas",
            )
        except Exception:
            logger.warning("Could not comment on %s after release unassign", ticket.get("key"))
        cleared.append({"key": ticket.get("key") or "", "from": from_version, "to": ""})
    return cleared


def _notify_devops(
    project_key: str,
    version: str,
    next_version: Optional[str],
    moved: List[Dict[str, str]],
) -> None:
    lines = [
        f"**{project_key} {version} is released.**",
        "",
    ]
    if moved and next_version:
        lines.append(
            f"{len(moved)} open ticket(s) were not in this release and moved to **{next_version}**:"
        )
        for item in moved:
            lines.append(f"- {item['key']}: {item['from']} → {item['to']}")
    elif moved:
        lines.append(
            f"{len(moved)} open ticket(s) were not in this release and had their version cleared:"
        )
        for item in moved:
            lines.append(f"- {item['key']}")
    else:
        lines.append("No open tickets needed to be moved.")
    try:
        from bigas.chat.activity import post_to_agent_thread

        post_to_agent_thread(
            "devops",
            "\n".join(lines),
            metadata={"source": "board_release", "project_key": project_key, "version": version},
        )
    except Exception:
        logger.warning("Could not post release carry-forward to DevOps chat", exc_info=True)


def _publish_github_release(
    project_key: str,
    version: str,
    *,
    target_ref: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        logger.warning("GITHUB_TOKEN missing; skipped GitHub release for %s %s", project_key, version)
        return None
    try:
        cfg = JiraAutomationConfig.from_env()
        repo = cfg.repo_for_project(project_key) or ""
    except Exception:
        repo = ""
    if not repo or "/" not in repo:
        logger.warning("No GitHub repo mapped for %s; skipped GitHub release", project_key)
        return None
    owner, name = repo.split("/", 1)
    try:
        return create_github_release(
            token=token,
            owner=owner.strip(),
            repo=name.strip(),
            fix_version=version,
            title=f"Release {version}",
            body="",
            target_commitish=(target_ref or "").strip() or None,
        )
    except GitHubReleaseError as exc:
        raise ReleaseError(str(exc)) from exc


def close_release(
    project_key: str,
    version: str,
    *,
    git_sha: Optional[str] = None,
    target_ref: Optional[str] = None,
    create_github: bool = True,
    create_next_if_missing: bool = True,
) -> Dict[str, Any]:
    """Mark a board release released, carry forward leftover open tickets, notify DevOps.

    Done and Final approval stay on this version (prod-tested before Done).
    Other open tickets move to the next existing unreleased version, or lose
    their version assignment if none exists. Ship still creates the next minor
    when ``create_next_if_missing`` is true.
    """
    proj = (project_key or "").strip().upper()
    try:
        name = normalize_version_name(version)
    except SemverError as exc:
        raise ReleaseError(str(exc)) from exc

    store = get_release_store()
    item = store.get_release_by_name(proj, name)
    if not item:
        raise ReleaseError(f"Release {name} not found for {proj}")

    if item.get("released"):
        return {
            "release": item,
            "already_released": True,
            "moved": [],
            "next_version": None,
            "github_release": None,
        }

    gh_release = None
    if create_github:
        gh_release = _publish_github_release(proj, name, target_ref=target_ref)

    tag_name = None
    try:
        tag_name = normalize_semver_tag(name)
    except ValueError:
        tag_name = f"v{name}"
    if gh_release:
        tag_name = gh_release.get("tag_name") or tag_name

    if create_next_if_missing:
        next_version = _ensure_next_minor(proj, name)
        moved = _move_open_tickets(proj, name, next_version)
    else:
        next_version = _next_existing_unreleased(proj, name)
        if next_version:
            moved = _move_open_tickets(proj, name, next_version)
        else:
            moved = _clear_open_ticket_versions(proj, name)
    updated = store.update_release(
        item["release_id"],
        released=True,
        released_at=_utcnow_iso(),
        is_default=False,
        git_sha=(git_sha or "").strip() or None,
        git_tag=tag_name,
    )
    _notify_devops(proj, name, next_version, moved)
    return {
        "release": updated or item,
        "already_released": False,
        "moved": moved,
        "next_version": next_version,
        "github_release": {
            "tag_name": (gh_release or {}).get("tag_name") or tag_name,
            "html_url": (gh_release or {}).get("html_url"),
        }
        if (gh_release or tag_name)
        else None,
    }


def project_key_for_repo(repo: str) -> Optional[str]:
    wanted = (repo or "").strip().lower()
    if not wanted:
        return None
    try:
        cfg = JiraAutomationConfig.from_env()
    except Exception:
        return None
    for key, mapped in (cfg.project_repos or {}).items():
        if (mapped or "").strip().lower() == wanted:
            return key
    return None


def maybe_close_board_release_from_workflow(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Close a board release when a deploy workflow succeeds on a semver tag."""
    action = (payload.get("action") or "").strip().lower()
    run = payload.get("workflow_run") or {}
    if action != "completed" or (run.get("conclusion") or "").strip().lower() != "success":
        return None
    path = f"{run.get('path') or ''} {run.get('name') or ''}".lower()
    if "deploy" not in path:
        return None
    ref = (run.get("head_branch") or "").strip()
    version = version_from_git_ref(ref)
    if not version:
        return None
    repo_obj = payload.get("repository") or {}
    owner = ((repo_obj.get("owner") or {}).get("login") or "").strip()
    name = (repo_obj.get("name") or "").strip()
    if not owner or not name:
        return None
    project_key = project_key_for_repo(f"{owner}/{name}")
    if not project_key:
        return None
    return close_release_from_deploy_ref(project_key, ref)


def close_release_from_deploy_ref(
    project_key: Optional[str],
    ref: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Close the board release when a successful deploy used a semver tag."""
    proj = (project_key or "").strip().upper()
    version = version_from_git_ref(ref or "")
    if not proj or not version:
        return None
    if not get_release_store().get_release_by_name(proj, version):
        return None
    try:
        return close_release(proj, version, target_ref=ref, create_github=False)
    except ReleaseError as exc:
        logger.warning("Could not close %s %s after deploy: %s", proj, version, exc)
        return None


def ship_release(
    project_key: str,
    version: str,
    *,
    target_ref: Optional[str] = None,
    deploy: bool = True,
) -> Dict[str, Any]:
    """Create the GitHub release and start a versioned deploy. Board close happens on success."""
    proj = (project_key or "").strip().upper()
    try:
        name = normalize_version_name(version)
    except SemverError as exc:
        raise ReleaseError(str(exc)) from exc
    item = get_release_store().get_release_by_name(proj, name)
    if not item:
        raise ReleaseError(f"Release {name} not found for {proj}")
    if item.get("released"):
        raise ReleaseError(f"{name} is already released")

    tag_name = (item.get("git_tag") or "").strip() or normalize_semver_tag(name)
    gh_release = None
    if (item.get("git_tag") or "").strip():
        gh_release = {"tag_name": tag_name}
    else:
        gh_release = _publish_github_release(proj, name, target_ref=target_ref)
        if gh_release:
            tag_name = gh_release.get("tag_name") or tag_name
            get_release_store().update_release(
                item["release_id"],
                git_tag=tag_name,
            )

    deploy_result = None
    if deploy:
        from bigas.resources.devops.service import DevOpsError, trigger_deployment

        try:
            deploy_result = trigger_deployment(project_key=proj, ref=tag_name)
        except DevOpsError as exc:
            raise ReleaseError(f"GitHub release created but deploy failed: {exc}") from exc

    return {
        "release": get_release_store().get_release(item["release_id"]),
        "github_release": {
            "tag_name": (gh_release or {}).get("tag_name") or tag_name,
            "html_url": (gh_release or {}).get("html_url"),
        }
        if (gh_release or tag_name)
        else None,
        "deploy": deploy_result,
    }
