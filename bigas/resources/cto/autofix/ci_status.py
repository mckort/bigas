"""PR check runs that autofix should fix, wait for, or ignore."""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_JOB_ID = re.compile(r"/job/(\d+)")
_INTERESTING = re.compile(
    r"error|fail|unused|✗|##\[error\]|ELIFECYCLE|npm error",
    re.IGNORECASE,
)
_INFRA = (
    "spending limit",
    "recent account payments have failed",
    "the job was not started because",
)
_PENDING_STATUSES = {"queued", "in_progress", "pending", "waiting", "requested"}
_USELESS_ANNOTATION = re.compile(
    r"process completed with exit code|node\.js 20 is deprecated|ubuntu-latest label will migrate",
    re.IGNORECASE,
)


def empty_ci(*, unavailable: bool = False) -> dict[str, Any]:
    return {"failed": [], "pending": [], "unavailable": unavailable}


def is_bigas_review_check(name: str) -> bool:
    lowered = (name or "").strip().lower()
    return lowered.startswith("bigas /") or lowered.startswith("bigas/")


def is_infrastructure_failure(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _INFRA)


def job_id_from_check(run: dict[str, Any]) -> int | None:
    for key in ("details_url", "html_url"):
        match = _JOB_ID.search(str(run.get(key) or ""))
        if match:
            return int(match.group(1))
    return None


def annotation_excerpt(annotations: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for ann in annotations[:20]:
        message = str(ann.get("message") or "").strip()
        if not message or _USELESS_ANNOTATION.search(message):
            continue
        path = str(ann.get("path") or "").strip()
        line_no = ann.get("start_line") or ""
        loc = f"{path}:{line_no} " if path else ""
        lines.append(f"{loc}{message}")
    return "\n".join(lines)


def log_excerpt(text: str, *, limit: int = 2500) -> str:
    clean = _ANSI.sub("", text or "")
    lines = [line for line in clean.splitlines() if line.strip()]
    # Keep the line after an error too, so a knip/eslint file path is not dropped.
    chosen: list[str] = []
    for index, line in enumerate(lines):
        if not _INTERESTING.search(line):
            continue
        chosen.append(line)
        if index + 1 < len(lines):
            chosen.append(lines[index + 1])
    chosen = chosen[-40:] or lines[-40:]
    excerpt = "\n".join(chosen).strip()
    if len(excerpt) > limit:
        return excerpt[-limit:]
    return excerpt


def ready_after_ci(
    review_ready: bool,
    ci: dict[str, Any],
    *,
    block_on_pending: bool,
) -> tuple[bool, bool, bool]:
    """
    Return (ready_to_merge, checks_pending, ci_failed).

    Failed code checks are never ready. Pending checks block only when
    block_on_pending is set (after an autofix push). Unknown check status
    does not block.
    """
    if ci.get("unavailable"):
        return review_ready, False, False
    failed = bool(ci.get("failed"))
    pending = bool(ci.get("pending"))
    if failed:
        return False, False, True
    if block_on_pending and pending and review_ready:
        return False, True, False
    return review_ready, False, False


def format_ci_prompt_block(failures: list[dict[str, Any]]) -> str:
    if not failures:
        return ""
    parts = [
        "## Failing CI",
        "These checks failed on the current pull request head. Fix them in this same push.",
        "",
    ]
    for failure in failures[:4]:
        name = failure.get("name") or "check"
        url = failure.get("url") or ""
        excerpt = (failure.get("excerpt") or "").strip() or "(no log excerpt)"
        parts.append(f"### {name}")
        if url:
            parts.append(url)
        parts.append(excerpt)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n\n"


def fetch_head_ci(client: Any, owner: str, repo: str, sha: str) -> dict[str, Any]:
    """Load failed and pending checks. Bigas review jobs and billing failures are ignored."""
    if not sha or not hasattr(client, "list_commit_check_runs"):
        return empty_ci()
    runs = client.list_commit_check_runs(owner, repo, sha)
    if not isinstance(runs, list):
        return empty_ci()

    failed: list[dict[str, Any]] = []
    pending: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        name = str(run.get("name") or "check")
        if is_bigas_review_check(name):
            continue
        status = str(run.get("status") or "").strip().lower()
        conclusion = str(run.get("conclusion") or "").strip().lower()
        if status != "completed":
            if status in _PENDING_STATUSES:
                pending.append(name)
            continue
        if conclusion != "failure":
            continue
        excerpt = ""
        check_id = run.get("id")
        if check_id is not None and hasattr(client, "list_check_run_annotations"):
            try:
                annotations = client.list_check_run_annotations(owner, repo, int(check_id))
            except Exception:
                logger.warning("Could not read annotations for check %s", check_id, exc_info=True)
                annotations = []
            if isinstance(annotations, list):
                excerpt = annotation_excerpt(annotations)
        blob = f"{name}\n{run.get('output') or ''}\n{excerpt}"
        if is_infrastructure_failure(blob):
            continue
        if not excerpt:
            job_id = job_id_from_check(run)
            if job_id is not None and hasattr(client, "get_actions_job_log"):
                try:
                    excerpt = log_excerpt(client.get_actions_job_log(owner, repo, job_id))
                except Exception:
                    logger.warning("Could not read job log %s", job_id, exc_info=True)
                    excerpt = ""
            if is_infrastructure_failure(excerpt):
                continue
        failed.append(
            {
                "name": name,
                "url": run.get("html_url") or run.get("details_url") or "",
                "excerpt": excerpt,
            }
        )
    return {"failed": failed, "pending": pending, "unavailable": False}
