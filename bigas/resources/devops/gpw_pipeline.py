"""GPW store staging rehearsal and production deploy.

Chat commands, all for project GPW-PROD (Green-Promo-Wear-Global/GPW):

- prepare staging — review develop against production, then build a full
  staging stack from the live production image and a production database copy
- update staging — apply develop, migrate, post-check, then the operator logs in
- teardown staging — destroy the staging stack after a confirm
- prepare deploy GPW-PROD — require a green rehearsal, back up production,
  ask, then deploy. Maintenance stays on until the post-deploy check is green.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PROJECT_KEY = "GPW-PROD"
REPO = "Green-Promo-Wear-Global/GPW"
PROD_BRANCH = "production"
CANDIDATE_BRANCH = "develop"
STAGING_URL = "https://staging.greenpromowear.com"
PROD_URL = "https://store.greenpromowear.com"
_POLL_TIMEOUT_SEC = 90 * 60

_PREPARE_STAGING_RE = re.compile(r"\bprepare\s+staging\b", re.I)
_UPDATE_STAGING_RE = re.compile(r"\bupdate\s+staging\b", re.I)
_TEARDOWN_RE = re.compile(r"\bteardown\s+staging\b", re.I)
_PREPARE_DEPLOY_RE = re.compile(r"\bprepare\s+deploy\s+gpw-prod\b", re.I)

_WORKFLOWS = {
    "prepare_staging": "prepare-staging.yml",
    "update_staging": "update-staging.yml",
    "teardown": "teardown-staging.yml",
    "backup": "backup-production.yml",
    "deploy_production": "deploy-production.yml",
}

_AUTOFIX_PHASES = {"update_staging", "deploy_production"}


def keep_maintenance(*, health_ok: bool) -> bool:
    """Maintenance stays up until the post-deploy check is green."""
    return not health_ok


def is_gpw_command(text: str) -> bool:
    blob = text or ""
    return bool(
        _PREPARE_STAGING_RE.search(blob)
        or _UPDATE_STAGING_RE.search(blob)
        or _TEARDOWN_RE.search(blob)
        or _PREPARE_DEPLOY_RE.search(blob)
    )


def list_gpw_command_shortcuts() -> list:
    """Chat composer shortcuts. Only GPW-PROD uses this deploy flow."""
    from bigas.portfolio import brand_name

    return [
        {
            "key": PROJECT_KEY,
            "name": brand_name(PROJECT_KEY) or PROJECT_KEY,
            "commands": [
                {"label": "Prepare staging", "prompt": "prepare staging"},
                {"label": "Update staging", "prompt": "update staging"},
                {"label": "Teardown staging", "prompt": "teardown staging"},
                {"label": "Prepare deploy", "prompt": "prepare deploy GPW-PROD"},
            ],
        }
    ]


def parse_gpw_command(text: str) -> str:
    blob = text or ""
    if _PREPARE_DEPLOY_RE.search(blob):
        return "prepare_deploy"
    if _TEARDOWN_RE.search(blob):
        return "teardown"
    if _UPDATE_STAGING_RE.search(blob):
        return "update_staging"
    if _PREPARE_STAGING_RE.search(blob):
        return "prepare_staging"
    return ""


def _store():
    from bigas.chat.db import get_chat_store

    return get_chat_store()


def _post(thread_id: Optional[str], content: str, *, status: Optional[str] = None) -> None:
    if not thread_id or not (content or "").strip():
        return
    meta: Dict[str, Any] = {"agent_id": "devops", "pipeline": True}
    if status:
        meta["status"] = status
    _store().add_message(thread_id, role="assistant", content=content.strip(), metadata=meta)


def _complete_progress(thread_id: Optional[str]) -> None:
    if not thread_id:
        return
    store = _store()
    if not hasattr(store, "patch_message"):
        return
    for message in store.list_messages(thread_id):
        meta = message.get("metadata") or {}
        if meta.get("pipeline") and meta.get("status") == "in_progress":
            store.patch_message(message["message_id"], metadata={"status": "complete"})


def _thread(thread_id: Optional[str]) -> Dict[str, Any]:
    if not thread_id:
        return {}
    return _store().get_thread(thread_id) or {}


def _patch(thread_id: Optional[str], **fields: Any) -> None:
    if not thread_id or not hasattr(_store(), "patch_thread"):
        return
    _store().patch_thread(thread_id, **fields)


def pending_gpw_action(thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
    pending = _thread(thread_id).get("pending_deploy")
    if isinstance(pending, dict) and pending.get("kind") == "gpw":
        return pending
    return None


def _rehearsal(thread_id: Optional[str]) -> Dict[str, Any]:
    data = _thread(thread_id).get("gpw_rehearsal")
    return dict(data) if isinstance(data, dict) else {}


def _set_rehearsal(thread_id: Optional[str], payload: Dict[str, Any]) -> None:
    _patch(thread_id, gpw_rehearsal=payload)


def _github():
    from bigas.resources.devops.service import _github_client

    return _github_client()


def _owner_name() -> tuple:
    owner, name = REPO.split("/", 1)
    return owner, name


def review_candidate() -> Dict[str, Any]:
    """Review develop against production. Returns shas and whether it may proceed."""
    from bigas.resources.cto.autofix.heuristics import review_is_ready_to_merge
    from bigas.resources.cto.pr_review.service import PRReviewService

    client = _github()
    owner, name = _owner_name()
    compare = client.compare_refs(owner, name, PROD_BRANCH, CANDIDATE_BRANCH)
    ahead = int(compare.get("ahead_by") or 0)
    production_sha = client.get_ref_sha(owner, name, PROD_BRANCH)
    candidate_sha = client.get_ref_sha(owner, name, CANDIDATE_BRANCH)
    if ahead <= 0:
        return {
            "ok": False,
            "reason": "develop is not ahead of production. Nothing new to stage.",
            "production_sha": production_sha,
            "candidate_sha": candidate_sha,
        }
    diff = client.get_compare_diff(owner, name, PROD_BRANCH, CANDIDATE_BRANCH)
    review = PRReviewService().review(diff)
    body = (review.text or "").strip()
    ready = review_is_ready_to_merge(body)
    pr = client.find_open_pull_request(owner, name, head=CANDIDATE_BRANCH, base=PROD_BRANCH)
    return {
        "ok": ready,
        "review": body,
        "production_sha": production_sha,
        "candidate_sha": candidate_sha,
        "pr_number": (pr or {}).get("number"),
        "pr_url": (pr or {}).get("html_url") or "",
    }


def _launch_review_autofix(result: Dict[str, Any]) -> str:
    repo = REPO
    pr_number = result.get("pr_number")
    body = result.get("review") or ""
    if pr_number:
        from bigas.resources.cto.autofix.service import AutofixService

        launched = AutofixService().run(
            repo=repo,
            pr_number=int(pr_number),
            review_body=body,
        )
        url = (launched.get("agent_url") or launched.get("pr_url") or "").strip()
        return f"Autofix started on the open PR{(': ' + url) if url else '.'}"
    from bigas.resources.cto.deploy_hotfix import launch_failed_deploy_fix

    launched = launch_failed_deploy_fix(
        repo=repo,
        failures=[
            {
                "workflow": "prepare-staging review",
                "run_id": "",
                "conclusion": "review",
                "excerpt": body[:4000] or "(empty review)",
            }
        ],
        starting_ref=CANDIDATE_BRANCH,
        extra_instructions=(
            "This is a code review of develop against production for the GPW store. "
            "Fix the review findings on develop. Do not deploy and do not touch production."
        ),
    )
    url = (launched.get("agent_url") or "").strip()
    return f"Autofix started on `{CANDIDATE_BRANCH}`{(': ' + url) if url else '.'}"


def dispatch_gpw_workflow(phase: str, inputs: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    from bigas.resources.devops.service import dispatch_workflow

    workflow = _WORKFLOWS[phase]
    client = _github()
    owner, name = _owner_name()
    ref = client.get_default_branch(owner, name)
    return dispatch_workflow(repo=REPO, workflow=workflow, ref=ref, inputs=inputs or None)


def _begin_poll(
    thread_id: Optional[str],
    *,
    phase: str,
    run: Dict[str, Any],
    production_sha: str = "",
    candidate_sha: str = "",
) -> Dict[str, Any]:
    _patch(
        thread_id,
        pending_deploy_poll={
            "kind": "gpw",
            "phase": phase,
            "repo": REPO,
            "triggered": [
                {
                    "workflow": run.get("workflow") or _WORKFLOWS.get(phase) or phase,
                    "run_id": run.get("run_id"),
                    "html_url": run.get("html_url") or "",
                }
            ],
            "production_sha": production_sha,
            "candidate_sha": candidate_sha,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_lines": [],
            "failed_runs": [],
            "done_run_ids": [],
        },
        has_pending_deploy_poll=True,
    )
    url = run.get("html_url") or ""
    run_id = run.get("run_id") or "?"
    _post(
        thread_id,
        f"Workflow `{run.get('workflow') or phase}` run #{run_id} is running"
        + (f" — {url}" if url else ".")
        + "\n\nI'll post here when it finishes.",
        status="in_progress",
    )
    return {"status": "in_progress", "summary": f"{phase} started", "deploy_poll_active": True}


def _start_prepare_staging(thread_id: Optional[str]) -> Dict[str, Any]:
    _post(
        thread_id,
        f"Reviewing `{CANDIDATE_BRANCH}` against `{PROD_BRANCH}` before building staging.",
        status="in_progress",
    )
    try:
        reviewed = review_candidate()
    except Exception as exc:
        logger.exception("GPW prepare-staging review failed")
        _complete_progress(thread_id)
        _post(thread_id, f"Review failed: {exc}")
        return {"status": "complete", "summary": str(exc)}
    if not reviewed.get("ok"):
        reason = (reviewed.get("reason") or "").strip()
        if reason:
            _complete_progress(thread_id)
            _post(thread_id, reason)
            return {"status": "complete", "summary": reason}
        note = ""
        try:
            note = _launch_review_autofix(reviewed)
        except Exception as exc:
            logger.exception("GPW review autofix failed")
            note = f"Could not start autofix ({exc})."
        _complete_progress(thread_id)
        preview = (reviewed.get("review") or "").strip()
        truncated = len(preview) > 1200
        if truncated:
            preview = preview[:1200] + "…"
        pr_url = (reviewed.get("pr_url") or "").strip()
        if pr_url:
            review_link = f"Full review: {pr_url}"
        else:
            owner, name = _owner_name()
            compare_url = (
                f"https://github.com/{owner}/{name}/compare/"
                f"{PROD_BRANCH}...{CANDIDATE_BRANCH}"
            )
            review_link = f"Full diff: {compare_url}"
        _post(
            thread_id,
            "Review is not clean, so staging was not built.\n\n"
            + (preview + "\n\n" if preview else "")
            + f"{review_link}\n\n"
            + note,
        )
        return {"status": "complete", "summary": "Review blocked staging."}
    production_sha = reviewed.get("production_sha") or ""
    candidate_sha = reviewed.get("candidate_sha") or ""
    _set_rehearsal(
        thread_id,
        {
            "candidate_sha": candidate_sha,
            "production_sha": production_sha,
            "staging_ready": False,
            "updated_ok": False,
            "updated_sha": "",
        },
    )
    try:
        run = dispatch_gpw_workflow(
            "prepare_staging",
            {"production_sha": production_sha, "candidate_sha": candidate_sha},
        )
    except Exception as exc:
        logger.exception("GPW prepare-staging dispatch failed")
        _complete_progress(thread_id)
        _post(thread_id, f"Could not start prepare-staging: {exc}")
        return {"status": "complete", "summary": str(exc)}
    _post(
        thread_id,
        "Review is clean. Building a full staging environment from production "
        f"`{production_sha[:7]}`, including a copy of the production database.",
    )
    return _begin_poll(
        thread_id,
        phase="prepare_staging",
        run=run,
        production_sha=production_sha,
        candidate_sha=candidate_sha,
    )


def _start_update_staging(thread_id: Optional[str]) -> Dict[str, Any]:
    rehearsal = _rehearsal(thread_id)
    if not rehearsal.get("staging_ready"):
        _post(
            thread_id,
            "Staging is not ready. Run **prepare staging** first so the environment "
            "matches production, including the database copy.",
        )
        return {"status": "complete", "summary": "Staging not prepared."}
    candidate_sha = rehearsal.get("candidate_sha") or ""
    try:
        client = _github()
        owner, name = _owner_name()
        current = client.get_ref_sha(owner, name, CANDIDATE_BRANCH)
    except Exception as exc:
        _post(thread_id, f"Could not read `{CANDIDATE_BRANCH}`: {exc}")
        return {"status": "complete", "summary": str(exc)}
    if current != candidate_sha:
        _post(
            thread_id,
            f"`{CANDIDATE_BRANCH}` moved after the review (`{candidate_sha[:7]}` → `{current[:7]}`). "
            "Run **prepare staging** again.",
        )
        return {"status": "complete", "summary": "develop moved"}
    try:
        run = dispatch_gpw_workflow("update_staging", {"image_sha": candidate_sha})
    except Exception as exc:
        logger.exception("GPW update-staging dispatch failed")
        _post(thread_id, f"Could not start update-staging: {exc}")
        return {"status": "complete", "summary": str(exc)}
    _post(
        thread_id,
        f"Updating staging to `{candidate_sha[:7]}`, including migrations. "
        "Maintenance is on until the post-deploy check is green.",
        status="in_progress",
    )
    return _begin_poll(
        thread_id,
        phase="update_staging",
        run=run,
        production_sha=rehearsal.get("production_sha") or "",
        candidate_sha=candidate_sha,
    )


def _ask(thread_id: Optional[str], action: str, *, candidate_sha: str = "") -> Dict[str, Any]:
    _patch(
        thread_id,
        pending_deploy={"kind": "gpw", "action": action, "candidate_sha": candidate_sha},
    )
    return {"status": "complete", "summary": f"Waiting to confirm {action}."}


def _start_teardown(thread_id: Optional[str]) -> Dict[str, Any]:
    _post(
        thread_id,
        "This destroys the staging stack in `gpw-staging-470307` "
        f"({STAGING_URL}): Cloud Run, Cloud SQL, scheduler and the domain mapping. "
        "The production store is not touched. The state bucket and manual secrets stay, "
        "so the next **prepare staging** can build it again.\n\n"
        "Reply **yes** to tear staging down.",
    )
    return _ask(thread_id, "teardown")


def _start_prepare_deploy(thread_id: Optional[str]) -> Dict[str, Any]:
    rehearsal = _rehearsal(thread_id)
    if not rehearsal.get("updated_ok"):
        _post(
            thread_id,
            "There is no green staging rehearsal. Run **prepare staging**, then "
            "**update staging**, and log in on staging before preparing a production deploy.",
        )
        return {"status": "complete", "summary": "No rehearsal."}
    candidate_sha = rehearsal.get("updated_sha") or ""
    try:
        client = _github()
        owner, name = _owner_name()
        current = client.get_ref_sha(owner, name, CANDIDATE_BRANCH)
    except Exception as exc:
        _post(thread_id, f"Could not read `{CANDIDATE_BRANCH}`: {exc}")
        return {"status": "complete", "summary": str(exc)}
    if current != candidate_sha:
        _post(
            thread_id,
            f"`{CANDIDATE_BRANCH}` moved after the rehearsal (`{candidate_sha[:7]}` → `{current[:7]}`). "
            "Run **update staging** again before production.",
        )
        return {"status": "complete", "summary": "develop moved"}
    try:
        run = dispatch_gpw_workflow("backup", {"candidate_sha": candidate_sha})
    except Exception as exc:
        logger.exception("GPW production backup dispatch failed")
        _post(thread_id, f"Could not start the production backup: {exc}")
        return {"status": "complete", "summary": str(exc)}
    _post(
        thread_id,
        f"Rehearsal for `{candidate_sha[:7]}` is green. Taking a production database backup "
        "before I ask you to deploy.",
        status="in_progress",
    )
    return _begin_poll(
        thread_id,
        phase="backup",
        run=run,
        production_sha=rehearsal.get("production_sha") or "",
        candidate_sha=candidate_sha,
    )


def _confirm(thread_id: Optional[str], pending: Dict[str, Any]) -> Dict[str, Any]:
    action = pending.get("action") or ""
    _patch(thread_id, pending_deploy=None)
    if action == "teardown":
        try:
            run = dispatch_gpw_workflow("teardown", {"confirm": "yes"})
        except Exception as exc:
            _post(thread_id, f"Could not start teardown: {exc}")
            return {"status": "complete", "summary": str(exc)}
        _post(thread_id, "Tearing down staging.", status="in_progress")
        return _begin_poll(thread_id, phase="teardown", run=run)
    if action == "deploy":
        candidate_sha = pending.get("candidate_sha") or _rehearsal(thread_id).get("updated_sha") or ""
        try:
            run = dispatch_gpw_workflow(
                "deploy_production",
                {"image_sha": candidate_sha},
            )
        except Exception as exc:
            _post(thread_id, f"Could not start the production deploy: {exc}")
            return {"status": "complete", "summary": str(exc)}
        _post(
            thread_id,
            "Maintenance is on for the store. Deploying, migrating, then running the "
            "post-deploy check. Maintenance comes off only if that check is green.",
            status="in_progress",
        )
        return _begin_poll(
            thread_id,
            phase="deploy_production",
            run=run,
            candidate_sha=candidate_sha,
            production_sha=_rehearsal(thread_id).get("production_sha") or "",
        )
    _post(thread_id, "Nothing is waiting for a confirmation.")
    return {"status": "complete", "summary": "No pending GPW action."}


def run_gpw_pipeline(*, thread_id: Optional[str], user_message: str) -> Dict[str, Any]:
    from bigas.resources.devops.pipeline import is_cancel, is_confirm

    pending = pending_gpw_action(thread_id)
    if pending and is_cancel(user_message):
        _patch(thread_id, pending_deploy=None)
        _complete_progress(thread_id)
        _post(thread_id, "Cancelled. Nothing was changed.")
        return {"status": "complete", "summary": "Cancelled."}
    if pending and is_confirm(user_message):
        return _confirm(thread_id, pending)

    command = parse_gpw_command(user_message)
    if command == "prepare_staging":
        return _start_prepare_staging(thread_id)
    if command == "update_staging":
        return _start_update_staging(thread_id)
    if command == "teardown":
        return _start_teardown(thread_id)
    if command == "prepare_deploy":
        return _start_prepare_deploy(thread_id)
    _post(thread_id, "Say **prepare staging**, **update staging**, **teardown staging**, or **prepare deploy GPW-PROD**.")
    return {"status": "complete", "summary": "Unknown GPW command."}


def _resolve_missing_gpw_run_id(poll: Dict[str, Any]) -> Optional[int]:
    """Find a workflow run id when dispatch returned before GitHub registered the run."""
    triggered = poll.get("triggered") or []
    item = triggered[0] if triggered else {}
    phase = (poll.get("phase") or "").strip()
    workflow = (item.get("workflow") or _WORKFLOWS.get(phase) or "").strip()
    if not workflow:
        return None
    started = _parse_started(poll.get("started_at") or "")
    slack = timedelta(seconds=30)
    try:
        client = _github()
        owner, name = _owner_name()
        branch = client.get_default_branch(owner, name)
        runs = client.list_workflow_runs(owner, name, workflow, branch=branch, limit=10)
    except Exception as exc:
        logger.warning("GPW could not list workflow runs for %s: %s", workflow, exc)
        return None
    for run in runs:
        created = _parse_started(run.get("created_at") or "")
        if created + slack < started:
            continue
        run_id = run.get("id")
        if run_id:
            return int(run_id)
    return None


def _parse_started(value: str) -> datetime:
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _finish_success(thread_id: str, poll: Dict[str, Any]) -> None:
    phase = poll.get("phase") or ""
    candidate = (poll.get("candidate_sha") or "").strip()
    production = (poll.get("production_sha") or "").strip()
    rehearsal = _rehearsal(thread_id)
    if phase == "prepare_staging":
        rehearsal.update(
            {
                "staging_ready": True,
                "production_sha": production or rehearsal.get("production_sha") or "",
                "candidate_sha": candidate or rehearsal.get("candidate_sha") or "",
                "updated_ok": False,
                "updated_sha": "",
            }
        )
        _set_rehearsal(thread_id, rehearsal)
        _post(
            thread_id,
            "Staging is up as a copy of production, including the database. "
            f"New code is not applied yet.\n\n{STAGING_URL}",
        )
        return
    if phase == "update_staging":
        rehearsal.update(
            {
                "staging_ready": True,
                "updated_ok": True,
                "updated_sha": candidate or rehearsal.get("candidate_sha") or "",
                "candidate_sha": candidate or rehearsal.get("candidate_sha") or "",
            }
        )
        _set_rehearsal(thread_id, rehearsal)
        _post(
            thread_id,
            "Staging updated and the post-deploy check passed. Maintenance is off.\n\n"
            f"Log in at {STAGING_URL}. The staging admin password is `ADMIN_PASSWORD` "
            "in Secret Manager for `gpw-staging-470307` (it is not posted here). "
            "Accounts from the production database copy can also sign in.",
        )
        return
    if phase == "teardown":
        rehearsal.update({"staging_ready": False, "updated_ok": False, "updated_sha": ""})
        _set_rehearsal(thread_id, rehearsal)
        _post(thread_id, "Staging is torn down. Production was not changed.")
        return
    if phase == "backup":
        _post(
            thread_id,
            "Production database backup is done.\n\n"
            f"Deploy `{candidate[:7]}` to {PROD_URL}? "
            "Maintenance goes on at the start and comes off only after a green post-deploy check.\n\n"
            "Reply **yes** to deploy, or **no** to stop.",
        )
        _patch(
            thread_id,
            pending_deploy={
                "kind": "gpw",
                "action": "deploy",
                "candidate_sha": candidate,
            },
        )
        return
    if phase == "deploy_production":
        note = ""
        try:
            client = _github()
            owner, name = _owner_name()
            if candidate:
                client.update_branch_ref(owner, name, PROD_BRANCH, candidate, force=False)
                note = f"\n\nFast-forwarded `{PROD_BRANCH}` to `{candidate[:7]}`."
        except Exception as exc:
            logger.exception("GPW production branch fast-forward failed")
            note = (
                f"\n\nThe store is updated, but `{PROD_BRANCH}` was not fast-forwarded: {exc}"
            )
        rehearsal.update({"staging_ready": False, "updated_ok": False, "updated_sha": ""})
        _set_rehearsal(thread_id, rehearsal)
        _post(
            thread_id,
            "Production deploy and post-deploy check passed. Maintenance is off.\n\n"
            f"{PROD_URL}{note}",
        )
        return
    _post(thread_id, "GPW workflow finished.")


def _finish_failure(thread_id: str, poll: Dict[str, Any], failed_runs: list) -> None:
    phase = poll.get("phase") or ""
    lines = ["The GPW workflow failed."]
    for item in failed_runs:
        url = item.get("html_url") or ""
        lines.append(
            f"- {item.get('workflow') or phase} #{item.get('run_id')} "
            f"{item.get('conclusion') or 'failure'}"
            + (f" — {url}" if url else "")
        )
    if phase in {"deploy_production", "update_staging"}:
        lines.append(
            "Maintenance stays on because the post-deploy check did not pass."
            if phase == "deploy_production"
            else "Staging maintenance stays on because the post-deploy check did not pass."
        )
    _post(thread_id, "\n".join(lines))
    if phase not in _AUTOFIX_PHASES:
        return
    try:
        from bigas.resources.cto.deploy_hotfix import launch_failed_deploy_fix
        from bigas.resources.devops.service import get_failed_run_excerpt

        enriched = []
        for item in failed_runs:
            row = dict(item)
            run_id = item.get("run_id")
            if run_id and not row.get("excerpt"):
                try:
                    fetched = get_failed_run_excerpt(repo=REPO, run_id=int(run_id))
                    row["excerpt"] = (fetched.get("excerpt") or "")[:2500]
                except Exception as exc:
                    row["excerpt"] = f"(could not fetch logs: {exc})"
            enriched.append(row)
        launched = launch_failed_deploy_fix(
            repo=REPO,
            failures=enriched or failed_runs,
            starting_ref=CANDIDATE_BRANCH,
            extra_instructions=(
                "This is the GPW store (Django on Cloud Run). "
                "Do not deploy, do not run terraform against production, and do not disable "
                "maintenance mode. Open a pull request against develop."
            ),
        )
    except Exception as exc:
        logger.exception("GPW autofix launch failed")
        _post(thread_id, f"Could not start autofix ({exc}).")
        return
    url = (launched.get("agent_url") or "").strip()
    _post(
        thread_id,
        "Autofix is looking at the failure and will open a PR on `develop`"
        + (f": {url}" if url else "."),
    )


def poll_gpw(thread_id: str) -> Dict[str, Any]:
    """One client-driven poll step for a GPW workflow run."""
    from bigas.resources.devops.service import get_deployment_status

    thread = _thread(thread_id)
    poll = thread.get("pending_deploy_poll")
    if not isinstance(poll, dict) or poll.get("kind") != "gpw":
        return {"status": "complete", "active": False}
    triggered = poll.get("triggered") or [{}]
    if not triggered[0].get("run_id"):
        resolved = _resolve_missing_gpw_run_id(poll)
        if resolved:
            item = dict(triggered[0])
            item["run_id"] = resolved
            if not item.get("workflow"):
                phase = (poll.get("phase") or "").strip()
                item["workflow"] = _WORKFLOWS.get(phase) or phase
            triggered = [item]
            poll = {**poll, "triggered": triggered}
            _patch(thread_id, pending_deploy_poll=poll)
        else:
            started = _parse_started(poll.get("started_at") or "")
            if datetime.now(timezone.utc) < started + timedelta(seconds=_POLL_TIMEOUT_SEC):
                return {"status": "in_progress", "active": True}
            _complete_progress(thread_id)
            _patch(thread_id, pending_deploy_poll=None, has_pending_deploy_poll=False)
            _post(thread_id, "No GitHub Actions run id came back. Check the Actions tab.")
            return {"status": "complete", "active": False}

    item = triggered[0]
    run_id = int(item["run_id"])
    started = _parse_started(poll.get("started_at") or "")
    if datetime.now(timezone.utc) >= started + timedelta(seconds=_POLL_TIMEOUT_SEC):
        _complete_progress(thread_id)
        _patch(thread_id, pending_deploy_poll=None, has_pending_deploy_poll=False)
        _post(thread_id, f"Timed out waiting for run #{run_id}. Check the Actions tab.")
        return {"status": "complete", "active": False}
    try:
        status = get_deployment_status(repo=REPO, run_id=run_id)
    except Exception as exc:
        logger.warning("GPW poll failed for run %s: %s", run_id, exc)
        return {"status": "in_progress", "active": True}
    if (status.get("workflow_status") or "").lower() != "completed":
        return {"status": "in_progress", "active": True}

    conclusion = (status.get("conclusion") or "unknown").lower()
    _complete_progress(thread_id)
    _patch(thread_id, pending_deploy_poll=None, has_pending_deploy_poll=False)
    if conclusion == "success":
        _finish_success(thread_id, poll)
    else:
        failed = [
            {
                "workflow": item.get("workflow") or poll.get("phase"),
                "run_id": run_id,
                "conclusion": conclusion,
                "html_url": status.get("html_url") or item.get("html_url") or "",
            }
        ]
        _finish_failure(thread_id, poll, failed)
    return {"status": "complete", "active": False}
