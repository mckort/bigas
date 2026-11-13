"""Cockpit: one read model for "what needs me" across boards and deploys.

The cockpit is the start page for the daily loop: approve board cards,
watch AI work, ship releases, and run the weekly update. It reads what
already exists — board tickets and devops chat-thread state — and adds no
new storage.

Needs you
    Tickets waiting for a description or plan approval (they block the AI)
    and devops threads waiting for a yes/no deploy confirm.
Sign-off
    Tickets in Final approval: shipped, waiting to be marked done. Kept out
    of "Needs you" because nothing is blocked on them; closed in bulk with
    ``close_signoff``.
AI working
    Tickets in an AI column and devops threads with a running pipeline poll.
Ship
    One row per deploy target with its default unreleased version, how
    many tickets are in that cut, the changes the next prepare deploy or
    deploy will ship, whether those changes are ready, and the latest
    shipped version.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from bigas.tickets.constants import (
    AI_TRIGGER_STATUSES,
    is_in_release_cut,
    resolve_column_status,
)
from bigas.tickets.semver import versions_match

logger = logging.getLogger(__name__)

# Manual column → (kind, column after approve, column after send back).
# Send back re-runs the AI step that produced the work, so it reads the
# human comment posted with it.
MANUAL_STEPS: Dict[str, Dict[str, str]] = {
    "Description approval (manual)": {
        "kind": "description",
        "label": "Description ready",
        "approve_status": "Design and plan (AI)",
        "send_back_status": "Research and describe (AI)",
    },
    "Design approval (manual)": {
        "kind": "design",
        "label": "Plan ready",
        "approve_status": "In Progress (AI)",
        "send_back_status": "Design and plan (AI)",
    },
    "Final approval (manual)": {
        "kind": "final",
        "label": "Test in production, then mark done",
        "approve_status": "Done",
        "send_back_status": "In Progress (AI)",
    },
}

AI_STAGE_LABELS = {
    "Research and describe (AI)": "Researching",
    "Design and plan (AI)": "Planning",
    "In Progress (AI)": "Implementing",
}

_PREPARE_PHASE_LABELS = {
    "wait_review_workflow": "Waiting for the PR review workflow",
    "wait_actions_review": "Waiting for the PR review",
    "autofix": "Autofix running",
    "wait_merge": "Waiting for the merge",
}

_GPW_PHASE_LABELS = {
    "prepare_staging": "Building staging",
    "update_staging": "Updating staging",
    "teardown": "Tearing down staging",
    "backup": "Backing up production",
    "deploy_production": "Deploying to production",
    "review_autofix": "Review and autofix",
}


def _slim_ticket(ticket: Dict[str, Any], board: Dict[str, Any]) -> Dict[str, Any]:
    review = ticket.get("review") if isinstance(ticket.get("review"), dict) else {}
    return {
        "ticket_id": ticket.get("ticket_id"),
        "board_id": ticket.get("board_id") or board.get("board_id"),
        "key": ticket.get("key") or "",
        "title": ticket.get("title") or ticket.get("summary") or "",
        "status": ticket.get("status") or "",
        "issue_type": ticket.get("issue_type") or "",
        "fix_version": ticket.get("fix_version") or "",
        "labels": list(ticket.get("labels") or []),
        "project_key": (board.get("project_key") or "").upper(),
        "board_name": board.get("name") or "",
        "updated_at": ticket.get("updated_at") or "",
        "url": ticket.get("url") or "",
        "agent_url": ticket.get("agent_url") or "",
        "pr_url": str(review.get("pr_url") or ""),
        "comment_count": ticket.get("comment_count") or 0,
    }


def _ticket_items(
    boards: Iterable[Dict[str, Any]],
    tickets_by_board: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    needs: List[Dict[str, Any]] = []
    working: List[Dict[str, Any]] = []
    signoff: List[Dict[str, Any]] = []
    for board in boards:
        project_key = (board.get("project_key") or "").strip().upper()
        if not project_key:
            continue  # personal boards have no AI workflow
        for ticket in tickets_by_board.get(board.get("board_id") or "", []):
            if (ticket.get("issue_type") or "") in ("Epic", "Objective"):
                continue
            status = resolve_column_status(ticket.get("status") or "", project_key=project_key)
            if not status:
                continue
            slim = _slim_ticket(ticket, board)
            if status in MANUAL_STEPS:
                step = MANUAL_STEPS[status]
                (signoff if step["kind"] == "final" else needs).append(
                    {
                        "id": f"ticket:{slim['ticket_id']}",
                        "type": "ticket",
                        "kind": step["kind"],
                        "label": step["label"],
                        "approve_status": step["approve_status"],
                        "send_back_status": step["send_back_status"],
                        "since": slim["updated_at"],
                        "ticket": slim,
                    }
                )
            elif status in AI_TRIGGER_STATUSES:
                working.append(
                    {
                        "id": f"ticket:{slim['ticket_id']}",
                        "type": "ticket",
                        "stage": AI_STAGE_LABELS.get(status, status),
                        "since": slim["updated_at"],
                        "ticket": slim,
                    }
                )
    return {"needs": needs, "working": working, "signoff": signoff}


def _deploy_items(threads: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    needs: List[Dict[str, Any]] = []
    working: List[Dict[str, Any]] = []
    for thread in threads:
        if (thread.get("agent_id") or "") != "devops":
            continue
        thread_id = thread.get("thread_id") or ""
        updated = thread.get("updated_at") or ""

        pending = thread.get("pending_deploy")
        if isinstance(pending, dict) and pending.get("project_key"):
            kind = pending.get("kind") or "deploy"
            action = pending.get("action") or "deploy"
            version = pending.get("version") or ""
            target = (pending.get("project_key") or "").upper()
            title = f"Version {version}" if version else "Production deploy"
            if kind == "gpw" and action != "deploy":
                label = f"Confirm {action.replace('_', ' ')}"
            else:
                label = "Ready to deploy to production"
            needs.append(
                {
                    "id": f"deploy:{thread_id}",
                    "type": "deploy_confirm",
                    "kind": "ship",
                    "label": label,
                    "title": title,
                    "project_key": target,
                    "version": version,
                    "action": action,
                    "risk_level": pending.get("risk_level") or "",
                    "open_ticket_keys": list(pending.get("open_ticket_keys") or []),
                    "extra_commit_count": pending.get("extra_commit_count") or 0,
                    "thread_id": thread_id,
                    "since": updated,
                }
            )

        poll = thread.get("pending_deploy_poll")
        if isinstance(poll, dict) and poll.get("project_key"):
            phase = poll.get("phase") or ""
            runs = [r for r in (poll.get("triggered") or []) if isinstance(r, dict)]
            working.append(
                {
                    "id": f"pipeline:{thread_id}:deploy",
                    "type": "pipeline",
                    "project_key": (poll.get("project_key") or "").upper(),
                    "stage": _GPW_PHASE_LABELS.get(phase, phase.replace("_", " ") or "Running"),
                    "phase": phase,
                    "thread_id": thread_id,
                    "run_url": (runs[0].get("html_url") if runs else "") or "",
                    "since": poll.get("started_at") or updated,
                }
            )

        prep = thread.get("pending_prepare_poll")
        if isinstance(prep, dict) and prep.get("project_key"):
            phase = prep.get("phase") or ""
            working.append(
                {
                    "id": f"pipeline:{thread_id}:prepare",
                    "type": "pipeline",
                    "project_key": (prep.get("project_key") or "").upper(),
                    "version": prep.get("version") or "",
                    "stage": _PREPARE_PHASE_LABELS.get(phase, phase.replace("_", " ") or "Preparing"),
                    "phase": phase,
                    "thread_id": thread_id,
                    "run_url": prep.get("pr_url") or "",
                    "since": prep.get("started_at") or updated,
                }
            )
    return {"needs": needs, "working": working}


def latest_released_deploy(releases: Optional[Iterable[Dict[str, Any]]]) -> Optional[Dict[str, str]]:
    """Newest shipped board version, as ``vX.Y.Z`` plus ``released_at`` when known."""
    from bigas.tickets.semver import SemverError, parse_semver

    released = [
        item
        for item in (releases or [])
        if item and item.get("released") and str(item.get("name") or "").strip()
    ]
    if not released:
        return None

    def _sort_key(item: Dict[str, Any]) -> tuple:
        at = _parse_time(item.get("released_at")) or datetime.min.replace(tzinfo=timezone.utc)
        name = str(item.get("name") or "")
        try:
            parsed = parse_semver(name)
        except SemverError:
            parsed = (0, 0, 0)
        return (at, parsed)

    latest = max(released, key=_sort_key)
    name = str(latest.get("name") or "").strip()
    label = name if name.lower().startswith("v") else f"v{name}"
    return {
        "version": label,
        "released_at": str(latest.get("released_at") or "").strip(),
    }


def ship_deploy_ready(
    *,
    state: str,
    mode: str,
    release_mode: str,
    cut_count: Optional[int],
    ahead: Optional[bool],
    version: str = "",
) -> bool:
    """True when the ship card's deploy action should be the blue button.

    A confirm that is already waiting is ready. A versioned production cut
    with an open version and cards in it is ready. Otherwise only a positive
    git check counts: the production branch, the open version branch, or the
    staging candidate is ahead of what is already deployed. Unknown
    (``ahead is None``) stays quiet, and a versioned card with no open
    version stays quiet, so an idle card is not blue with nothing to ship.
    """
    if state == "waiting":
        return True
    if state == "running":
        return False
    if mode == "versioned" and release_mode != "development":
        if not version:
            return False
        if cut_count:
            return True
    return ahead is True


def _default_release(releases: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    open_releases = [r for r in releases if not r.get("released")]
    for release in open_releases:
        if release.get("is_default"):
            return release
    return open_releases[0] if open_releases else None


# Ship card progress: Review → Build (merge or staging) → Confirm → Deploy → Done.
SHIP_STEPS = ("Review", "Build", "Confirm", "Deploy", "Done")
# Enough to name what is waiting. The card shows the first few and a count.
_PENDING_CAP = 20
STEP_REVIEW, STEP_BUILD, STEP_CONFIRM, STEP_DEPLOY = 0, 1, 2, 3
PHASE_STEPS = {
    # versioned prepare deploy (pending_prepare_poll)
    "wait_review_workflow": STEP_REVIEW,
    "wait_actions_review": STEP_REVIEW,
    "autofix": STEP_REVIEW,
    "wait_merge": STEP_BUILD,
    # staging rehearsal (pending_deploy_poll)
    "review_autofix": STEP_REVIEW,
    "prepare_staging": STEP_BUILD,
    "update_staging": STEP_BUILD,
    "teardown": STEP_BUILD,
    "backup": STEP_DEPLOY,
    "deploy_production": STEP_DEPLOY,
}


def _pending_from_tickets(tickets: List[Dict[str, Any]]) -> tuple:
    """Newest cut cards first. ``(items, count)``; items stop at ``_PENDING_CAP``."""
    ordered = sorted(tickets, key=lambda ticket: str(ticket.get("updated_at") or ""), reverse=True)
    items: List[Dict[str, str]] = []
    count = 0
    for ticket in ordered:
        key = str(ticket.get("key") or "").strip()
        title = str(ticket.get("title") or ticket.get("summary") or "").strip()
        if not key and not title:
            continue
        count += 1
        if len(items) >= _PENDING_CAP:
            continue
        items.append(
            {
                "key": key,
                "title": title,
                "url": str(ticket.get("url") or "").strip(),
            }
        )
    return items, count


def _pending_from_freshness(fresh: Dict[str, Any]) -> tuple:
    """Commits the git check already found, in the same shape as cut cards."""
    raw = fresh.get("pending") if isinstance(fresh.get("pending"), list) else []
    items: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        title = str(item.get("title") or "").strip()
        if not key and not title:
            continue
        items.append({"key": key, "title": title, "url": str(item.get("url") or "").strip()})
        if len(items) >= _PENDING_CAP:
            break
    try:
        count = int(fresh.get("pending_count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count < len(items):
        count = len(items)
    return items, count


def _ship_rows(
    *,
    boards: Iterable[Dict[str, Any]],
    tickets_by_board: Dict[str, List[Dict[str, Any]]],
    projects: Iterable[Dict[str, Any]],
    command_groups: Iterable[Dict[str, Any]],
    releases_by_project: Dict[str, List[Dict[str, Any]]],
    deploy_items: Dict[str, List[Dict[str, Any]]],
    release_modes: Optional[Dict[str, str]] = None,
    deploy_freshness: Optional[Dict[str, Dict[str, Any]]] = None,
    tickets_loaded: bool = True,
) -> List[Dict[str, Any]]:
    """One row per deploy target.

    ``release_modes`` maps project key → ``production`` (versioned releases) or
    ``development`` (deploys from the production branch). Unknown keys count
    as production, which is how every versioned row behaved before.
    """
    modes = release_modes or {}
    freshness = deploy_freshness or {}

    def _with_deploy_cue(
        row: Dict[str, Any],
        *,
        cut_count: Optional[int],
        pending: Optional[tuple] = None,
    ) -> Dict[str, Any]:
        key = row["key"]
        releases = releases_by_project[key] if key in releases_by_project else []
        release_error = releases is None
        fresh = freshness.get(key) or {}
        board_latest = None if release_error else latest_released_deploy(releases or [])
        github_latest = fresh.get("latest") if isinstance(fresh.get("latest"), dict) else None
        ahead = fresh.get("ahead") if key in freshness else None
        row["latest_deploy"] = board_latest or github_latest or None
        row["deploy_ready"] = ship_deploy_ready(
            state=row["state"],
            mode=row["mode"],
            release_mode=row["release_mode"],
            cut_count=cut_count,
            ahead=ahead if isinstance(ahead, bool) else None,
            version=str(row.get("version") or ""),
        )
        # A versioned production cut is the prepare-deploy list. Development
        # and staging ship whatever git says is not deployed yet. An empty
        # cut still falls through to those commits.
        if pending is None:
            items, count = _pending_from_freshness(fresh if isinstance(fresh, dict) else {})
        else:
            items, count = pending
        row["pending"] = items
        row["pending_count"] = count
        return row
    tickets_by_project: Dict[str, List[Dict[str, Any]]] = {}
    for board in boards:
        key = (board.get("project_key") or "").strip().upper()
        if key:
            tickets_by_project.setdefault(key, []).extend(
                tickets_by_board.get(board.get("board_id") or "", [])
            )

    def _norm_key(value: Any) -> str:
        return (value or "").upper()

    def _index_by_key(items: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        indexed: Dict[str, Dict[str, Any]] = {}
        for item in items:
            item_key = _norm_key(item.get("project_key"))
            if item_key and item_key not in indexed:
                indexed[item_key] = item
        return indexed

    waiting_by_key = _index_by_key(deploy_items["needs"])
    running_by_key = _index_by_key(deploy_items["working"])

    def _state(key: str) -> Dict[str, Any]:
        item = waiting_by_key.get(key)
        if item:
            return {
                "state": "waiting",
                "detail": item["label"],
                "thread_id": item["thread_id"],
                "step": STEP_CONFIRM,
            }
        item = running_by_key.get(key)
        if item:
            return {
                "state": "running",
                "detail": item["stage"],
                "thread_id": item["thread_id"],
                "step": PHASE_STEPS.get(item.get("phase") or "", STEP_REVIEW),
            }
        return {"state": "idle", "detail": "", "thread_id": "", "step": None}

    rows: List[Dict[str, Any]] = []
    for group in command_groups:
        key = _norm_key(group.get("key"))
        if not key:
            continue
        rows.append(
            _with_deploy_cue(
                {
                    "key": key,
                    "name": group.get("name") or key,
                    "mode": "staging",
                    "release_mode": "production",
                    "version": "",
                    "cut_count": None,
                    "commands": list(group.get("commands") or []),
                    **_state(key),
                },
                cut_count=None,
            )
        )
    seen = {row["key"] for row in rows}
    for project in projects:
        key = _norm_key(project.get("key"))
        if not key or key in seen:
            continue
        # load_cockpit stores None for a project whose releases failed to load;
        # a missing key means the project has no releases yet.
        releases = releases_by_project[key] if key in releases_by_project else []
        release_error = releases is None
        release = None if release_error else _default_release(releases)
        version = (release or {}).get("name") or ""
        release_mode = modes.get(key, "production")
        if release_mode == "development":
            commands = [{"label": "Deploy", "prompt": f"deploy {key} to production"}]
        else:
            commands = [{"label": "Prepare deploy", "prompt": f"prepare deploy {key} {version}".strip()}]
        # A shell response has not read the board yet, so a cut of 0 would be a lie.
        cut_count: Optional[int] = None
        cut_pending = None
        if tickets_loaded:
            cut = [
                t
                for t in tickets_by_project.get(key, [])
                if version
                and str(t.get("fix_version") or "").lstrip("vV") == version.lstrip("vV")
                and is_in_release_cut(t.get("status") or "", project_key=key)
            ]
            cut_count = len(cut)
            cut_pending = _pending_from_tickets(cut) if release_mode != "development" and cut else None
        rows.append(
            _with_deploy_cue(
                {
                    "key": key,
                    "name": project.get("name") or key,
                    "mode": "versioned",
                    "release_mode": release_mode,
                    "version": version,
                    "cut_count": cut_count,
                    "release_error": release_error,
                    "commands": commands,
                    **_state(key),
                },
                cut_count=cut_count,
                pending=cut_pending,
            )
        )
    return rows


# A card that has sat in an AI column this long has almost certainly stopped:
# research and planning take minutes, implement at most a couple of hours.
STUCK_AFTER = timedelta(hours=24)
PIPELINE_STUCK_AFTER = timedelta(hours=6)
WEEK = timedelta(days=7)


def _parse_time(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _split_stuck(items: List[Dict[str, Any]], now: datetime) -> tuple:
    running, stuck = [], []
    for item in items:
        since = _parse_time(item.get("since"))
        limit = PIPELINE_STUCK_AFTER if item.get("type") == "pipeline" else STUCK_AFTER
        if since and now - since > limit:
            stuck.append({**item, "stuck": True})
        else:
            running.append(item)
    return running, stuck


_WEEK_SKIP_TYPES = ("Epic", "Objective")


def _week_card(ticket: Dict[str, Any], project: str) -> Dict[str, Any]:
    status = ticket.get("status") or ""
    return {
        "key": ticket.get("key") or "",
        "title": ticket.get("title") or ticket.get("summary") or "",
        "project_key": project,
        "status": resolve_column_status(status, project_key=project) or status,
        "updated_at": ticket.get("updated_at") or "",
    }


def _issue_sort_key(key: str) -> tuple:
    text = str(key or "")
    head, sep, tail = text.rpartition("-")
    if sep and tail.isdigit():
        return (head, int(tail))
    return (text, 0)


def _issues_on_version(
    tickets: Iterable[Dict[str, Any]],
    project: str,
    version: str,
) -> List[Dict[str, Any]]:
    """Board cards tagged with this fix version. Epics are not release contents."""
    name = (version or "").strip()
    if not name:
        return []
    issues = [
        _week_card(ticket, project)
        for ticket in tickets
        if (ticket.get("issue_type") or "") not in _WEEK_SKIP_TYPES
        and versions_match(str(ticket.get("fix_version") or ""), name)
    ]
    issues.sort(key=lambda card: _issue_sort_key(str(card.get("key") or "")), reverse=True)
    return issues


def _week_stats(
    boards: Iterable[Dict[str, Any]],
    tickets_by_board: Dict[str, List[Dict[str, Any]]],
    releases_by_project: Dict[str, Optional[List[Dict[str, Any]]]],
    now: datetime,
) -> Dict[str, Any]:
    """Cards that reached Final approval or Done, and releases shipped, in the last 7 days.

    Tickets carry no status history, so a card counts by its last update while
    in a shipped column. Good enough for a weekly glance. Each release also
    lists the cards tagged with that version, including ones last touched earlier.
    """
    since = now - WEEK
    shipped: Dict[str, int] = {}
    cards: List[Dict[str, Any]] = []
    tickets_by_project: Dict[str, List[Dict[str, Any]]] = {}
    for board in boards:
        key = (board.get("project_key") or "").strip().upper()
        if not key:
            continue
        tickets = list(tickets_by_board.get(board.get("board_id") or "", []) or [])
        tickets_by_project.setdefault(key, []).extend(tickets)
        for t in tickets:
            if (t.get("issue_type") or "") in _WEEK_SKIP_TYPES:
                continue
            updated = _parse_time(t.get("updated_at"))
            if updated and updated >= since and is_in_release_cut(t.get("status") or "", project_key=key):
                shipped[key] = shipped.get(key, 0) + 1
                cards.append(_week_card(t, key))
    releases = []
    for key, items in (releases_by_project or {}).items():
        project = str(key or "").strip().upper()
        for r in items or []:
            at = _parse_time(r.get("released_at"))
            if r.get("released") and at and at >= since:
                name = str(r.get("name") or "").strip()
                releases.append(
                    {
                        "project_key": project,
                        "name": name,
                        "released_at": r.get("released_at") or "",
                        "issues": _issues_on_version(tickets_by_project.get(project, []), project, name),
                    }
                )
    cards.sort(key=lambda card: str(card.get("updated_at") or ""), reverse=True)
    releases.sort(key=lambda item: str(item.get("released_at") or ""), reverse=True)
    return {
        "shipped": sum(shipped.values()),
        "shipped_by_project": shipped,
        "cards": cards,
        "releases": releases,
    }


def _sort_by_since(items: List[Dict[str, Any]], *, newest_first: bool) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda i: str(i.get("since") or ""), reverse=newest_first)


def build_cockpit(
    *,
    boards: List[Dict[str, Any]],
    tickets_by_board: Dict[str, List[Dict[str, Any]]],
    threads: List[Dict[str, Any]],
    projects: Optional[List[Dict[str, Any]]] = None,
    command_groups: Optional[List[Dict[str, Any]]] = None,
    releases_by_project: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    now: Optional[datetime] = None,
    release_modes: Optional[Dict[str, str]] = None,
    deploy_freshness: Optional[Dict[str, Dict[str, Any]]] = None,
    tickets_loaded: bool = True,
) -> Dict[str, Any]:
    """Assemble the cockpit payload from already-loaded data (pure function).

    ``tickets_loaded`` false is the fast shell: ship, week releases, and running
    pipelines, with Needs you left unset so the page can paint before the board
    scan. Counts for that section stay null rather than zero.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ticket_items = _ticket_items(boards, tickets_by_board) if tickets_loaded else {
        "needs": [],
        "working": [],
        "signoff": [],
    }
    deploy_items = _deploy_items(threads)
    # Deploy confirms first (they hold maintenance windows), then oldest cards.
    needs = deploy_items["needs"] + _sort_by_since(ticket_items["needs"], newest_first=False)
    working, stuck = _split_stuck(
        deploy_items["working"] + _sort_by_since(ticket_items["working"], newest_first=True), now
    )
    ship = _ship_rows(
        boards=boards,
        tickets_by_board=tickets_by_board,
        projects=projects or [],
        command_groups=command_groups or [],
        releases_by_project=releases_by_project or {},
        deploy_items=deploy_items,
        release_modes=release_modes,
        deploy_freshness=deploy_freshness,
        tickets_loaded=tickets_loaded,
    )
    signoff = _sort_by_since(ticket_items["signoff"], newest_first=True)
    week = _week_stats(boards, tickets_by_board, releases_by_project or {}, now)
    if not tickets_loaded:
        # Issues stay unset until the board scan finishes, so the drawer does
        # not show an empty release while the cards are still loading.
        week = {
            **week,
            "shipped": None,
            "shipped_by_project": None,
            "cards": None,
            "releases": [{**item, "issues": None} for item in (week.get("releases") or [])],
        }
        needs_out = None
        signoff_out = None
        needs_count = None
        signoff_count = None
    else:
        needs_out = needs
        signoff_out = signoff
        needs_count = len(needs)
        signoff_count = len(signoff)
    return {
        "partial": not tickets_loaded,
        "needs": needs_out,
        "working": working,
        "stuck": stuck,
        "signoff": signoff_out,
        "ship": ship,
        "ship_steps": list(SHIP_STEPS),
        "week": week,
        "counts": {
            "needs": needs_count,
            "working": len(working),
            "stuck": len(stuck),
            "signoff": signoff_count,
        },
    }


