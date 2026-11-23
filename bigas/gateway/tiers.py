"""What each MCP tool can do, in plain words: look, propose, act, or never.

- look:    reads data. Safe to run without asking.
- propose: creates something reviewable and reversible (a ticket, a PR, a draft).
- act:     changes the world (deploy, release, send, publish). Needs the owner's approval.
- never:   not for outside agents (cleanup, OAuth code exchange, webhooks).

A tool that is not listed here and matches no read-only prefix is treated as
``act``, so a new tool is held for approval until someone classifies it.
"""
from __future__ import annotations

from typing import Dict

LOOK = "look"
PROPOSE = "propose"
ACT = "act"
NEVER = "never"

TIER_LABELS = {
    LOOK: "Look",
    PROPOSE: "Propose",
    ACT: "Act",
    NEVER: "Never",
}

TOOL_TIERS: Dict[str, str] = {
    # Look: reports, analytics, lookups.
    "analyze_trends": LOOK,
    "analyze_underperforming_pages": LOOK,
    "ask_analytics_question": LOOK,
    "check_deployment_risk": LOOK,
    "check_website_health": LOOK,
    "fetch_ai_usage": LOOK,
    "fetch_google_ads_recommendations": LOOK,
    "get_deployment_status": LOOK,
    "get_job_result": LOOK,
    "get_job_status": LOOK,
    "get_latest_report": LOOK,
    "get_stored_reports": LOOK,
    "linkedin_ads_health_check": LOOK,
    "lookup_board_releases": LOOK,
    "lookup_ticket": LOOK,
    "reddit_ads_health_check": LOOK,
    "run_cross_platform_marketing_analysis": LOOK,
    "run_cross_platform_marketing_analysis_async": LOOK,
    "run_google_ads_portfolio_report": LOOK,
    "run_google_ads_portfolio_report_async": LOOK,
    "run_linkedin_portfolio_report": LOOK,
    "run_linkedin_portfolio_report_async": LOOK,
    "run_meta_portfolio_report": LOOK,
    "run_meta_portfolio_report_async": LOOK,
    "run_reddit_portfolio_report": LOOK,
    "run_reddit_portfolio_report_async": LOOK,
    "search_tickets": LOOK,
    "summarize_reddit_ad_analytics": LOOK,
    "website_monitor": LOOK,
    "weekly_analytics_report": LOOK,
    "weekly_analytics_report_async": LOOK,
    "weekly_cto_ai_report": LOOK,
    # Propose: reviewable, reversible work.
    "autofix_followup": PROPOSE,
    "autofix_pr": PROPOSE,
    "create_github_pr": PROPOSE,
    "create_ticket": PROPOSE,
    "draft_marketing_email": PROPOSE,
    "fix_failed_deployment": PROPOSE,
    "generate_weekly_x_post": PROPOSE,  # drafts; publishing needs the owner's signed link
    "notify_pr_merged": PROPOSE,
    "preview_marketing_email": PROPOSE,
    "progress_updates": PROPOSE,
    "review_and_comment_pr": PROPOSE,
    "run_qa": PROPOSE,
    "update_ticket": PROPOSE,
    "weekly_okr_pulse": PROPOSE,
    # Act: real changes, approval first.
    "cherry_pick_hotfix": ACT,
    "create_release_notes": ACT,  # can create a GitHub release and mark a version released
    "list_board_recipients": ACT,  # personal data (email addresses)
    "trigger_deployment": ACT,
    # Never: maintenance, OAuth plumbing, webhooks.
    "cleanup_old_activity": NEVER,
    "cleanup_old_reports": NEVER,
    "github_workflow_run": NEVER,
    "linkedin_exchange_code": NEVER,
    "product_resource_placeholder": NEVER,
    "reddit_exchange_code": NEVER,
}

_LOOK_PREFIXES = ("fetch_", "get_", "lookup_", "search_", "list_", "analyze_", "summarize_")


def tool_tier(name: str) -> str:
    """Tier for a tool name. Unknown tools default to ``act`` (fail safe)."""
    name = (name or "").strip()
    if name in TOOL_TIERS:
        return TOOL_TIERS[name]
    if name.startswith(_LOOK_PREFIXES) and not name.endswith(("_write", "_delete")):
        return LOOK
    return ACT
