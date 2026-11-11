"""Optional product areas, switched with ``BIGAS_FEATURES``.

The core loop is always on: cockpit, board, chat with Product, CTO, CFO and
DevOps, deploys, and the weekly update. A few areas are optional:

``marketing``
    Marketing Analyst agent, GA4 and paid-ads reports (Google, Meta,
    LinkedIn, Reddit).
``objectives``
    The ``/objectives`` OKR page and the Monday OKR pulse.
``outreach``
    Board email outreach. Also needs ``ENABLE_OUTBOUND_EMAIL=true``.

``BIGAS_FEATURES`` unset (or ``all``) keeps every area on, so existing
deployments are unchanged. Set it to a comma-separated list to keep only
those optional areas, e.g. ``BIGAS_FEATURES=objectives`` or
``BIGAS_FEATURES=none`` for the core loop only.

Hiding an area removes it from the UI, the agent list and the tools chat
agents can see. HTTP endpoints stay registered, so Cloud Scheduler jobs and
MCP clients you already run keep working.
"""
from __future__ import annotations

import os
from typing import Dict, FrozenSet, Iterable, List

OPTIONAL_FEATURES: FrozenSet[str] = frozenset({"marketing", "objectives", "outreach"})

# Chat tools that belong to an optional area. Hidden from chat agents when the
# area is off. Names match the MCP manifest.
FEATURE_TOOLS: Dict[str, FrozenSet[str]] = {
    "marketing": frozenset(
        {
            "analyze_trends",
            "analyze_underperforming_pages",
            "ask_analytics_question",
            "cleanup_old_reports",
            "fetch_analytics_report",
            "fetch_custom_report",
            "fetch_linkedin_ad_analytics_report",
            "fetch_reddit_ad_analytics_report",
            "fetch_reddit_audience_report",
            "get_latest_report",
            "get_stored_reports",
            "linkedin_ads_health_check",
            "linkedin_exchange_code",
            "reddit_ads_health_check",
            "reddit_exchange_code",
            "run_cross_platform_marketing_analysis",
            "run_cross_platform_marketing_analysis_async",
            "run_google_ads_portfolio_report",
            "run_google_ads_portfolio_report_async",
            "run_linkedin_portfolio_report",
            "run_linkedin_portfolio_report_async",
            "run_meta_portfolio_report",
            "run_meta_portfolio_report_async",
            "run_reddit_portfolio_report",
            "run_reddit_portfolio_report_async",
            "summarize_reddit_ad_analytics",
            "weekly_analytics_report",
            "weekly_analytics_report_async",
        }
    ),
    "objectives": frozenset({"weekly_okr_pulse"}),
    "outreach": frozenset(
        {"draft_marketing_email", "preview_marketing_email", "list_board_recipients"}
    ),
}

# Chat agents that belong to an optional area.
FEATURE_AGENTS: Dict[str, str] = {"marketing": "marketing"}


def enabled_features() -> FrozenSet[str]:
    raw = (os.environ.get("BIGAS_FEATURES") or "").strip().lower()
    if not raw or raw == "all":
        return OPTIONAL_FEATURES
    if raw == "none":
        return frozenset()
    picked = {part.strip() for part in raw.split(",") if part.strip()}
    return frozenset(OPTIONAL_FEATURES & picked)


def feature_enabled(name: str) -> bool:
    key = (name or "").strip().lower()
    if key not in OPTIONAL_FEATURES:
        return True  # core areas cannot be switched off
    return key in enabled_features()


def features_payload() -> Dict[str, bool]:
    """``{feature: enabled}`` for the frontend."""
    on = enabled_features()
    return {name: name in on for name in sorted(OPTIONAL_FEATURES)}


def hidden_tool_names() -> FrozenSet[str]:
    hidden: set = set()
    for feature, names in FEATURE_TOOLS.items():
        if not feature_enabled(feature):
            hidden.update(names)
    return frozenset(hidden)


def hidden_agent_ids() -> FrozenSet[str]:
    return frozenset(
        agent for feature, agent in FEATURE_AGENTS.items() if not feature_enabled(feature)
    )


def filter_tools(tools: Iterable[Dict]) -> List[Dict]:
    """Drop tools that belong to a switched-off area."""
    hidden = hidden_tool_names()
    if not hidden:
        return list(tools)
    return [t for t in tools if (t.get("name") or "").strip().lower() not in hidden]


def filter_agents(agents: Iterable[Dict]) -> List[Dict]:
    hidden = hidden_agent_ids()
    if not hidden:
        return list(agents)
    return [a for a in agents if (a.get("agent_id") or "") not in hidden]