MAX_SIGNOFF_BATCH = 500


def close_signoff(user_id: str, ticket_ids: List[str]) -> Dict[str, Any]:
    """Move Final approval tickets to Done in one go.

    Each ticket is marked ``done_processed`` first, so the per-ticket Done note
    to the Product thread is skipped; one summary is posted instead.
    """
    from bigas.chat.activity import mirror_to_activity_feed, post_to_agent_thread
    from bigas.tickets.service import TicketService
    from bigas.tickets.store import get_ticket_store

    store = get_ticket_store()
    service = TicketService()
    closed: List[str] = []
    skipped: List[Dict[str, str]] = []
    for ticket_id in list(dict.fromkeys(ticket_ids))[:MAX_SIGNOFF_BATCH]:
        ticket = store.get_ticket(ticket_id)
        if not ticket:
            skipped.append({"ticket_id": ticket_id, "reason": "not found"})
            continue
        board = store.get_board(ticket.get("board_id") or "") or {}
        key = ticket.get("key") or ticket_id
        if board.get("user_id") != user_id:
            skipped.append({"ticket_id": ticket_id, "reason": "not yours"})
            continue
        project_key = (board.get("project_key") or "").upper()
        status = resolve_column_status(ticket.get("status") or "", project_key=project_key or None)
        if status != "Final approval (manual)":
            skipped.append({"ticket_id": ticket_id, "key": key, "reason": f"in {ticket.get('status')}"})
            continue
        try:
            store.update_ticket(ticket_id, done_processed=True)
            service.update_ticket(
                ticket_id, user_id=user_id, previous_status=ticket.get("status"), status="Done"
            )
            closed.append(key)
        except Exception as exc:  # keep going; report per ticket
            logger.warning("Cockpit sign-off failed for %s", key, exc_info=True)
            skipped.append({"ticket_id": ticket_id, "key": key, "reason": str(exc)[:120]})

    if closed:
        shown = ", ".join(closed[:20]) + (f" and {len(closed) - 20} more" if len(closed) > 20 else "")
        msg = f"Marked {len(closed)} shipped card{'s' if len(closed) != 1 else ''} done from the cockpit: {shown}."
        try:
            mirror_to_activity_feed(msg, type_="ticket", source="product")
            post_to_agent_thread("product", f"Progress note: {msg}")
        except Exception:
            logger.warning("Cockpit sign-off summary was not posted", exc_info=True)
    return {"closed": closed, "skipped": skipped}


