"""Short action-oriented `summary` strings for CTO MCP tool responses."""
from __future__ import annotations

from typing import Any, Callable

SUMMARY_REPLY_INSTRUCTION = (
    "Returns a `summary` for the chat reply — use that text, do not dump the JSON."
)


def with_summary(payload: dict, summarizer: Callable[[dict], str]) -> dict:
    out = dict(payload)
    out["summary"] = summarizer(out)
    return out


def _first_link(*candidates: Any) -> str:
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _auto_merge_clause(auto_merge: Any) -> str:
    if not isinstance(auto_merge, dict):
        return ""
    if auto_merge.get("merged"):
        return " The PR was squash-merged."
    if auto_merge.get("auto_merge_enabled"):
        return " GitHub auto-merge is enabled (waiting on checks)."
    return ""


def _cooldown_wait(payload: dict) -> str:
    try:
        cooldown_s = int(payload.get("cooldown_seconds") or 0)
        age_s = int(payload.get("head_age_seconds") or 0)
    except (TypeError, ValueError):
        return "a few minutes"
    if cooldown_s <= 0:
        return "a few minutes"
    wait_left = max(0, cooldown_s - age_s)
    if wait_left <= 0:
        return "a moment"
    mins = max(1, (wait_left + 59) // 60)
    return f"~{mins} min"


def summarize_review_result(payload: dict) -> str:
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return f"Could not review the PR: {error.strip().rstrip('.')}."

    pr_url = _first_link(payload.get("pr_url"))
    comment_url = _first_link(payload.get("comment_url"))
    reason = str(payload.get("reason") or "").strip()

    if payload.get("skipped") and reason == "pr_already_merged":
        suffix = f" {pr_url}" if pr_url else ""
        return f"PR is already merged. Nothing to review.{suffix}".strip()

    merge_bit = _auto_merge_clause(payload.get("auto_merge"))
    board = payload.get("board_ticket")
    ticket_bit = ""
    if isinstance(board, dict) and board.get("created") and board.get("issue_key"):
        ticket_bit = f" Created {board.get('issue_key')} on the board."
    if payload.get("ready_to_merge"):
        if comment_url:
            return (
                f"Review looks clean — ready to merge. Comment: {comment_url}."
                f"{merge_bit}{ticket_bit}"
            ).rstrip()
        return f"Review looks clean — ready to merge.{merge_bit}{ticket_bit}".rstrip()

    if payload.get("review_posted") and comment_url:
        return (
            f"Review posted with findings. Comment: {comment_url}. "
            f"Want me to run autofix?{ticket_bit}"
        )
    if comment_url:
        return f"Review finished. Comment: {comment_url}.{ticket_bit}".rstrip()
    return f"Review finished, but no GitHub comment URL was returned.{ticket_bit}".rstrip()


def summarize_pr_merged_result(payload: dict) -> str:
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return f"Could not move the ticket after merge: {error.strip().rstrip('.')}."

    result = payload.get("jira_final_approval")
    if not isinstance(result, dict):
        result = {}
    reason = str(result.get("reason") or "").strip()
    issue_key = (result.get("issue_key") or "").strip()
    moved_to = (result.get("moved_to") or "").strip()
    pr_url = _first_link(payload.get("pr_url"), result.get("pr_url"))
    suffix = f" {pr_url}" if pr_url else ""

    if result.get("ok") and not result.get("skipped") and issue_key:
        dest = moved_to or "Final approval (manual)"
        if result.get("created") or reason == "created_in_final_approval":
            return f"Created {issue_key} from the PR and put it in {dest}.{suffix}".strip()
        return f"Moved {issue_key} to {dest}.{suffix}".strip()
    if reason == "already_in_final_approval" and issue_key:
        return f"{issue_key} is already in Final approval.{suffix}".strip()
    if reason == "pr_not_merged":
        return f"PR is not merged yet; ticket stays put.{suffix}".strip()
    if reason == "no Jira issue key found on PR":
        return f"PR is merged, but no ticket key was found on it.{suffix}".strip()
    if result.get("skipped") and reason:
        return f"Did not move a ticket after merge ({reason}).{suffix}".strip()
    return f"Handled merged PR.{suffix}".strip()


def summarize_autofix_result(payload: dict) -> str:
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return (
            f"Autofix failed: {error.strip().rstrip('.')}. "
            "Want me to trigger autofix again?"
        )

    pr_url = _first_link(payload.get("pr_url"))
    pr_bit = f" PR: {pr_url}." if pr_url else ""
    agent_url = _first_link(payload.get("agent_url"), payload.get("agent_id"))
    reason = str(payload.get("reason") or "").strip()

    if payload.get("launched"):
        round_n = payload.get("autofix_round") or "?"
        max_n = payload.get("max_iterations") or "?"
        if agent_url:
            return (
                f"Autofix is running (round {round_n}/{max_n}). "
                f"Follow the agent: {agent_url}"
            )
        return f"Autofix is running (round {round_n}/{max_n}).{pr_bit}".strip()

    if payload.get("loop_protection"):
        count = payload.get("autofix_count") or "?"
        max_n = payload.get("max_iterations") or "?"
        return (
            f"Autofix stopped after {count}/{max_n} rounds. "
            f"Needs a manual look.{pr_bit}"
        ).strip()

    if payload.get("review_clean"):
        return f"Review is clean — no autofix needed.{pr_bit}".strip()

    if payload.get("cooldown"):
        wait = _cooldown_wait(payload)
        return (
            f"Autofix is paused (cooldown, {wait}). "
            f"The Actions loop will retry.{pr_bit}"
        ).strip()

    if payload.get("stale_review"):
        return (
            "Waiting for re-review of the latest autofix commit "
            f"before launching another agent.{pr_bit}"
        ).strip()

    if payload.get("skipped") and reason == "pr_already_merged":
        return f"PR is already merged. No autofix launched.{pr_bit}".strip()

    if payload.get("skipped"):
        why = reason or "skipped"
        return f"Autofix skipped: {why}.{pr_bit}".strip()

    return f"Autofix finished.{pr_bit}".strip()


def summarize_followup_result(payload: dict) -> str:
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return (
            f"Autofix follow-up failed: {error.strip().rstrip('.')}. "
            "Want me to trigger autofix again?"
        )

    pr_url = _first_link(payload.get("pr_url"))
    pr_bit = f" PR: {pr_url}." if pr_url else ""
    agent_url = _first_link(payload.get("agent_url"), payload.get("agent_id"))
    comment_url = _first_link(payload.get("comment_url"))
    status = str(payload.get("status") or "UNKNOWN").strip() or "UNKNOWN"
    reason = str(payload.get("reason") or "").strip()

    if payload.get("skipped") and reason == "pr_already_merged":
        return f"PR is already merged.{pr_bit}".strip()

    if not payload.get("done"):
        if agent_url:
            return (
                f"Autofix is still running ({status}). "
                f"Follow the agent: {agent_url}"
            )
        return f"Autofix is still running ({status}).{pr_bit}".strip()

    if not payload.get("finalized"):
        if agent_url:
            return (
                f"Autofix agent finished ({status}). "
                f"Follow the agent: {agent_url} "
                "Say if you want me to finalize (Discord + re-review)."
            )
        return (
            f"Autofix agent finished ({status}). "
            "Say if you want me to finalize (Discord + re-review)."
        )

    if not payload.get("ok"):
        if agent_url:
            return (
                f"Autofix failed ({status}). Agent: {agent_url} "
                "Want me to trigger autofix again?"
            )
        return f"Autofix failed ({status}). Want me to trigger autofix again?"

    if payload.get("asked_confirmation"):
        return (
            "The agent stopped to ask for confirmation instead of pushing. "
            f"Want me to trigger autofix again?{pr_bit}"
        ).strip()

    if payload.get("fixes_pushed") is False and not payload.get("rereviewed"):
        return (
            "Autofix finished without pushing commits. "
            f"Want me to trigger autofix again?{pr_bit}"
        ).strip()

    merge_bit = _auto_merge_clause(payload.get("auto_merge"))
    pushed = payload.get("fixes_pushed") is not False
    if payload.get("ready_to_merge"):
        if pushed:
            lead = "Autofix pushed fixes and the re-review looks clean."
        else:
            lead = "Autofix did not need new commits; the re-review looks clean."
        if comment_url:
            return f"{lead} Ready to merge. Comment: {comment_url}.{merge_bit}".rstrip()
        return f"{lead} Ready to merge.{merge_bit}{pr_bit}".strip()

    if payload.get("loop_protection"):
        count = payload.get("autofix_count") or "?"
        max_n = payload.get("max_iterations") or "?"
        return (
            f"Still has findings after {count}/{max_n} autofix rounds. "
            f"Needs a manual look.{pr_bit}"
        ).strip()

    if not pushed:
        if comment_url:
            return (
                "Autofix did not push; re-review still has findings. "
                f"Stopping the loop. Comment: {comment_url}."
            )
        return (
            "Autofix did not push; re-review still has findings. "
            f"Stopping the loop.{pr_bit}"
        ).strip()

    if comment_url:
        return (
            "Autofix pushed fixes, but the re-review still has findings. "
            f"Another round may run. Comment: {comment_url}."
        )
    return (
        "Autofix pushed fixes, but the re-review still has findings. "
        f"Another round may run.{pr_bit}"
    ).strip()


def summarize_deploy_hotfix_result(payload: dict) -> str:
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return (
            f"Could not launch the CTO fix agent: {error.strip().rstrip('.')}."
        )

    agent_url = _first_link(payload.get("agent_url"), payload.get("agent_id"))
    if payload.get("launched") and agent_url:
        return (
            "CTO agent launched to fix the failed deploy and open a PR. "
            f"Follow the agent: {agent_url}"
        )
    if payload.get("launched"):
        return "CTO agent launched to fix the failed deploy and open a PR."
    return "Could not launch the CTO fix agent."