def _load_board_tickets(service, boards: List[Dict[str, Any]], user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Tickets for every project board. Several boards are read at once."""
    tickets_by_board: Dict[str, List[Dict[str, Any]]] = {}
    board_ids = [
        board.get("board_id") or ""
        for board in boards
        if (board.get("project_key") or "").strip()
    ]

    def _one(board_id: str):
        try:
            return board_id, service.list_tickets(board_id, user_id=user_id)
        except Exception:
            logger.warning("Cockpit could not list tickets for board %s", board_id, exc_info=True)
            return board_id, []

    if len(board_ids) <= 1:
        for board_id in board_ids:
            loaded_id, tickets = _one(board_id)
            tickets_by_board[loaded_id] = tickets
        return tickets_by_board

    with ThreadPoolExecutor(max_workers=min(8, len(board_ids))) as pool:
        for loaded_id, tickets in pool.map(_one, board_ids):
            tickets_by_board[loaded_id] = tickets
    return tickets_by_board


def load_cockpit(
    user_id: str,
    *,
    include_tickets: bool = True,
    include_freshness: bool = True,
) -> Dict[str, Any]:
    """Load boards, tickets, devops threads and releases for one user.

    The shell (``include_tickets`` and ``include_freshness`` false) skips the
    board scan and the git compare so This week, Ship, and AI working can
    render before Needs you.
    """
    from bigas.chat.db import get_chat_store
    from bigas.tickets.releases import list_releases
    from bigas.tickets.service import TicketService

    service = TicketService()
    try:
        boards = service.list_boards(user_id)
    except Exception:
        logger.warning("Cockpit could not list boards", exc_info=True)
        boards = []
    tickets_by_board: Dict[str, List[Dict[str, Any]]] = {}
    ticket_pool = None
    ticket_future = None
    if include_tickets:
        # Overlap the board scan with releases and the git compare below.
        ticket_pool = ThreadPoolExecutor(max_workers=1)
        ticket_future = ticket_pool.submit(_load_board_tickets, service, boards, user_id)

    try:
        threads = get_chat_store().list_threads(user_id)
    except Exception:
        logger.warning("Cockpit could not list chat threads", exc_info=True)
        threads = []

    projects: List[Dict[str, Any]] = []
    command_groups: List[Dict[str, Any]] = []
    try:
        from bigas.resources.devops.prepare import list_shortcut_projects

        projects = list_shortcut_projects()
    except Exception:
        logger.warning("Cockpit could not list deploy projects", exc_info=True)
    try:
        from bigas.resources.devops.gpw_pipeline import list_gpw_command_shortcuts

        command_groups = list_gpw_command_shortcuts()
    except Exception:
        logger.warning("Cockpit could not list staging environments", exc_info=True)

    board_keys = {
        (b.get("project_key") or "").strip().upper() for b in boards if b.get("project_key")
    }
    releases_by_project: Dict[str, List[Dict[str, Any]]] = {}
    release_keys = {
        (item.get("key") or "").upper()
        for item in list(projects) + list(command_groups)
        if (item.get("key") or "").strip()
    }
    for key in release_keys:
        if key not in board_keys:
            continue
        try:
            releases_by_project[key] = list_releases(key)
        except Exception:
            logger.warning("Cockpit could not list releases for %s", key, exc_info=True)
            releases_by_project[key] = None  # shown as an error, not as "no version"
    # Only show deploy targets the user has a board for. Built-in staging
    # environments exist for every install, so they need the same filter.
    projects = [p for p in projects if (p.get("key") or "").upper() in board_keys]
    command_groups = [g for g in command_groups if (g.get("key") or "").upper() in board_keys]

    release_modes: Dict[str, str] = {}
    release_prefixes: Dict[str, str] = {}
    try:
        from bigas.go_live import release_state
    except Exception:
        logger.warning("Cockpit could not read release modes", exc_info=True)
    else:
        for project in projects:
            key = (project.get("key") or "").upper()
            try:
                state = release_state(key)
            except Exception:
                logger.warning("Cockpit could not read release mode for %s", key, exc_info=True)
                continue
            release_modes[key] = state["mode"]
            release_prefixes[key] = (state.get("release_prefix") or "").strip()

    freshness: Dict[str, Dict[str, Any]] = {}
    if include_freshness:
        try:
            from bigas.cockpit_freshness import lookup_deploy_freshness

            freshness = lookup_deploy_freshness(
                _freshness_specs(
                    projects,
                    command_groups,
                    release_modes,
                    release_prefixes,
                    releases_by_project,
                )
            )
        except Exception:
            logger.warning("Cockpit could not check for undeployed changes", exc_info=True)

    if ticket_future is not None:
        try:
            tickets_by_board = ticket_future.result()
        except Exception:
            logger.warning("Cockpit could not list tickets", exc_info=True)
            tickets_by_board = {}
        finally:
            ticket_pool.shutdown(wait=False)

    return build_cockpit(
        boards=boards,
        tickets_by_board=tickets_by_board,
        threads=threads,
        projects=projects,
        command_groups=command_groups,
        releases_by_project=releases_by_project,
        release_modes=release_modes,
        deploy_freshness=freshness,
        tickets_loaded=include_tickets,
    )


def _freshness_specs(
    projects: Iterable[Dict[str, Any]],
    command_groups: Iterable[Dict[str, Any]],
    release_modes: Dict[str, str],
    release_prefixes: Dict[str, str],
    releases_by_project: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """What each ship card needs from git: ahead-of-deploy, and a version if the board has none."""
    specs: List[Dict[str, Any]] = []
    for group in command_groups:
        key = (group.get("key") or "").upper()
        if not key:
            continue
        releases = releases_by_project.get(key)
        specs.append(
            {
                "key": key,
                "mode": "staging",
                "release_mode": "production",
                "version": "",
                "release_prefix": "",
                "need_latest": not latest_released_deploy(releases or []),
            }
        )
    seen = {spec["key"] for spec in specs}
    for project in projects:
        key = (project.get("key") or "").upper()
        if not key or key in seen:
            continue
        releases = releases_by_project.get(key)
        release = None if releases is None else _default_release(releases or [])
        specs.append(
            {
                "key": key,
                "mode": "versioned",
                "release_mode": release_modes.get(key, "production"),
                "version": (release or {}).get("name") or "",
                "release_prefix": release_prefixes.get(key) or "staging",
                "need_latest": releases is not None and not latest_released_deploy(releases or []),
            }
        )
    return specs
