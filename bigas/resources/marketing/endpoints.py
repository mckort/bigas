from flask import Blueprint, current_app, jsonify, request, send_file
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
    Filter,
    FilterExpression,
    FilterExpressionList,
    OrderBy,
)
import os
import openai
from datetime import date, datetime, timedelta
import time
import logging
import json
import hashlib
import traceback
import threading
import uuid
from decimal import Decimal, InvalidOperation
from bigas.resources.marketing.service import MarketingAnalyticsService
from bigas.resources.marketing.google_ads_portfolio_service import (
    run_google_ads_campaign_portfolio,
)
from bigas.resources.marketing.meta_ads_portfolio_service import (
    run_meta_campaign_portfolio,
)
from bigas.resources.marketing.utils import (
    convert_metric_name,
    convert_dimension_name,
    process_ga_response,
    get_date_range_strings,
    validate_date_range,
    validate_ga4_metrics_dimensions,
    sanitize_error_message,
    validate_request_data
)
import requests
from bs4 import BeautifulSoup
import re
from typing import Dict, Any, Optional, List, Sequence, Tuple

logger = logging.getLogger(__name__)


_ASYNC_JOBS: Dict[str, Dict[str, Any]] = {}
_ASYNC_JOBS_LOCK = threading.Lock()


def _normalize_reddit_spend(spend: Any, row: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """
    Normalize Reddit report spend to a major-currency amount (e.g. EUR/USD).
    Reddit Reports API returns SPEND in micro (divide by 1e6). Values >= 1000 are treated as micro.
    """
    if row and spend is None:
        spend = row.get("amount_spent") or row.get("spend_amount")
    if spend is None:
        return None
    if isinstance(spend, dict):
        spend = spend.get("value") or spend.get("amount") or spend.get("value_micro") or spend.get("amount_micro")
    if spend is None:
        return None
    try:
        num = float(spend)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return num
    # Reddit Reports API: SPEND is in micro (divide by 1 million)
    if num >= 1000:
        by_micro = num / 1e6
        if by_micro < 1e5:
            return round(by_micro, 2)
    if num >= 1e6:
        return round(num / 1e6, 2)
    return round(num, 2)


def _normalize_audience_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize Reddit audience report rows: spend (micro->EUR) and add ctr_pct, cpc per segment."""
    out = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        row = dict(row)
        if row.get("spend") is not None:
            row["spend"] = _normalize_reddit_spend(row["spend"], row)
        try:
            imp = int(row.get("impressions") or 0)
            clk = int(row.get("clicks") or 0)
            reach = int(row.get("reach") or 0)
            spend = row.get("spend")
            if imp > 0:
                row["ctr_pct"] = round(100.0 * clk / imp, 2)
            else:
                row["ctr_pct"] = None
            if clk > 0 and spend is not None:
                row["cpc"] = round(float(spend) / clk, 2)
            else:
                row["cpc"] = None
            if imp > 0 and reach > 0:
                row["frequency"] = round(float(imp) / float(reach), 4)
            else:
                row["frequency"] = None
        except (TypeError, ValueError):
            row["ctr_pct"] = None
            row["cpc"] = None
            row["frequency"] = None
        out.append(row)
    return out


def _build_linkedin_compact_payload(
    storage: Any,
    enriched_path: str,
    sample_limit: int = 50,
) -> Optional[Dict[str, Any]]:
    """Load enriched LinkedIn report from GCS and return compact payload for cross-platform analysis."""
    try:
        obj = storage.get_json(enriched_path)
        if not isinstance(obj, dict):
            return None
        payload = obj.get("payload") or {}
        enriched = payload.get("enriched_response") or {}
        if not isinstance(enriched, dict):
            return None
        elements = enriched.get("elements") or []
        if not elements:
            return None
        summary = enriched.get("summary") or {}
        context = enriched.get("context") or {}
        compact_rows = []
        for el in elements[:sample_limit]:
            if not isinstance(el, dict):
                continue
            pivots_resolved = el.get("pivotValuesResolved") or []
            seg_names = []
            for pv in pivots_resolved:
                if not isinstance(pv, dict):
                    continue
                name = pv.get("name") or pv.get("urn")
                if name:
                    seg_names.append(str(name))
            metrics = el.get("metrics") or {}
            derived = el.get("derived") or {}
            compact_rows.append({
                "segments": seg_names,
                "metrics": {
                    "impressions": metrics.get("impressions"),
                    "clicks": metrics.get("clicks"),
                    "costInLocalCurrency": metrics.get("costInLocalCurrency"),
                },
                "derived": {
                    "ctr": derived.get("ctr"),
                    "avg_cpc_local": derived.get("avg_cpc_local"),
                },
            })
        currency = (
            (context.get("currency") or "local")
            .strip().upper() or "LOCAL"
        )
        return {
            "platform": "linkedin",
            "currency": currency,
            "source_blob": enriched_path,
            "summary": summary,
            "context": context,
            "sample_rows": compact_rows,
        }
    except Exception as e:
        logger.warning("_build_linkedin_compact_payload failed for %s: %s", enriched_path, e)
        return None


def _build_reddit_compact_payload(
    storage: Any,
    enriched_path: str,
    sample_limit: int = 50,
) -> Optional[Dict[str, Any]]:
    """Load enriched Reddit report from GCS and return compact payload for cross-platform analysis."""
    try:
        obj = storage.get_json(enriched_path)
        if not obj or not isinstance(obj, dict):
            return None
        payload = obj.get("payload") or {}
        enriched = payload.get("enriched_response") or {}
        if not isinstance(enriched, dict):
            return None
        elements = enriched.get("elements") or []
        if not elements:
            return None
        summary = enriched.get("summary") or {}
        context = enriched.get("context") or {}
        compact_rows = []
        for el in elements[:sample_limit]:
            if not isinstance(el, dict):
                continue
            compact_rows.append({
                "segments": el.get("segments") or [],
                "metrics": el.get("metrics") or {},
                "derived": el.get("derived") or {},
            })
        reddit_currency = context.get("spend_currency") or "EUR"
        reddit_currency = (reddit_currency.strip().upper() if isinstance(reddit_currency, str) else None) or "EUR"
        return {
            "platform": "reddit",
            "currency": reddit_currency,
            "source_blob": enriched_path,
            "summary": summary,
            "context": context,
            "sample_rows": compact_rows,
            "note": "Spend in context.spend_currency; do not sum across platforms/currencies.",
        }
    except Exception as e:
        logger.warning("_build_reddit_compact_payload failed for %s: %s", enriched_path, e)
        return None


marketing_bp = Blueprint('marketing_bp', __name__)

# This logic is moved from the original app file to be specific to this blueprint.
DEPLOYMENT_MODE = os.environ.get("DEPLOYMENT_MODE", "standalone")
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID")
GA4_API_PROPERTY_ID = f"properties/{GA4_PROPERTY_ID}" if GA4_PROPERTY_ID else None
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Default timeout (in seconds) for outbound HTTP calls to chat platforms such as Discord.
DISCORD_HTTP_TIMEOUT = int(os.environ.get("DISCORD_HTTP_TIMEOUT", "10"))

# Default demographic pivots for LinkedIn portfolio report (API allows max 3 per creative).
# Full set of common options: MEMBER_JOB_TITLE, MEMBER_JOB_FUNCTION, MEMBER_SENIORITY, MEMBER_INDUSTRY, MEMBER_COUNTRY_V2, MEMBER_COMPANY_SIZE
DEFAULT_LINKEDIN_PORTFOLIO_PIVOTS = ["MEMBER_JOB_TITLE", "MEMBER_JOB_FUNCTION", "MEMBER_SENIORITY"]

# Pivots used by the one-command run_linkedin_portfolio_report (job title, function, country).
LINKEDIN_PORTFOLIO_REPORT_PIVOTS = ["MEMBER_JOB_TITLE", "MEMBER_JOB_FUNCTION", "MEMBER_COUNTRY_V2"]

# Simple prompt registry for ad analytics summarization.
# Keyed by (platform, report_type).
AD_SUMMARY_PROMPTS: Dict[Tuple[str, str], Dict[str, str]] = {
    (
        "linkedin",
        "ad_analytics",
    ): {
        "system": (
            "You are a senior B2B performance marketing specialist. "
            "You receive normalized ad performance data from LinkedIn Ads and must:\n"
            "1) Summarize performance clearly for non-technical stakeholders.\n"
            "2) Call out key insights by segment (job titles, job functions, countries, etc.).\n"
            "3) Recommend concrete next steps for budget allocation, targeting, and creative testing.\n"
            "Be concise but insightful, and prefer clear lists over long paragraphs."
        ),
        "user_template": (
            "Here is JSON analytics for {platform} ads:\n\n"
            "{payload}\n\n"
            "Please respond in this structure:\n"
            "1. High-level summary (3–6 bullet points).\n"
            "2. Key insights (by job title, job function, and country; highlight best & worst segments).\n"
            "3. Recommendations (specific next steps for future spend, targeting, and A/B tests).\n"
            "4. Risks / caveats (anything missing or uncertain in the data).\n"
        ),
    },
    (
        "linkedin",
        "creative_portfolio",
    ): {
        "system": (
            "You are a senior B2B performance marketer specializing in paid social (LinkedIn Ads). "
            "You receive summarized per-creative portfolio data and must:\n"
            "1) Highlight which creatives and segments (titles, functions, countries) are driving performance.\n"
            "2) Identify underperforming creatives or segments that should be paused or reworked.\n"
            "3) Provide concrete next steps for budget shifts and new creative or targeting tests.\n"
            "Be concise, structured, and action-oriented."
        ),
        "user_template": (
            "Here is a JSON portfolio of LinkedIn ad creatives:\n\n"
            "{payload}\n\n"
            "Please respond in this structure:\n"
            "1. Portfolio overview (3–6 bullet points).\n"
            "2. Top performing creatives (with key segments and metrics).\n"
            "3. Underperforming creatives or segments and what to change.\n"
            "4. Recommended experiments (new creatives, audiences, bids, or budgets).\n"
            "5. Risks / caveats.\n"
        ),
    },
    (
        "reddit",
        "ad_analytics",
    ): {
        "system": (
            "You are a senior performance marketing specialist. "
            "You receive normalized ad performance data from Reddit Ads and must:\n"
            "1) Summarize performance clearly for non-technical stakeholders.\n"
            "2) Call out key insights by segment (campaign, ad, community/placement if present).\n"
            "3) Recommend concrete next steps for budget allocation, targeting, and creative testing.\n"
            "Note: Spend is in the currency reported (e.g. USD or EUR). Do not add spend across platforms or currencies without explicit conversion.\n"
            "Be concise but insightful, and prefer clear lists over long paragraphs."
        ),
        "user_template": (
            "Here is JSON analytics for {platform} ads (Option A: currencies as-reported; do not sum spend across currencies):\n\n"
            "{payload}\n\n"
            "Please respond in this structure:\n"
            "1. High-level summary (3–6 bullet points).\n"
            "2. Key insights (by campaign, ad, segment; highlight best & worst).\n"
            "3. Recommendations (specific next steps for future spend, targeting, and tests).\n"
            "4. Risks / caveats (anything missing or uncertain; note currency if comparing to other platforms).\n"
        ),
    },
    (
        "reddit",
        "portfolio",
    ): {
        "system": (
            "You are a senior performance marketing specialist. "
            "You receive a combined Reddit Ads report: performance metrics (spend, impressions, clicks, CTR, CPC) plus audience breakdowns (interests, communities, country, region, DMA) with per-segment CTR and CPC. "
            "All spend is in EUR only. Use performance.summary (total_spend, total_impressions, total_clicks, total_ctr_pct, total_cpc) and audience segment rows (clicks, impressions, spend, ctr_pct, cpc) exactly as in the payload. "
            "discovered_campaigns lists all campaigns that had activity in the date range (active/paused/done), sorted by spend; audience_by_campaign gives per-campaign interests and communities for each of those campaigns (like LinkedIn per-creative). "
            "Audience segments are ordered by clicks, highest first. Report them by the exact names and numbers in the payload; do not state zero clicks when the payload shows non-zero. "
            "Call out specific interest and community names when present (e.g. 'News & Education', 'General Entertainment', or community names) with their clicks, CTR %, and CPC. "
            "When audience_scope is 'campaign', the main audience object is for the top campaign by spend; use audience_by_campaign to report per-campaign breakdowns when available. "
            "Never invent, estimate, or substitute numbers: use only the figures in the payload; if a value is missing or null, say so instead of using a placeholder. "
            "Be structured, actionable, and suitable for posting to Discord."
        ),
        "user_template": (
            "Here is a combined Reddit Ads portfolio (performance + audience). All spend in EUR. performance.summary includes total_spend, total_impressions, total_clicks, total_ctr_pct, total_cpc. "
            "discovered_campaigns = list of campaigns in the date range (id, name, spend). audience_by_campaign = per-campaign interests and communities (each segment has clicks, impressions, spend, ctr_pct, cpc). Segments ordered by clicks, top first:\n\n"
            "{payload}\n\n"
            "Please respond in this structure:\n"
            "**Facts** – 4–8 bullet points: discovered campaigns (names and spend); campaign totals (spend EUR, impressions, clicks, CTR %, CPC); top interests per campaign (exact names, e.g. News & Education, with clicks, CTR %, CPC); top communities (name, clicks, CTR %, CPC); geography.\n"
            "**Summary** – 2–4 sentences on what’s working and what isn’t.\n"
            "**Recommendations** – 3–6 concrete next steps (targeting, budget, creative, or audience tests).\n"
            "**Risks / caveats** – Anything missing or uncertain (e.g. date range; account vs campaign scope).\n"
        ),
    },
    (
        "cross_platform",
        "budget_analysis",
    ): {
        "system": (
            "You are a senior marketing analyst. You receive performance data from multiple paid channels (LinkedIn, Reddit, Google Ads, Meta, and optionally others) and, when available, ga4_attribution: Paid Social traffic from Google Analytics 4 broken down by session source (eventCount, activeUsers, keyEvents per source). "
            "Your job is to compare performance across platforms, identify where to allocate more budget and where to focus "
            "(e.g. LinkedIn with focus on job function X or country Y; Reddit with focus on community or interest Z; Google Ads with focus on campaigns or keywords; Meta with focus on campaigns, placements, or audiences). "
            "When ga4_attribution.by_source is present, use it to connect ad spend to outcomes: which sources (linkedin, meta, reddit) drove key events and users, so you can say which paid channel is not only cheap in CPC but also drives conversions. "
            "Use only the figures provided; do not invent numbers. Each platform has a 'currency' field (e.g. SEK, EUR, USD, or LOCAL for LinkedIn). "
            "When stating spend or CPC for a platform, use that platform's 'currency' value: if it is SEK, EUR, USD, etc., state the amount in that currency (e.g. '1.62 SEK', '50 EUR'); if it is LOCAL (e.g. LinkedIn), state 'local currency' or 'account local currency'. Never use € or $ for a platform whose currency is SEK or another code. "
            "Do not add spend across currencies without noting conversion. Be concise and actionable; output is posted to Discord."
        ),
        "user_template": (
            "Here are the platform reports for the period {date_range}:\n\n"
            "{payload}\n\n"
            "Respond in exactly this structure:\n"
            "1. **Summary** – 3–5 bullet points comparing platforms and overall performance.\n"
            "2. **Key data points** – Top metrics per platform (spend, impressions, clicks, CTR, CPC; for LinkedIn highlight top segments; for Reddit top campaigns/communities/interests; for Google Ads top campaigns, cost, conversions, ROAS; for Meta top campaigns, cost, conversions, ROAS). For every amount (spend, CPC, CPA), use the platform's 'currency' field (e.g. 'X SEK', 'Y EUR')—never use € or $ unless that is the platform's currency. If ga4_attribution is present, include which paid sources (LinkedIn, Meta, Reddit, etc.) drove the most key events and users.\n"
            "3. **Recommendation** – Where to spend more marketing budget and on what (e.g. 'LinkedIn with focus on [job title/function/country]', 'Reddit with focus on [communities/interests]', 'Google Ads with focus on [campaigns/keywords]', 'Meta with focus on [campaigns/placements]'). Include 2–4 concrete next steps.\n"
            "4. **Risks / caveats** – Anything missing or uncertain (e.g. date range, currency, scope).\n"
        ),
    },
    (
        "google_ads",
        "portfolio",
    ): {
        "system": (
            "You are a senior performance marketer specializing in Google Ads (search and display). "
            "You receive normalized campaign-level performance data and must:\n"
            "1) Summarize overall performance clearly for non-technical stakeholders.\n"
            "2) Highlight the best and worst performing campaigns with actual numbers.\n"
            "3) Recommend specific budget shifts, bid/keyword changes, and experiments.\n"
            "Be concise, structured, and action-oriented."
        ),
        "user_template": (
            "Here is a JSON portfolio of Google Ads campaigns (daily performance):\n\n"
            "{payload}\n\n"
            "Please respond in this structure:\n"
            "1. Portfolio overview (3–6 bullet points).\n"
            "2. Top performing campaigns (with key metrics like cost, conversions, ROAS).\n"
            "3. Underperforming campaigns and what to change.\n"
            "4. Recommendations for budget shifts, bidding, and experiments.\n"
            "5. Risks / caveats.\n"
        ),
    },
    (
        "meta",
        "portfolio",
    ): {
        "system": (
            "You are a senior performance marketer specializing in Meta Ads (Facebook and Instagram). "
            "You receive normalized campaign-level performance data and must:\n"
            "1) Summarize overall performance clearly for non-technical stakeholders.\n"
            "2) Highlight the best and worst performing campaigns with actual numbers.\n"
            "3) Recommend specific budget shifts, placements, and experiments.\n"
            "All monetary figures (spend, CPC, CPA, ROAS, conversion value) are in the Meta account currency provided in the JSON (for this account typically SEK). "
            "Never assume USD; always mention the currency when you state amounts.\n"
            "Be concise, structured, and action-oriented."
        ),
        "user_template": (
            "Here is a JSON portfolio of Meta (Facebook/Instagram) Ads campaigns (daily performance):\n\n"
            "{payload}\n\n"
            "Please respond in this structure:\n"
            "1. Portfolio overview (3–6 bullet points).\n"
            "2. Top performing campaigns (with key metrics like cost, conversions, ROAS).\n"
            "3. Underperforming campaigns and what to change.\n"
            "4. Recommendations for budget shifts, placements, and experiments.\n"
            "5. Risks / caveats (including any uncertainty about currency; explicitly mention the currency when you state spend or CPC).\n"
        ),
    },
}


class AdsAnalyticsRequest:
    """
    Generic ads analytics request schema for all paid media platforms.

    This is intentionally minimal and JSON-serializable so it can be logged,
    hashed for caching, and reused across LinkedIn, Google Ads, Meta, Reddit, etc.
    """

    def __init__(
        self,
        platform: str,
        endpoint: str,
        finder: str,
        account_urns: List[str],
        start_date: str,
        end_date: str,
        time_granularity: str,
        pivot: Optional[str] = None,
        pivots: Optional[List[str]] = None,
        campaign_urns: Optional[List[str]] = None,
        campaign_group_urns: Optional[List[str]] = None,
        creative_urns: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
        version: Optional[str] = None,
        include_entity_names: bool = False,
    ):
        self.platform = platform
        self.endpoint = endpoint
        self.finder = finder
        self.account_urns = account_urns
        self.start_date = start_date
        self.end_date = end_date
        self.time_granularity = time_granularity
        self.pivot = pivot
        self.pivots = pivots
        self.campaign_urns = campaign_urns
        self.campaign_group_urns = campaign_group_urns
        self.creative_urns = creative_urns
        self.fields = fields
        self.version = version
        self.include_entity_names = include_entity_names

    def to_signature_dict(self) -> Dict[str, Any]:
        """Return a deterministic, JSON-serializable dict used for hashing and logging."""
        return {
            "platform": self.platform,
            "endpoint": self.endpoint,
            "finder": self.finder,
            "account_urns": self.account_urns,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "time_granularity": self.time_granularity,
            "pivot": self.pivot,
            "pivots": self.pivots,
            "campaign_urns": self.campaign_urns,
            "campaign_group_urns": self.campaign_group_urns,
            "creative_urns": self.creative_urns,
            "fields": self.fields,
            "version": self.version,
            "include_entity_names": self.include_entity_names,
        }


def normalize_ids_to_urns(ids: Sequence[Any], urn_prefix: str) -> List[str]:
    """
    Normalize a heterogeneous list of ids (ints, strings, or URNs) into a unique
    list of URNs with the provided LinkedIn prefix.

    Example:
      normalize_ids_to_urns([123, "456", "urn:li:sponsoredCampaign:789"], "sponsoredCampaign")
    """
    out: List[str] = []
    for raw in ids or []:
        s = str(raw).strip()
        if not s:
            continue
        if s.startswith("urn:"):
            out.append(s)
        elif s.isdigit():
            out.append(f"urn:li:{urn_prefix}:{s}")
    # Preserve deterministic ordering while removing duplicates.
    seen = set()
    deduped: List[str] = []
    for v in out:
        if v in seen:
            continue
        seen.add(v)
        deduped.append(v)
    return deduped


def build_ads_cache_keys(
    request: AdsAnalyticsRequest,
    primary_account_urn: str,
) -> Dict[str, Any]:
    """
    Build a deterministic cache key (SHA-256) and blob names for raw + enriched
    ads analytics reports.

    Returns a dict with:
      - request_signature: the dict used for hashing
      - request_hash: hex digest
      - blob_name: storage path for the raw report
      - enriched_blob_name: storage path for the enriched report
      - base_name: logical base for filenames (without date or hash)
    """
    signature = request.to_signature_dict()
    signature_json = json.dumps(
        signature, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    request_hash = hashlib.sha256(signature_json.encode("utf-8")).hexdigest()

    safe_account = primary_account_urn.split(":")[-1]
    hash_prefix = request_hash[:12]
    # Prefer first pivot element for statistics finder, otherwise single pivot.
    pivot_label = (
        (request.pivots[0] if request.pivots else request.pivot)
        if (request.pivots or request.pivot)
        else "UNKNOWN"
    )
    base_name = f"{request.endpoint}_{safe_account}_{pivot_label}"
    blob_name = (
        f"raw_ads/{request.platform}/{request.end_date}/{base_name}_{hash_prefix}.json"
    )
    enriched_blob_name = (
        f"raw_ads/{request.platform}/{request.end_date}/{base_name}_{hash_prefix}.enriched.json"
    )

    return {
        "request_signature": signature,
        "request_hash": request_hash,
        "blob_name": blob_name,
        "enriched_blob_name": enriched_blob_name,
        "base_name": base_name,
    }


def _post_google_ads_portfolio_to_discord(
    webhook_url: str,
    model: str,
    portfolio_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Generate a Google Ads portfolio summary via OpenAI and post it to Discord.
    """
    prompt_cfg = AD_SUMMARY_PROMPTS.get(("google_ads", "portfolio"))
    if not prompt_cfg:
        raise RuntimeError("Prompt configuration missing for Google Ads portfolio")

    system_prompt = prompt_cfg["system"]
    payload = {
        "summary": portfolio_result.get("summary") or {},
        "request_metadata": portfolio_result.get("request_metadata") or {},
        "sample_rows": (portfolio_result.get("rows") or [])[:50],
    }
    user_prompt = prompt_cfg["user_template"].format(
        payload=json.dumps(payload, indent=2),
    )

    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
    completion = openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1100,
        temperature=0.4,
        timeout=40,
    )
    analysis_text = completion.choices[0].message.content.strip()

    meta = portfolio_result.get("request_metadata") or {}
    date_range_s = f"{meta.get('start_date')}–{meta.get('end_date')}"
    customer_id = meta.get("customer_id")

    report_level = (meta.get("report_level") or "campaign").strip().lower()
    level_label = {
        "campaign": "Campaign-level",
        "ad": "Ad-level creative",
        "audience_breakdown": "Audience-breakdown",
    }.get(report_level, "Campaign-level")
    discord_message = (
        "## 📊 Google Ads Portfolio Report\n\n"
        f"{analysis_text}\n\n"
        "---\n"
        f"_{level_label} performance for {date_range_s} (customer {customer_id})._"
    )
    post_long_to_discord(webhook_url, discord_message)

    return {
        "posted": True,
        "webhook_url": webhook_url,
        "model": model,
    }


def _post_meta_portfolio_to_discord(
    webhook_url: str,
    model: str,
    portfolio_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Generate a Meta Ads portfolio summary via OpenAI and post to Discord."""
    prompt_cfg = AD_SUMMARY_PROMPTS.get(("meta", "portfolio"))
    if not prompt_cfg:
        raise RuntimeError("Prompt configuration missing for Meta portfolio")

    system_prompt = prompt_cfg["system"]
    payload = {
        "summary": portfolio_result.get("summary") or {},
        "request_metadata": portfolio_result.get("request_metadata") or {},
        "sample_rows": (portfolio_result.get("rows") or [])[:50],
    }
    user_prompt = prompt_cfg["user_template"].format(
        payload=json.dumps(payload, indent=2),
    )

    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
    completion = openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1100,
        temperature=0.4,
        timeout=40,
    )
    analysis_text = completion.choices[0].message.content.strip()

    meta = portfolio_result.get("request_metadata") or {}
    date_range_s = f"{meta.get('start_date')}–{meta.get('end_date')}"
    account_id = meta.get("account_id")

    report_level = (meta.get("report_level") or "campaign").strip().lower()
    level_label = {
        "campaign": "Campaign-level",
        "ad": "Ad-level creative",
        "audience_breakdown": "Audience-breakdown",
    }.get(report_level, "Campaign-level")
    discord_message = (
        "## 📊 Meta Ads Portfolio Report\n\n"
        f"{analysis_text}\n\n"
        "---\n"
        f"_{level_label} performance for {date_range_s} (account {account_id})._"
    )
    post_long_to_discord(webhook_url, discord_message)

    return {
        "posted": True,
        "webhook_url": webhook_url,
        "model": model,
    }


# In SaaS mode, don't initialize client at module load
# Client will be initialized per-request from SaaS layer
if DEPLOYMENT_MODE == "saas":
    logger.info("SaaS mode: Google Analytics client will be initialized per-request")
    client = None
else:
    try:
        client = BetaAnalyticsDataClient()
    except Exception as e:
        logger.error(f"Failed to initialize Google Analytics Data API client: {e}")
        client = None

CACHE_DURATION = 3600
analytics_cache = {}

# Simple rate limiting storage
request_counts = {}
RATE_LIMIT_WINDOW = 3600  # 1 hour
RATE_LIMIT_MAX_REQUESTS = 100  # Max requests per hour per endpoint

@marketing_bp.route('/linkedin/callback', methods=['GET'])
def linkedin_oauth_callback():
    """
    OAuth redirect handler for LinkedIn Authorization Code flow.

    LinkedIn redirects the user's browser to this endpoint with:
      - code
      - state
      - error / error_description (if denied)

    SECURITY:
    - We intentionally do NOT log the query params to avoid leaking the auth code.
    """
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")
    error_description = request.args.get("error_description")

    if error:
        return (
            f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>LinkedIn OAuth Error</title></head>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;">
    <h2>LinkedIn OAuth error</h2>
    <p><b>error</b>: {error}</p>
    <p><b>error_description</b>: {error_description or ''}</p>
    <p>You can close this tab.</p>
  </body>
</html>""",
            400,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    if not code:
        return (
            """<!doctype html>
<html>
  <head><meta charset="utf-8"><title>LinkedIn OAuth Callback</title></head>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;">
    <h2>LinkedIn OAuth callback</h2>
    <p>No <code>code</code> query parameter was provided.</p>
    <p>You can close this tab.</p>
  </body>
</html>""",
            400,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    # Return the code so the user can copy it into the token exchange step.
    # (We are not exchanging it server-side in this endpoint.)
    safe_state = state or ""
    return (
        f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>LinkedIn OAuth Callback</title></head>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;">
    <h2>LinkedIn OAuth callback received</h2>
    <p><b>state</b>: <code>{safe_state}</code></p>
    <p><b>code</b> (copy this):</p>
    <pre style="background:#f6f8fa; padding: 12px; border-radius: 8px; overflow:auto;">{code}</pre>
    <p>Next, exchange this code for tokens using LinkedIn's <code>/oauth/v2/accessToken</code>.</p>
    <p>You can close this tab.</p>
  </body>
</html>""",
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )


@marketing_bp.route('/reddit/callback', methods=['GET'])
def reddit_oauth_callback():
    """
    OAuth redirect handler for Reddit Authorization Code flow.

    Reddit redirects the user's browser to this endpoint with:
      - code
      - state
      - error (if denied)

    SECURITY:
    - We intentionally do NOT log the query params to avoid leaking the auth code.
    """
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        return (
            f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>Reddit OAuth Error</title></head>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;">
    <h2>Reddit OAuth error</h2>
    <p><b>error</b>: {error}</p>
    <p>You can close this tab.</p>
  </body>
</html>""",
            400,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    if not code:
        return (
            """<!doctype html>
<html>
  <head><meta charset="utf-8"><title>Reddit OAuth Callback</title></head>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;">
    <h2>Reddit OAuth callback</h2>
    <p>No <code>code</code> query parameter was provided.</p>
    <p>You can close this tab.</p>
  </body>
</html>""",
            400,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    safe_state = state or ""
    return (
        f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>Reddit OAuth Callback</title></head>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;">
    <h2>Reddit OAuth callback received</h2>
    <p><b>state</b>: <code>{safe_state}</code></p>
    <p><b>code</b> (copy this):</p>
    <pre style="background:#f6f8fa; padding: 12px; border-radius: 8px; overflow:auto;">{code}</pre>
    <p>Next, exchange this code for tokens using POST /mcp/tools/reddit_exchange_code (or Reddit's /api/v1/access_token).</p>
    <p>You can close this tab.</p>
  </body>
</html>""",
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )


def check_rate_limit(endpoint: str, client_ip: str = "default") -> bool:
    """
    Simple rate limiting check.
    
    Args:
        endpoint: The endpoint being accessed
        client_ip: Client IP address (default for simplicity)
        
    Returns:
        True if request is allowed, False if rate limited
    """
    current_time = time.time()
    key = f"{endpoint}:{client_ip}"
    
    # Clean old entries
    if key in request_counts:
        if current_time - request_counts[key]["timestamp"] > RATE_LIMIT_WINDOW:
            del request_counts[key]
    
    # Check rate limit
    if key in request_counts:
        if request_counts[key]["count"] >= RATE_LIMIT_MAX_REQUESTS:
            return False
        request_counts[key]["count"] += 1
    else:
        request_counts[key] = {
            "count": 1,
            "timestamp": current_time
        }
    
    return True

def get_cached_data(cache_key):
    if cache_key in analytics_cache:
        timestamp, data = analytics_cache[cache_key]
        if time.time() - timestamp < CACHE_DURATION:
            return data
    return None

def set_cached_data(cache_key, data):
    analytics_cache[cache_key] = (time.time(), data)

def get_ga_report(property_id, start_date, end_date, metrics, dimensions, dimension_filters=None, order_bys=None, limit=None, comparison_start_date=None, comparison_end_date=None):
    if not client:
        raise RuntimeError("Google Analytics Data API client is not initialized.")
    property_id_api_format = f"properties/{property_id}" if not property_id.startswith("properties/") else property_id
    date_ranges = [DateRange(start_date=start_date, end_date=end_date, name="current_period")]
    if comparison_start_date and comparison_end_date:
        date_ranges.append(DateRange(start_date=comparison_start_date, end_date=comparison_end_date, name="previous_period"))
    filter_expression = FilterExpression(and_group=FilterExpressionList(expressions=dimension_filters)) if dimension_filters else None
    converted_metrics = [Metric(name=m) for m in metrics]
    converted_dimensions = [Dimension(name=d) for d in dimensions]
    request_obj = RunReportRequest(property=property_id_api_format, date_ranges=date_ranges, metrics=converted_metrics, dimensions=converted_dimensions, dimension_filter=filter_expression, order_bys=list(order_bys) if order_bys else None, limit=limit)
    response = client.run_report(request_obj, timeout=20)
    return response

def get_ga_report_with_cache(property_id, start_date, end_date, metrics, dimensions, dimension_filters=None, order_bys=None, limit=None, comparison_start_date=None, comparison_end_date=None):
    cache_key_parts = [property_id, start_date, end_date, tuple(metrics), tuple(dimensions)]
    if dimension_filters: cache_key_parts.append(json.dumps(str(dimension_filters), sort_keys=True))
    if order_bys: cache_key_parts.append(json.dumps(str(order_bys), sort_keys=True))
    if limit is not None: cache_key_parts.append(str(limit))
    if comparison_start_date: cache_key_parts.append(comparison_start_date)
    if comparison_end_date: cache_key_parts.append(comparison_end_date)
    cache_key = "_".join([str(part) for part in cache_key_parts])
    cached_data = get_cached_data(cache_key)
    if cached_data: return cached_data
    try:
        response = get_ga_report(property_id, start_date, end_date, metrics, dimensions, dimension_filters, order_bys, limit, comparison_start_date, comparison_end_date)
        set_cached_data(cache_key, response)
        return response
    except Exception as e:
        logger.error(f"Error in get_ga_report_with_cache: {str(e)}")
        raise


def _map_ga4_source_to_platform(source: str) -> str:
    """Map GA4 session/first user source to our platform key (linkedin, meta, reddit, google_ads, other)."""
    if not source:
        return "other"
    s = source.lower().strip()
    if "linkedin" in s:
        return "linkedin"
    if "facebook" in s or "instagram" in s or "meta" in s or "fb.com" in s:
        return "meta"
    if "reddit" in s:
        return "reddit"
    if "google" in s:
        return "google_ads"
    return "other"


def _get_ga4_paid_social_attribution(
    property_id: str,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """
    Fetch GA4 Paid Social traffic by User Acquisition (first user) dimensions:
    firstUserDefaultChannelGroup = Paid Social, broken down by firstUserSource.
    Uses metrics: eventCount, activeUsers, conversions (Key Events; API uses 'conversions').
    Attribution is First Click (first touch that brought the user).

    Returns a dict with by_source (list of {source, platform, eventCount, activeUsers, keyEvents?}),
    date_range, and optional note/error.
    """
    if not property_id or not property_id.strip():
        return {"note": "GA4_PROPERTY_ID not set", "by_source": []}
    if not client:
        return {"note": "GA4 client not initialized", "by_source": []}
    property_id = property_id.strip()
    if not property_id.startswith("properties/"):
        property_id = f"properties/{property_id}"
    # User Acquisition scope: "First user" dimensions (first touch)
    dimensions = ["firstUserDefaultChannelGroup", "firstUserSource"]
    dimension_filters = [
        FilterExpression(
            filter=Filter(
                field_name="firstUserDefaultChannelGroup",
                string_filter=Filter.StringFilter(
                    value="Paid Social",
                    match_type=Filter.StringFilter.MatchType.EXACT,
                ),
            ),
        ),
    ]
    # API standard for Key Events is 'conversions'; fallback to request without it if invalid
    metrics_with_conversions = ["eventCount", "activeUsers", "conversions"]
    metrics_fallback = ["eventCount", "activeUsers"]
    response = None
    try:
        response = get_ga_report_with_cache(
            property_id,
            start_date,
            end_date,
            metrics_with_conversions,
            dimensions,
            dimension_filters=dimension_filters,
            limit=50,
        )
    except Exception as e1:
        logger.warning("GA4 Paid Social attribution (with conversions) failed, retrying without: %s", e1)
        try:
            response = get_ga_report_with_cache(
                property_id,
                start_date,
                end_date,
                metrics_fallback,
                dimensions,
                dimension_filters=dimension_filters,
                limit=50,
            )
        except Exception as e2:
            logger.warning("GA4 Paid Social attribution fetch failed: %s", e2)
            return {
                "note": f"GA4 attribution unavailable: {sanitize_error_message(str(e2))}",
                "by_source": [],
                "date_range": {"start_date": start_date, "end_date": end_date},
            }
    rows = process_ga_response(response)
    by_source: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        # firstUserSource = source that first brought the user (e.g. linkedin.com, facebook)
        src = (row.get("firstUserSource") or "").strip()
        platform = _map_ga4_source_to_platform(src)
        event_count = int(row.get("eventCount") or 0)
        active_users = int(row.get("activeUsers") or 0)
        # Key Events: API may return 'conversions' (standard) and/or 'keyEvents'
        key_events_raw = row.get("conversions") or row.get("keyEvents")
        key_events = int(key_events_raw or 0) if key_events_raw is not None else None
        by_source.append({
            "source": src or "(not set)",
            "platform": platform,
            "eventCount": event_count,
            "activeUsers": active_users,
            "keyEvents": key_events,
        })
    by_source.sort(key=lambda x: (-(x.get("eventCount") or 0), -(x.get("activeUsers") or 0)))
    return {
        "by_source": by_source,
        "date_range": {"start_date": start_date, "end_date": end_date},
        "note": "Paid Social from GA4 by first user source (User Acquisition, First Click). Use platform to match ad platforms (linkedin=LinkedIn, meta=Meta/Instagram/Facebook, reddit=Reddit). Key events via conversions metric.",
    }


def _create_async_job(payload: Dict[str, Any], timeout_seconds: int) -> str:
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    now = datetime.utcnow().isoformat()
    with _ASYNC_JOBS_LOCK:
        _ASYNC_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "timeout_seconds": timeout_seconds,
            "progress_pct": 0,
            "stage": "queued",
            "result": None,
            "error": None,
            "payload_preview": {
                "account_urn": payload.get("account_urn"),
                "discovery_relative_range": payload.get("discovery_relative_range"),
                "discovery_start_date": payload.get("discovery_start_date"),
                "discovery_end_date": payload.get("discovery_end_date"),
            },
        }
    return job_id


def _update_async_job(job_id: str, **fields: Any) -> None:
    with _ASYNC_JOBS_LOCK:
        job = _ASYNC_JOBS.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = datetime.utcnow().isoformat()


def _get_async_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _ASYNC_JOBS_LOCK:
        job = _ASYNC_JOBS.get(job_id)
        if not job:
            return None
        return dict(job)


def _run_linkedin_portfolio_job(app_obj: Any, job_id: str, payload: Dict[str, Any]) -> None:
    _run_async_tool_job(
        app_obj=app_obj,
        job_id=job_id,
        payload=payload,
        tool_path="/mcp/tools/run_linkedin_portfolio_report",
        tool_label="LinkedIn portfolio",
    )


def _run_reddit_portfolio_job(app_obj: Any, job_id: str, payload: Dict[str, Any]) -> None:
    _run_async_tool_job(
        app_obj=app_obj,
        job_id=job_id,
        payload=payload,
        tool_path="/mcp/tools/run_reddit_portfolio_report",
        tool_label="Reddit portfolio",
    )


def _run_google_ads_portfolio_job(app_obj: Any, job_id: str, payload: Dict[str, Any]) -> None:
    _run_async_tool_job(
        app_obj=app_obj,
        job_id=job_id,
        payload=payload,
        tool_path="/mcp/tools/run_google_ads_portfolio_report",
        tool_label="Google Ads portfolio",
    )


def _run_meta_portfolio_job(app_obj: Any, job_id: str, payload: Dict[str, Any]) -> None:
    _run_async_tool_job(
        app_obj=app_obj,
        job_id=job_id,
        payload=payload,
        tool_path="/mcp/tools/run_meta_portfolio_report",
        tool_label="Meta portfolio",
    )


def _run_async_tool_job(
    app_obj: Any,
    job_id: str,
    payload: Dict[str, Any],
    tool_path: str,
    tool_label: str,
) -> None:
    """
    Execute any long-running portfolio tool asynchronously by invoking the existing endpoint internally.
    """
    _update_async_job(job_id, status="running", stage="running_pipeline", progress_pct=10)
    try:
        worker_payload = dict(payload)
        worker_payload["async"] = False
        worker_payload["_internal_async_worker"] = True

        internal_headers = {}
        internal_access_key = (payload.get("_internal_access_key") or "").strip()
        if internal_access_key:
            internal_headers["X-Bigas-Access-Key"] = internal_access_key

        with app_obj.app_context():
            with app_obj.test_client() as client:
                resp = client.post(
                    tool_path,
                    json=worker_payload,
                    headers=internal_headers,
                )

        result_body = {}
        if resp.is_json:
            result_body = resp.get_json() or {}
        else:
            result_body = {"raw_response": resp.get_data(as_text=True)}

        if resp.status_code >= 400 or (isinstance(result_body, dict) and result_body.get("error")):
            err = (
                result_body.get("error")
                if isinstance(result_body, dict)
                else f"{tool_label} job failed with status {resp.status_code}"
            )
            if not err:
                err = result_body.get("detail") if isinstance(result_body, dict) else None
            if not err:
                err = f"{tool_label} job failed with status {resp.status_code}"
            _update_async_job(
                job_id,
                status="failed",
                stage="failed",
                progress_pct=100,
                error=sanitize_error_message(str(err)),
                result=result_body if isinstance(result_body, dict) else None,
            )
            return

        _update_async_job(
            job_id,
            status="succeeded",
            stage="completed",
            progress_pct=100,
            result=result_body,
            error=None,
        )
    except Exception as e:
        logger.error("Async %s job %s failed: %s", tool_label, job_id, traceback.format_exc())
        _update_async_job(
            job_id,
            status="failed",
            stage="failed",
            progress_pct=100,
            error=sanitize_error_message(str(e)),
        )


@marketing_bp.route('/mcp/tools/fetch_analytics_report', methods=['POST'])
def fetch_analytics_report():
    # Rate limiting check
    if not check_rate_limit("fetch_analytics_report"):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    
    # Validate request data
    data = request.json
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    start_date, end_date = get_date_range_strings(30)
    start_date = data.get('start_date', start_date)
    end_date = data.get('end_date', end_date)
    metrics = data.get('metrics', ['activeUsers'])
    dimensions = data.get('dimensions', ['country'])
    
    # Validate date range
    is_valid, error_msg = validate_date_range(start_date, end_date)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    # Validate metrics and dimensions
    is_valid, error_msg = validate_ga4_metrics_dimensions(metrics, dimensions)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    try:
        current_ga4_property_id = os.environ.get("GA4_PROPERTY_ID")
        ga_response = get_ga_report_with_cache(current_ga4_property_id, start_date, end_date, metrics, dimensions)
        processed_data = process_ga_response(ga_response)
        return jsonify({"status": "success", "data": processed_data})
    except Exception as e:
        logger.error(f"Error in fetch_analytics_report: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500

@marketing_bp.route('/mcp/tools/fetch_custom_report', methods=['POST'])
def fetch_custom_report():
    # Rate limiting check
    if not check_rate_limit("fetch_custom_report"):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    
    # Validate request data
    data = request.json
    is_valid, error_msg = validate_request_data(data, required_fields=['dimensions', 'metrics', 'date_ranges'])
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    try:
        # Normalize dimensions and metrics
        dimensions = [convert_dimension_name(d) for d in data['dimensions']]
        metrics = [convert_metric_name(m) for m in data['metrics']]
        date_ranges = data['date_ranges']
        
        # Validate metrics and dimensions
        is_valid, error_msg = validate_ga4_metrics_dimensions(metrics, dimensions)
        if not is_valid:
            return jsonify({"error": error_msg}), 400
        
        # Validate date ranges
        for dr in date_ranges:
            is_valid, error_msg = validate_date_range(dr['start_date'], dr['end_date'])
            if not is_valid:
                return jsonify({"error": f"Invalid date range: {error_msg}"}), 400
        
        all_processed_data = {}
        for dr in date_ranges:
            current_ga4_property_id = os.environ.get("GA4_PROPERTY_ID")
            ga_response = get_ga_report_with_cache(current_ga4_property_id, dr['start_date'], dr['end_date'], metrics, dimensions)
            processed_data = process_ga_response(ga_response)
            all_processed_data[dr.get('name', f"{dr['start_date']}_to_{dr['end_date']}")] = processed_data
        return jsonify({"status": "success", "data": all_processed_data})
    except KeyError as e:
        return jsonify({"error": f"Missing required field: {e}"}), 400
    except Exception as e:
        logger.error(f"Error in fetch_custom_report: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500

@marketing_bp.route('/mcp/tools/ask_analytics_question', methods=['POST'])
def ask_analytics_question():
    # Rate limiting check
    if not check_rate_limit("ask_analytics_question"):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    
    # Validate request data
    data = request.json
    is_valid, error_msg = validate_request_data(data, required_fields=['question'])
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    question = data.get('question')
    if not question: 
        return jsonify({"error": "Question not provided"}), 400
    
    # Validate question length
    if len(question) > 500:
        return jsonify({"error": "Question too long (max 500 characters)"}), 400
    
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        current_ga4_property_id = os.environ.get("GA4_PROPERTY_ID")
        answer = service.answer_question(current_ga4_property_id, question)
        return jsonify({"answer": answer})
    except Exception as e:
        logger.error(f"Error in ask_analytics_question: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500

@marketing_bp.route('/mcp/tools/analyze_trends', methods=['POST'])
def analyze_trends():
    # Rate limiting check
    if not check_rate_limit("analyze_trends"):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    
    # Validate request data
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    # Handle different parameter formats
    if 'metric' in data:
        # Single metric format - convert to proper format
        metric_name = convert_metric_name(data['metric'])
        metrics = [metric_name]
    else:
        # Handle array of metrics
        raw_metrics = data.get('metrics', ['activeUsers'])
        metrics = [convert_metric_name(m) for m in raw_metrics]
    
    dimensions = data.get('dimensions', ['country'])
    
    # Validate metrics and dimensions
    is_valid, error_msg = validate_ga4_metrics_dimensions(metrics, dimensions)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    # Handle date range parameter
    date_range = data.get('date_range', 'last_30_days')
    
    # Validate date range parameter
    valid_date_ranges = ['last_7_days', 'last_30_days']
    if date_range not in valid_date_ranges:
        return jsonify({"error": f"Invalid date_range. Must be one of: {', '.join(valid_date_ranges)}"}), 400
    
    # Check if Discord webhook is available (marketing channel)
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_MARKETING") or os.environ.get("DISCORD_WEBHOOK_URL")
    post_to_discord_enabled = webhook_url is not None
    
    try:
        # Define time frames based on date_range parameter
        today = datetime.now()
        if date_range == 'last_7_days':
            time_frames = [
                {
                    "name": "last_7_days", 
                    "start_date": (today - timedelta(days=7)).strftime("%Y-%m-%d"), 
                    "end_date": today.strftime("%Y-%m-%d"), 
                    "comparison_start_date": (today - timedelta(days=14)).strftime("%Y-%m-%d"), 
                    "comparison_end_date": (today - timedelta(days=8)).strftime("%Y-%m-%d")
                }
            ]
        elif date_range == 'last_30_days':
            time_frames = [
                {
                    "name": "last_30_days", 
                    "start_date": (today - timedelta(days=30)).strftime("%Y-%m-%d"), 
                    "end_date": today.strftime("%Y-%m-%d"), 
                    "comparison_start_date": (today - timedelta(days=60)).strftime("%Y-%m-%d"), 
                    "comparison_end_date": (today - timedelta(days=31)).strftime("%Y-%m-%d")
                }
            ]
        else:
            # Default to both time frames
            time_frames = [
                {
                    "name": "last_7_days", 
                    "start_date": (today - timedelta(days=7)).strftime("%Y-%m-%d"), 
                    "end_date": today.strftime("%Y-%m-%d"), 
                    "comparison_start_date": (today - timedelta(days=14)).strftime("%Y-%m-%d"), 
                    "comparison_end_date": (today - timedelta(days=8)).strftime("%Y-%m-%d")
                },
                {
                    "name": "last_30_days", 
                    "start_date": (today - timedelta(days=30)).strftime("%Y-%m-%d"), 
                    "end_date": today.strftime("%Y-%m-%d"), 
                    "comparison_start_date": (today - timedelta(days=60)).strftime("%Y-%m-%d"), 
                    "comparison_end_date": (today - timedelta(days=31)).strftime("%Y-%m-%d")
                }
            ]
        
        # Get actual trend data using the service
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        result = service.trend_analysis_service.analyze_trends_with_insights(
            os.environ.get("GA4_PROPERTY_ID"), metrics, dimensions, date_range
        )
        
        # The result already contains formatted data
        formatted_trends = result["data"]
        
        # Post to Discord if webhook is available
        if post_to_discord_enabled:
            # Create a summary message for Discord
            discord_message = f"# 📈 Trend Analysis Report\n\n"
            discord_message += f"**Metrics**: {', '.join(metrics)}\n"
            discord_message += f"**Dimensions**: {', '.join(dimensions)}\n"
            discord_message += f"**Date Range**: {date_range}\n\n"
            
            # Add key insights from the data
            for time_frame, data in formatted_trends.items():
                summary = data.get("summary", {})
                discord_message += f"**{time_frame.replace('_', ' ').title()}**:\n"
                discord_message += f"• Current Period: {summary.get('current_period_total', 0):,}\n"
                discord_message += f"• Previous Period: {summary.get('previous_period_total', 0):,}\n"
                discord_message += f"• Change: {summary.get('percentage_change', 0):+.1f}%\n"
                discord_message += f"• Trend: {summary.get('trend_direction', 'stable')}\n\n"
            
            # Post the data summary
            post_to_discord(webhook_url, discord_message)
            
            # Post AI insights separately
            ai_insights_message = f"**🤖 AI Insights**:\n{result['ai_insights']}"
            post_to_discord(webhook_url, ai_insights_message)
        
        return jsonify({
            "status": "success", 
            "data": result["data"],
            "ai_insights": result["ai_insights"],
            "discord_posted": post_to_discord_enabled,
            "metadata": {
                "metrics_analyzed": metrics,
                "dimensions_analyzed": dimensions,
                "date_range_requested": date_range,
                "time_frames_analyzed": list(result["data"].keys())
            }
        })
    except Exception as e:
        logger.error(f"Error in analyze_trends: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500

def get_manifest():
    """Returns the manifest for the marketing tools."""
    return {
        "name": "Marketing Tools",
        "description": "Tools for Google Analytics reporting, natural language queries, trend analysis, and report storage.",
        "tools": [
            {"name": "fetch_analytics_report", "description": "Fetches a standard Google Analytics report.", "path": "/mcp/tools/fetch_analytics_report", "method": "POST"},
            {"name": "fetch_custom_report", "description": "Fetches a custom Google Analytics report with specific dimensions and metrics.", "path": "/mcp/tools/fetch_custom_report", "method": "POST"},
            {"name": "ask_analytics_question", "description": "Asks a question about your Google Analytics data in natural language.", "path": "/mcp/tools/ask_analytics_question", "method": "POST"},
            {"name": "analyze_trends", "description": "Analyzes trends in your Google Analytics data over time.", "path": "/mcp/tools/analyze_trends", "method": "POST"},
            {"name": "weekly_analytics_report", "description": "Generates a comprehensive weekly analytics report, posts it to Discord, and stores it for later analysis.", "path": "/mcp/tools/weekly_analytics_report", "method": "POST"},
            {"name": "get_stored_reports", "description": "Retrieves a list of all available stored weekly reports.", "path": "/mcp/tools/get_stored_reports", "method": "GET"},
            {"name": "get_latest_report", "description": "Retrieves the most recent weekly analytics report with summary.", "path": "/mcp/tools/get_latest_report", "method": "GET"},
            {"name": "analyze_underperforming_pages", "description": "Analyzes underperforming pages from stored reports and generates AI-powered improvement suggestions.", "path": "/mcp/tools/analyze_underperforming_pages", "method": "POST"},
            {"name": "cleanup_old_reports", "description": "Cleans up old weekly reports to manage storage costs.", "path": "/mcp/tools/cleanup_old_reports", "method": "POST"},
            {"name": "linkedin_ads_health_check", "description": "Smoke test LinkedIn Ads API access by listing ad accounts.", "path": "/mcp/tools/linkedin_ads_health_check", "method": "GET"},
            {"name": "fetch_linkedin_ad_analytics_report", "description": "Fetch LinkedIn adAnalytics and store raw data in GCS. Supports explicit date range via start_date/end_date or relative_range.", "path": "/mcp/tools/fetch_linkedin_ad_analytics_report", "method": "POST", "parameters": {"type": "object", "properties": {"account_urn": {"type": "string"}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "relative_range": {"type": "string", "enum": ["LAST_DAY", "LAST_7_DAYS", "LAST_30_DAYS"]}, "time_granularity": {"type": "string", "enum": ["DAILY", "MONTHLY", "ALL"], "default": "DAILY"}, "pivot": {"type": "string", "default": "ACCOUNT"}, "pivots": {"type": "array", "items": {"type": "string"}}, "store_raw": {"type": "boolean", "default": True}, "force_refresh": {"type": "boolean", "default": False}, "include_entity_names": {"type": "boolean", "default": False}}}},
            {"name": "linkedin_exchange_code", "description": "Exchange a LinkedIn OAuth authorization code for refresh token (does not log the code).", "path": "/mcp/tools/linkedin_exchange_code", "method": "POST"},
            {"name": "reddit_exchange_code", "description": "Exchange a Reddit OAuth authorization code for refresh token (does not log the code).", "path": "/mcp/tools/reddit_exchange_code", "method": "POST"},
            {"name": "fetch_reddit_ad_analytics_report", "description": "Fetch Reddit Ads performance report and store raw + enriched in GCS.", "path": "/mcp/tools/fetch_reddit_ad_analytics_report", "method": "POST"},
            {"name": "fetch_reddit_audience_report", "description": "Fetch Reddit Ads audience report: interests, communities, country, region, or DMA (reach and metrics per segment).", "path": "/mcp/tools/fetch_reddit_audience_report", "method": "POST", "parameters": {"type": "object", "properties": {"account_id": {"type": "string", "description": "Reddit ad account ID. Optional if REDDIT_AD_ACCOUNT_ID is configured."}, "report_type": {"type": "string", "enum": ["interests", "communities", "country", "region", "dma"], "description": "Audience report type (required)."}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "relative_range": {"type": "string", "enum": ["LAST_7_DAYS", "LAST_30_DAYS"]}, "campaign_id": {"type": "string", "description": "Optional campaign ID for campaign-scoped audience fetch."}, "store_raw": {"type": "boolean", "default": False}, "include_raw_response": {"type": "boolean", "default": False}}, "required": ["report_type"]}},
            {"name": "summarize_reddit_ad_analytics", "description": "Summarize Reddit ads from enriched GCS blob; post to Discord.", "path": "/mcp/tools/summarize_reddit_ad_analytics", "method": "POST", "parameters": {"type": "object", "properties": {"enriched_storage_path": {"type": "string", "description": "GCS path to Reddit enriched blob (required). Example: raw_ads/reddit/2026-02-19/ad_performance_...enriched.json"}, "webhook_url": {"type": "string", "description": "Optional Discord webhook URL override"}, "llm_model": {"type": "string", "default": "gpt-4.1-mini"}, "sample_limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200}}, "required": ["enriched_storage_path"]}},
            {"name": "run_reddit_portfolio_report", "description": "One-command Reddit ads: fetch report, summarize, post to Discord. Supports async mode for MCP clients with short timeouts.", "path": "/mcp/tools/run_reddit_portfolio_report", "method": "POST", "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}, "relative_range": {"type": "string", "enum": ["LAST_7_DAYS", "LAST_30_DAYS"], "default": "LAST_7_DAYS"}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "include_audience": {"type": "boolean", "default": True}, "post_to_discord": {"type": "boolean", "default": True}, "async": {"type": "boolean", "default": False}, "timeout_seconds": {"type": "integer", "default": 300, "minimum": 10, "maximum": 900}}}},
            {"name": "run_reddit_portfolio_report_async", "description": "Async Reddit portfolio report. Returns job_id immediately; poll with get_job_status and get_job_result.", "path": "/mcp/tools/run_reddit_portfolio_report_async", "method": "POST", "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}, "relative_range": {"type": "string", "enum": ["LAST_7_DAYS", "LAST_30_DAYS"], "default": "LAST_7_DAYS"}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "include_audience": {"type": "boolean", "default": True}, "timeout_seconds": {"type": "integer", "default": 300, "minimum": 10, "maximum": 900}}}},
            {"name": "run_linkedin_portfolio_report", "description": "One-command LinkedIn portfolio: discover creatives, fetch demographics, summarize, post to Discord. Supports async mode for MCP clients with short timeouts.", "path": "/mcp/tools/run_linkedin_portfolio_report", "method": "POST", "parameters": {"type": "object", "properties": {"account_urn": {"type": "string"}, "discovery_relative_range": {"type": "string", "enum": ["LAST_7_DAYS", "LAST_30_DAYS", "LAST_90_DAYS"], "default": "LAST_30_DAYS"}, "discovery_start_date": {"type": "string", "description": "YYYY-MM-DD"}, "discovery_end_date": {"type": "string", "description": "YYYY-MM-DD"}, "max_creatives_per_run": {"type": "integer", "default": 10}, "llm_model": {"type": "string", "default": "gpt-4.1-mini"}, "async": {"type": "boolean", "default": False}, "timeout_seconds": {"type": "integer", "default": 300, "minimum": 10, "maximum": 900}}}},
            {"name": "run_linkedin_portfolio_report_async", "description": "Async LinkedIn portfolio report. Returns job_id immediately; poll with get_job_status and get_job_result.", "path": "/mcp/tools/run_linkedin_portfolio_report_async", "method": "POST", "parameters": {"type": "object", "properties": {"account_urn": {"type": "string"}, "discovery_relative_range": {"type": "string", "enum": ["LAST_7_DAYS", "LAST_30_DAYS", "LAST_90_DAYS"], "default": "LAST_30_DAYS"}, "discovery_start_date": {"type": "string", "description": "YYYY-MM-DD"}, "discovery_end_date": {"type": "string", "description": "YYYY-MM-DD"}, "max_creatives_per_run": {"type": "integer", "default": 10}, "llm_model": {"type": "string", "default": "gpt-4.1-mini"}, "timeout_seconds": {"type": "integer", "default": 300, "minimum": 10, "maximum": 900}}}},
            {"name": "get_job_status", "description": "Poll async job status for long-running MCP tools.", "path": "/mcp/tools/get_job_status", "method": "POST", "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}},
            {"name": "get_job_result", "description": "Fetch async job result for long-running MCP tools.", "path": "/mcp/tools/get_job_result", "method": "POST", "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}},
            {"name": "run_google_ads_portfolio_report", "description": "One-command Google Ads portfolio: campaign/ad/audience-breakdown performance, summary, optional Discord. Supports async mode for MCP clients with short timeouts.", "path": "/mcp/tools/run_google_ads_portfolio_report", "method": "POST", "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}, "login_customer_id": {"type": "string"}, "report_level": {"type": "string", "enum": ["campaign", "ad", "audience_breakdown"], "default": "campaign"}, "breakdowns": {"type": "array", "items": {"type": "string"}}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "post_to_discord": {"type": "boolean", "default": False}, "llm_model": {"type": "string", "default": "gpt-4.1-mini"}, "async": {"type": "boolean", "default": False}, "timeout_seconds": {"type": "integer", "default": 300, "minimum": 10, "maximum": 900}}}},
            {"name": "run_google_ads_portfolio_report_async", "description": "Async Google Ads portfolio report. Returns job_id immediately; poll with get_job_status and get_job_result.", "path": "/mcp/tools/run_google_ads_portfolio_report_async", "method": "POST", "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}, "login_customer_id": {"type": "string"}, "report_level": {"type": "string", "enum": ["campaign", "ad", "audience_breakdown"], "default": "campaign"}, "breakdowns": {"type": "array", "items": {"type": "string"}}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "post_to_discord": {"type": "boolean", "default": False}, "llm_model": {"type": "string", "default": "gpt-4.1-mini"}, "timeout_seconds": {"type": "integer", "default": 300, "minimum": 10, "maximum": 900}}}},
            {"name": "run_meta_portfolio_report", "description": "One-command Meta (Facebook/Instagram) Ads campaign portfolio: daily performance, summary, optional Discord. Supports async mode for MCP clients with short timeouts.", "path": "/mcp/tools/run_meta_portfolio_report", "method": "POST", "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}, "report_level": {"type": "string", "enum": ["campaign", "ad", "audience_breakdown"], "default": "campaign"}, "breakdowns": {"type": "array", "items": {"type": "string"}}, "include_targeting": {"type": "boolean", "default": False}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "post_to_discord": {"type": "boolean", "default": False}, "llm_model": {"type": "string", "default": "gpt-4.1-mini"}, "async": {"type": "boolean", "default": False}, "timeout_seconds": {"type": "integer", "default": 300, "minimum": 10, "maximum": 900}}}},
            {"name": "run_meta_portfolio_report_async", "description": "Async Meta portfolio report. Returns job_id immediately; poll with get_job_status and get_job_result.", "path": "/mcp/tools/run_meta_portfolio_report_async", "method": "POST", "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}, "report_level": {"type": "string", "enum": ["campaign", "ad", "audience_breakdown"], "default": "campaign"}, "breakdowns": {"type": "array", "items": {"type": "string"}}, "include_targeting": {"type": "boolean", "default": False}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "post_to_discord": {"type": "boolean", "default": False}, "llm_model": {"type": "string", "default": "gpt-4.1-mini"}, "timeout_seconds": {"type": "integer", "default": 300, "minimum": 10, "maximum": 900}}}},
            {"name": "run_cross_platform_marketing_analysis", "description": "Run LinkedIn, Reddit, Google Ads, and Meta portfolio reports (default last 30 days), then AI comparison: summary, key data, budget recommendation; post to Discord.", "path": "/mcp/tools/run_cross_platform_marketing_analysis", "method": "POST"},
            {"name": "reddit_ads_health_check", "description": "Smoke test Reddit Ads API and list ad accounts (verify REDDIT_AD_ACCOUNT_ID).", "path": "/mcp/tools/reddit_ads_health_check", "method": "GET"},
        ]
    }

@marketing_bp.route('/mcp/tools/linkedin_exchange_code', methods=['POST'])
def linkedin_exchange_code():
    """
    Exchange LinkedIn OAuth authorization code for tokens.

    This endpoint exists to avoid putting LINKEDIN_CLIENT_SECRET into a shell command
    that ends up in terminal history. It uses:
      - LINKEDIN_CLIENT_ID
      - LINKEDIN_CLIENT_SECRET
    from the server environment.

    Request JSON:
      - code: authorization code from /linkedin/callback (required)
      - redirect_uri: optional; defaults to this service's /linkedin/callback URL

    Request JSON (optional):
      - include_access_token: bool (default false). If true, returns access_token in response.

    Response:
      - refresh_token (+ expiry) if provided by LinkedIn
      - scope, expires_in
      - access_token (only if include_access_token=true)
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data, required_fields=["code"])
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"error": "code is required"}), 400

    include_access_token = bool(data.get("include_access_token", False))

    redirect_uri = (data.get("redirect_uri") or "").strip()
    if not redirect_uri:
        # Derive from incoming request in a proxy-safe way.
        # Cloud Run terminates TLS and forwards HTTP to the container; Flask may see http:// unless we use forwarded headers.
        proto = (request.headers.get("X-Forwarded-Proto") or request.scheme or "https").split(",")[0].strip()
        host = (request.headers.get("X-Forwarded-Host") or request.host).split(",")[0].strip()
        redirect_uri = f"{proto}://{host}/linkedin/callback"

    client_id = (os.environ.get("LINKEDIN_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("LINKEDIN_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        return jsonify({"error": "LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET must be set on the server."}), 500

    try:
        # Use application/x-www-form-urlencoded, per LinkedIn docs.
        # Add a User-Agent to reduce the chance of WAF/429 issues.
        token_resp = requests.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "bigas-core/1.0 (Cloud Run)",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=30,
        )

        if token_resp.status_code >= 400:
            # Don't log the code. Log only status + truncated body.
            body_preview = (token_resp.text or "")[:2000]
            retry_after = token_resp.headers.get("Retry-After")
            logger.error(
                "LinkedIn accessToken exchange failed: status=%s retry_after=%s body=%s",
                token_resp.status_code,
                retry_after,
                body_preview,
            )

            # Pass through rate limits so the caller can back off.
            if token_resp.status_code == 429:
                return (
                    jsonify(
                        {
                            "error": "LinkedIn token exchange rate-limited (429). Try again later.",
                            "retry_after": retry_after,
                        }
                    ),
                    429,
                )

            return (
                jsonify(
                    {
                        "error": f"LinkedIn token exchange failed ({token_resp.status_code})",
                        "details_preview": body_preview,
                    }
                ),
                502,
            )

        payload = token_resp.json() if token_resp.text else {}

        # Best-effort: persist access_token to GCS so other endpoints can run without
        # repeatedly calling the refresh-token mint endpoint (helps avoid 429s).
        try:
            access_token = (payload.get("access_token") or "").strip()
            expires_in = int(payload.get("expires_in") or 0)
            if access_token and expires_in > 0:
                from bigas.resources.marketing.storage_service import StorageService

                now_ts = int(time.time())
                storage = StorageService()
                storage.store_json(
                    "secrets/linkedin/access_token.json",
                    {
                        "access_token": access_token,
                        "obtained_at": now_ts,
                        "expires_in": expires_in,
                        "expires_at": now_ts + expires_in,
                        "scope": payload.get("scope"),
                        "note": "Stored by /mcp/tools/linkedin_exchange_code",
                    },
                )
        except Exception:
            # Don't fail the request if storage fails.
            pass

        # Return only the safer fields by default (omit access_token).
        return jsonify(
            {
                "status": "success",
                "expires_in": payload.get("expires_in"),
                **({"access_token": payload.get("access_token")} if include_access_token else {}),
                "refresh_token": payload.get("refresh_token"),
                "refresh_token_expires_in": payload.get("refresh_token_expires_in"),
                "scope": payload.get("scope"),
                "redirect_uri_used": redirect_uri,
                "note": "Set LINKEDIN_REFRESH_TOKEN to the refresh_token value (not the auth code).",
            }
        )
    except Exception as e:
        logger.error(f"Error in linkedin_exchange_code: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/reddit_exchange_code', methods=['POST'])
def reddit_exchange_code():
    """
    Exchange Reddit OAuth authorization code for tokens.

    Uses REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET from the server environment.
    Reddit requires HTTP Basic Auth (client_id as user, client_secret as password).

    Request JSON:
      - code: authorization code from /reddit/callback (required)
      - redirect_uri: optional; defaults to this service's /reddit/callback URL
      - include_access_token: bool (default false). If true, returns access_token in response.

    Response:
      - refresh_token (use duration=permanent in authorize URL to get one)
      - access_token, expires_in, scope
      - access_token only in response if include_access_token=true
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data, required_fields=["code"])
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"error": "code is required"}), 400

    include_access_token = bool(data.get("include_access_token", False))

    redirect_uri = (data.get("redirect_uri") or "").strip()
    if not redirect_uri:
        proto = (request.headers.get("X-Forwarded-Proto") or request.scheme or "https").split(",")[0].strip()
        host = (request.headers.get("X-Forwarded-Host") or request.host).split(",")[0].strip()
        redirect_uri = f"{proto}://{host}/reddit/callback"

    client_id = (os.environ.get("REDDIT_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("REDDIT_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        return jsonify({"error": "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set on the server."}), 500

    try:
        import base64
        basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        token_resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "bigas-core/1.0 (Cloud Run)",
                "Authorization": f"Basic {basic_auth}",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )

        if token_resp.status_code >= 400:
            body_preview = (token_resp.text or "")[:2000]
            retry_after = token_resp.headers.get("Retry-After")
            logger.error(
                "Reddit access_token exchange failed: status=%s retry_after=%s body=%s",
                token_resp.status_code,
                retry_after,
                body_preview,
            )
            if token_resp.status_code == 429:
                return (
                    jsonify(
                        {
                            "error": "Reddit token exchange rate-limited (429). Try again later.",
                            "retry_after": retry_after,
                        }
                    ),
                    429,
                )
            return (
                jsonify(
                    {
                        "error": f"Reddit token exchange failed ({token_resp.status_code})",
                        "details_preview": body_preview,
                    }
                ),
                502,
            )

        payload = token_resp.json() if token_resp.text else {}

        try:
            access_token = (payload.get("access_token") or "").strip()
            expires_in = int(payload.get("expires_in") or 0)
            if access_token and expires_in > 0:
                from bigas.resources.marketing.storage_service import StorageService

                now_ts = int(time.time())
                storage = StorageService()
                storage.store_json(
                    "secrets/reddit/access_token.json",
                    {
                        "access_token": access_token,
                        "obtained_at": now_ts,
                        "expires_in": expires_in,
                        "expires_at": now_ts + expires_in,
                        "scope": payload.get("scope"),
                        "note": "Stored by /mcp/tools/reddit_exchange_code",
                    },
                )
        except Exception:
            pass

        return jsonify(
            {
                "status": "success",
                "expires_in": payload.get("expires_in"),
                **({"access_token": payload.get("access_token")} if include_access_token else {}),
                "refresh_token": payload.get("refresh_token"),
                "scope": payload.get("scope"),
                "redirect_uri_used": redirect_uri,
                "note": "Set REDDIT_REFRESH_TOKEN to the refresh_token value (use duration=permanent when authorizing).",
            }
        )
    except Exception as e:
        logger.error(f"Error in reddit_exchange_code: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/linkedin_ads_health_check', methods=['GET'])
def linkedin_ads_health_check():
    """
    Smoke test LinkedIn Ads API:
    - Mint access token from refresh token
    - List ad accounts
    """
    try:
        from bigas.resources.marketing.linkedin_ads_service import LinkedInAdsService

        svc = LinkedInAdsService()
        data = svc.list_ad_accounts(count=10)
        elements = data.get("elements", []) or []
        account_ids = []
        account_urns = []
        for el in elements:
            # In /rest/adAccounts responses, `id` is typically a numeric sponsoredAccount id.
            raw_id = el.get("id")
            if raw_id is None:
                raw_id = el.get("account") or el.get("urn")

            if raw_id is None:
                continue

            # Normalize to sponsoredAccount URN for reporting usage.
            if isinstance(raw_id, int) or (isinstance(raw_id, str) and raw_id.isdigit()):
                account_id = int(raw_id)
                account_ids.append(account_id)
                account_urns.append(f"urn:li:sponsoredAccount:{account_id}")
            elif isinstance(raw_id, str) and raw_id.startswith("urn:"):
                account_urns.append(raw_id)

        return jsonify(
            {
                "status": "success",
                "ad_accounts_count": len(elements),
                "ad_account_ids": account_ids[:20],
                "ad_account_urns": account_urns[:20],
                "raw_preview": elements[:5],
            }
        )
    except Exception as e:
        logger.error(f"Error in linkedin_ads_health_check: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/reddit_ads_health_check', methods=['GET'])
def reddit_ads_health_check():
    """
    Smoke test Reddit Ads API: mint access token, then Get Me and list ad accounts (v3 flow).
    Use this to verify REDDIT_AD_ACCOUNT_ID or to discover your ad account IDs.
    """
    try:
        from bigas.resources.marketing.reddit_ads_service import (
            RedditAdsService,
            RedditAuthError,
            RedditApiError,
        )

        svc = RedditAdsService()
        me = svc.get_me()
        accounts = []
        account_list = []
        try:
            data = svc.list_ad_accounts()
            accounts = data.get("data") or data.get("ad_accounts") or data.get("results") or []
            if isinstance(accounts, dict):
                accounts = list(accounts.values()) if accounts else []
            if not isinstance(accounts, list):
                accounts = []
            for acc in accounts[:20]:
                if not isinstance(acc, dict):
                    continue
                acc_id = acc.get("id") or acc.get("ad_account_id") or acc.get("account_id")
                name = acc.get("name") or acc.get("account_name") or ""
                account_list.append({"id": acc_id, "name": name})
        except RedditApiError as e:
            if e.status_code == 404:
                return jsonify(
                    {
                        "status": "success",
                        "message": "Token works (Get Me succeeded). Listing ad accounts returned 404; paths may differ. Use REDDIT_AD_ACCOUNT_ID from Reddit Ads Manager.",
                        "me_preview": me,
                        "ad_accounts_count": 0,
                        "ad_accounts": [],
                    }
                )
            raise

        return jsonify(
            {
                "status": "success",
                "ad_accounts_count": len(accounts),
                "ad_accounts": account_list,
                "me_preview": me,
                "raw_preview": accounts[:5] if accounts else [],
            }
        )
    except RedditAuthError as e:
        logger.error(f"Reddit auth error in reddit_ads_health_check: {e} detail={getattr(e, 'detail', None)}")
        response_body = {"error": sanitize_error_message(str(e))}
        if getattr(e, "detail", None):
            response_body["reddit_error"] = e.detail
        if getattr(e, "response_body", None):
            response_body["reddit_response_body"] = e.response_body
        return jsonify(response_body), 401
    except Exception as e:
        logger.error(f"Error in reddit_ads_health_check: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/fetch_linkedin_ad_analytics_report', methods=['POST'])
def fetch_linkedin_ad_analytics_report():
    """
    Fetch LinkedIn adAnalytics for a given ad account URN.

    Request JSON:
      - account_urn: urn:li:sponsoredAccount:XXXX (required if not set in env)
      - start_date: YYYY-MM-DD (default: last 7 days)
      - end_date: YYYY-MM-DD (default: today)
      - relative_range: optional shortcut instead of explicit dates. One of:
          * LAST_DAY       -> yesterday only
          * LAST_7_DAYS    -> last 7 full days ending yesterday
          * LAST_30_DAYS   -> last 30 full days ending yesterday
        If start_date/end_date are provided, they TAKE PRECEDENCE over relative_range.
      - time_granularity: DAILY|MONTHLY|ALL (default: DAILY)
      - pivot: ACCOUNT|CAMPAIGN|CREATIVE|... (default: ACCOUNT). Used with the analytics finder (single pivot).
      - pivots: optional list of pivots for the statistics finder (up to 3). Example: ["CREATIVE","MEMBER_JOB_TITLE"]
      - campaign_ids: optional list of numeric campaign ids (e.g. [474442423])
      - campaign_group_ids: optional list of numeric campaign group ids (e.g. [775062233])
      - creative_ids: optional list of numeric creative/ad ids (e.g. [1165984713])
      - fields: optional list of fields to request from LinkedIn (e.g. ["impressions","clicks","costInLocalCurrency"])
      - store_raw: bool (default: true)
      - force_refresh: bool (default: false). If true, bypass cache and refetch even if the exact report exists.
      - include_entity_names: bool (default: false). If true, store an additional enriched JSON with IDs/URNs resolved where possible.

    Raw report caching (option B):
      - We compute a deterministic hash from the request parameters.
      - If the blob already exists in GCS, we return it instead of refetching (unless force_refresh=true).
      - Storage layout: raw_ads/linkedin/{end_date}/ad_analytics_{accountId}_{pivot}_{hashPrefix}.json
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    # Date handling: explicit dates win, otherwise relative_range, otherwise last 7 days.
    today = datetime.utcnow().date()
    default_end = today
    default_start = default_end - timedelta(days=7)

    account_urn = (data.get("account_urn") or os.environ.get("LINKEDIN_AD_ACCOUNT_URN") or "").strip()

    relative_range_raw = (data.get("relative_range") or "").strip().upper()
    start_date_s = (data.get("start_date") or "").strip()
    end_date_s = (data.get("end_date") or "").strip()

    if not start_date_s or not end_date_s:
        # Only apply relative_range when explicit dates are not both provided.
        if relative_range_raw:
            if relative_range_raw == "LAST_DAY":
                # Yesterday only.
                end = today - timedelta(days=1)
                start = end
            elif relative_range_raw == "LAST_7_DAYS":
                end = today - timedelta(days=1)
                start = end - timedelta(days=6)
            elif relative_range_raw == "LAST_30_DAYS":
                end = today - timedelta(days=1)
                start = end - timedelta(days=29)
            else:
                return jsonify({"error": "relative_range must be one of: LAST_DAY, LAST_7_DAYS, LAST_30_DAYS"}), 400

            if not start_date_s:
                start_date_s = start.isoformat()
            if not end_date_s:
                end_date_s = end.isoformat()

    # Final fallback if nothing was provided / resolved.
    if not start_date_s:
        start_date_s = default_start.isoformat()
    if not end_date_s:
        end_date_s = default_end.isoformat()
    time_granularity = (data.get("time_granularity") or "DAILY").strip().upper()
    pivot = (data.get("pivot") or "ACCOUNT").strip().upper()
    pivots = data.get("pivots")
    if pivots is not None and not isinstance(pivots, list):
        return jsonify({"error": "pivots must be a list of pivot names"}), 400
    campaign_ids = data.get("campaign_ids") or []
    campaign_group_ids = data.get("campaign_group_ids") or []
    creative_ids = data.get("creative_ids") or []
    fields = data.get("fields")
    if fields is not None and not isinstance(fields, list):
        return jsonify({"error": "fields must be a list of field names"}), 400
    store_raw = data.get("store_raw", True)
    force_refresh = bool(data.get("force_refresh", False))
    include_entity_names = bool(data.get("include_entity_names", False))

    is_valid, error_msg = validate_date_range(start_date_s, end_date_s)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    if not account_urn:
        return jsonify({"error": "account_urn is required (or set LINKEDIN_AD_ACCOUNT_URN)."}), 400

    # Accept either a sponsoredAccount URN or a numeric account id.
    account_urn = account_urn.strip()
    if account_urn.isdigit():
        account_urn = f"urn:li:sponsoredAccount:{account_urn}"

    try:
        from bigas.resources.marketing.linkedin_ads_service import LinkedInAdsService
        from bigas.resources.marketing.storage_service import StorageService

        svc = LinkedInAdsService()
        start_d = date.fromisoformat(start_date_s)
        end_d = date.fromisoformat(end_date_s)

        # Build optional filters using shared normalization helpers.
        campaign_urns = normalize_ids_to_urns(
            campaign_ids, urn_prefix="sponsoredCampaign"
        )
        campaign_group_urns = normalize_ids_to_urns(
            campaign_group_ids, urn_prefix="sponsoredCampaignGroup"
        )
        creative_urns = normalize_ids_to_urns(
            creative_ids, urn_prefix="sponsoredCreative"
        )

        cleaned_fields = None
        if fields:
            cleaned_fields = sorted({str(f).strip() for f in fields if str(f).strip()})

        linkedin_version = os.environ.get("LINKEDIN_VERSION") or "202601"

        # Always request these so results are attributable and include metrics (LinkedIn returns no elements without metric fields).
        required_fields = {"dateRange", "pivotValues", "impressions", "clicks", "costInLocalCurrency"}
        final_fields = (
            sorted(set((cleaned_fields or [])) | required_fields)
            if cleaned_fields is not None
            else sorted(required_fields)
        )

        pivots_clean: Optional[List[str]] = None
        if pivots:
            pivots_clean = [str(p).strip().upper() for p in pivots if str(p).strip()]
            if len(pivots_clean) > 3:
                return (
                    jsonify(
                        {
                            "error": "pivots supports up to 3 elements for LinkedIn adAnalytics statistics"
                        }
                    ),
                    400,
                )

        # Build generic ads analytics request + cache keys for this LinkedIn report.
        analytics_request = AdsAnalyticsRequest(
            platform="linkedin",
            endpoint="ad_analytics",
            finder=("statistics" if pivots_clean else "analytics"),
            account_urns=[account_urn],
            start_date=start_date_s,
            end_date=end_date_s,
            time_granularity=time_granularity,
            pivot=pivot,
            pivots=pivots_clean,
            campaign_urns=campaign_urns or None,
            campaign_group_urns=campaign_group_urns or None,
            creative_urns=creative_urns or None,
            fields=final_fields,
            version=linkedin_version,
            include_entity_names=include_entity_names,
        )

        cache_info = build_ads_cache_keys(
            request=analytics_request,
            primary_account_urn=account_urn,
        )
        request_signature = cache_info["request_signature"]
        request_hash = cache_info["request_hash"]
        blob_name = cache_info["blob_name"]
        enriched_blob_name = cache_info["enriched_blob_name"]

        # Cache hit: if the exact report already exists, return it.
        if store_raw and not force_refresh:
            storage = StorageService()
            if storage.blob_exists(blob_name):
                cached = storage.get_json(blob_name) or {}
                cached_payload = cached.get("payload") if isinstance(cached, dict) else None
                cached_payload = cached_payload if isinstance(cached_payload, dict) else {}
                cached_response = cached_payload.get("response") if isinstance(cached_payload, dict) else None

                elements = cached_response.get("elements", []) if isinstance(cached_response, dict) else []
                out = {
                    "status": "success",
                    "from_cache": True,
                    "request_hash": request_hash,
                    "account_urn": account_urn,
                    "date_range": {"start_date": start_date_s, "end_date": end_date_s},
                    "elements_count": len(elements) if isinstance(elements, list) else None,
                    "elements_preview": (elements[:10] if isinstance(elements, list) else None),
                    "stored": True,
                    "storage_path": blob_name,
                }
                if include_entity_names and storage.blob_exists(enriched_blob_name):
                    out["enriched_storage_path"] = enriched_blob_name
                return jsonify(out)

        if pivots_clean:
            raw = svc.ad_analytics_statistics(
                start_date=start_d,
                end_date=end_d,
                time_granularity=time_granularity,
                pivots=pivots_clean,
                account_urns=[account_urn],
                campaign_urns=campaign_urns or None,
                campaign_group_urns=campaign_group_urns or None,
                creative_urns=creative_urns or None,
                fields=final_fields,
            )
        else:
            raw = svc.ad_analytics(
                start_date=start_d,
                end_date=end_d,
                time_granularity=time_granularity,
                pivot=pivot,
                account_urns=[account_urn],
                campaign_urns=campaign_urns or None,
                campaign_group_urns=campaign_group_urns or None,
                creative_urns=creative_urns or None,
                fields=final_fields,
            )

        stored = False
        storage_path = None
        if store_raw:
            storage = StorageService()
            storage_path = storage.store_raw_ads_report_at_blob(
                platform="linkedin",
                blob_name=blob_name,
                report_data={
                    "request": {
                        "account_urn": account_urn,
                        "start_date": start_date_s,
                        "end_date": end_date_s,
                        "time_granularity": time_granularity,
                        "pivot": pivot,
                        "pivots": pivots_clean,
                        "campaign_urns": campaign_urns or None,
                        "campaign_group_urns": campaign_group_urns or None,
                        "creative_urns": creative_urns or None,
                        "fields": final_fields,
                        "version": linkedin_version,
                        "request_hash": request_hash,
                        "include_entity_names": include_entity_names,
                    },
                    "response": raw,
                },
                report_date=end_date_s,
                metadata={"request_hash": request_hash},
            )
            stored = True

            if include_entity_names:
                try:
                    safe_account_id = None
                    try:
                        safe_account_id = int(account_urn.split(":")[-1])
                    except Exception:
                        safe_account_id = None

                    enriched = _enrich_linkedin_adanalytics_response(
                        raw,
                        account_id=safe_account_id,
                        svc=svc,
                        context={
                            "creative_urns": creative_urns or None,
                        },
                    )
                    storage.store_json(
                        blob_name=enriched_blob_name,
                        data={
                            "metadata": {
                                "platform": "linkedin",
                                "report_date": end_date_s,
                                "report_type": "raw_ads_enriched",
                                "stored_at": datetime.utcnow().isoformat(),
                                "version": "1.0",
                                "request_hash": request_hash,
                            },
                            "payload": {
                                "request_hash": request_hash,
                                "source_blob": blob_name,
                                "enriched_response": enriched,
                            },
                        },
                    )
                except Exception:
                    # Best-effort enrichment only; never fail the report fetch because of it.
                    logger.warning("LinkedIn enrichment failed: %s", traceback.format_exc())

        elements = raw.get("elements", []) if isinstance(raw, dict) else []

        return jsonify(
            {
                "status": "success",
                "from_cache": False,
                "request_hash": request_hash,
                "account_urn": account_urn,
                "date_range": {"start_date": start_date_s, "end_date": end_date_s},
                "elements_count": len(elements) if isinstance(elements, list) else None,
                "elements_preview": (elements[:10] if isinstance(elements, list) else None),
                "stored": stored,
                "storage_path": storage_path,
                "enriched_storage_path": (enriched_blob_name if (store_raw and include_entity_names) else None),
            }
        )
    except Exception as e:
        logger.error(f"Error in fetch_linkedin_ad_analytics_report: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/fetch_reddit_ad_analytics_report', methods=['POST'])
def fetch_reddit_ad_analytics_report():
    """
    Fetch Reddit Ads performance report for an ad account. Stores raw + enriched (normalized) in GCS.

    Request JSON:
      - account_id: Reddit ad account ID (required if not set in env REDDIT_AD_ACCOUNT_ID)
      - start_date / end_date: YYYY-MM-DD, or use relative_range: LAST_7_DAYS | LAST_30_DAYS
      - dimensions: optional list (default: campaign_id, ad_id, day)
      - metrics: optional list (default: impressions, clicks, spend, ctr, ecpc)
      - store_raw: bool (default: true)
      - force_refresh: bool (default: false)
    """
    data = request.json or {}
    today = datetime.utcnow().date()
    default_end = today
    default_start = default_end - timedelta(days=7)

    account_id = (data.get("account_id") or os.environ.get("REDDIT_AD_ACCOUNT_ID") or "").strip()
    relative_range_raw = (data.get("relative_range") or "").strip().upper()
    start_date_s = (data.get("start_date") or "").strip()
    end_date_s = (data.get("end_date") or "").strip()

    if not start_date_s or not end_date_s:
        if relative_range_raw:
            if relative_range_raw == "LAST_7_DAYS":
                end = today - timedelta(days=1)
                start = end - timedelta(days=6)
            elif relative_range_raw == "LAST_30_DAYS":
                end = today - timedelta(days=1)
                start = end - timedelta(days=29)
            else:
                return jsonify({"error": "relative_range must be one of: LAST_7_DAYS, LAST_30_DAYS"}), 400
            start_date_s = start_date_s or start.isoformat()
            end_date_s = end_date_s or end.isoformat()
    if not start_date_s:
        start_date_s = default_start.isoformat()
    if not end_date_s:
        end_date_s = default_end.isoformat()

    dimensions = data.get("dimensions")
    metrics_list = data.get("metrics")
    if dimensions is not None and not isinstance(dimensions, list):
        return jsonify({"error": "dimensions must be a list"}), 400
    if metrics_list is not None and not isinstance(metrics_list, list):
        return jsonify({"error": "metrics must be a list"}), 400
    store_raw = data.get("store_raw", True)
    force_refresh = bool(data.get("force_refresh", False))

    is_valid, error_msg = validate_date_range(start_date_s, end_date_s)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    if not account_id:
        return jsonify({"error": "account_id is required (or set REDDIT_AD_ACCOUNT_ID)."}), 400

    try:
        from bigas.resources.marketing.reddit_ads_service import RedditAdsService, RedditApiError
        from bigas.resources.marketing.storage_service import StorageService

        svc = RedditAdsService()
        start_d = date.fromisoformat(start_date_s)
        end_d = date.fromisoformat(end_date_s)

        analytics_request = AdsAnalyticsRequest(
            platform="reddit",
            endpoint="ad_performance",
            finder="report",
            account_urns=[account_id],
            start_date=start_date_s,
            end_date=end_date_s,
            time_granularity="DAILY",
            pivot="CAMPAIGN",
            pivots=None,
            fields=metrics_list,
        )
        cache_info = build_ads_cache_keys(
            request=analytics_request,
            primary_account_urn=account_id,
        )
        request_hash = cache_info["request_hash"]
        blob_name = cache_info["blob_name"]
        enriched_blob_name = cache_info["enriched_blob_name"]

        if store_raw and not force_refresh:
            storage = StorageService()
            if storage.blob_exists(blob_name):
                cached = storage.get_json(blob_name) or {}
                payload = cached.get("payload") or {}
                raw_data = payload.get("raw_response") or {}
                data_rows = raw_data.get("data") if isinstance(raw_data, dict) else []
                return jsonify(
                    {
                        "status": "success",
                        "from_cache": True,
                        "request_hash": request_hash,
                        "account_id": account_id,
                        "date_range": {"start_date": start_date_s, "end_date": end_date_s},
                        "elements_count": len(data_rows) if isinstance(data_rows, list) else None,
                        "storage_path": blob_name,
                        "enriched_storage_path": enriched_blob_name,
                    }
                )

        report_result = svc.get_performance_report(
            account_id=account_id,
            start_date=start_d,
            end_date=end_d,
            dimensions=dimensions,
            metrics=metrics_list,
        )
        raw_response = report_result.get("raw_response") or {}
        data_rows = report_result.get("data") or []
        if not isinstance(data_rows, list):
            data_rows = []

        # Normalize to Option A schema: segments, metrics (impressions, clicks, spend, reach), spend_currency, derived (ctr_pct, avg_cpc, frequency)
        # Reddit dashboard often shows a single currency (e.g. EUR) per account, but the API
        # can return different or mixed currencies. We normalize spend to a major unit and
        # track the per-row currency explicitly; the top-level context currency is derived
        # from the set of row currencies below.
        elements = []
        # Default fallback if no currency information is present in rows.
        default_currency = "EUR"
        for row in data_rows:
            if not isinstance(row, dict):
                continue
            seg_parts = []
            for k in ["campaign_id", "campaign_name", "ad_id", "ad_name", "day", "country", "community"]:
                v = row.get(k)
                if v is not None and str(v).strip():
                    seg_parts.append(f"{k}={v}")
            segments = seg_parts if seg_parts else [str(row)]
            imp = row.get("impressions")
            clk = row.get("clicks")
            reach = row.get("reach")
            spend = _normalize_reddit_spend(row.get("spend"), row)
            ctr = row.get("ctr")
            ecpc = row.get("ecpc")
            row_currency = (
                (row.get("currency") or row.get("spend_currency") or "")
                .strip()
                .upper()
                or default_currency
            )
            frequency = None
            try:
                imp_i = int(imp) if imp is not None else None
                reach_i = int(reach) if reach is not None else None
                if imp_i is not None and reach_i and reach_i > 0:
                    frequency = round(float(imp_i) / float(reach_i), 4)
            except Exception:
                frequency = None
            elements.append({
                "segments": segments,
                "campaign_id": row.get("campaign_id"),
                "campaign_name": row.get("campaign_name"),
                "metrics": {
                    "impressions": imp,
                    "clicks": clk,
                    "reach": reach,
                    "spend": spend,
                    "spend_currency": row_currency,
                },
                "derived": {
                    "ctr_pct": float(ctr) if ctr is not None else None,
                    "avg_cpc": float(ecpc) if ecpc is not None else None,
                    "frequency": frequency,
                },
            })

        # Derive a stable context currency for the enriched payload:
        # - If all rows share the same currency, use that.
        # - If there are no rows, fall back to the default.
        # - If multiple currencies appear, mark as MIXED and expose the set for downstream tools.
        currency_values = {
            (el.get("metrics") or {}).get("spend_currency")
            for el in elements
            if isinstance(el, dict) and (el.get("metrics") or {}).get("spend_currency")
        }
        if not currency_values:
            context_currency = default_currency
            context_currencies = []
        elif len(currency_values) == 1:
            context_currency = next(iter(currency_values))
            context_currencies = [context_currency]
        else:
            context_currency = "MIXED"
            context_currencies = sorted({c for c in currency_values if c})

        stored = False
        storage_path = None
        if store_raw:
            storage = StorageService()
            storage.store_raw_ads_report_at_blob(
                platform="reddit",
                blob_name=blob_name,
                report_data={
                    "request": {
                        "account_id": account_id,
                        "start_date": start_date_s,
                        "end_date": end_date_s,
                        "request_hash": request_hash,
                    },
                    "raw_response": raw_response,
                    "data": data_rows,
                },
                report_date=end_date_s,
                metadata={"request_hash": request_hash},
            )
            storage.store_json(
                blob_name=enriched_blob_name,
                data={
                    "metadata": {
                        "platform": "reddit",
                        "report_date": end_date_s,
                        "report_type": "raw_ads_enriched",
                        "stored_at": datetime.utcnow().isoformat(),
                        "version": "1.0",
                        "request_hash": request_hash,
                    },
                    "payload": {
                        "request_hash": request_hash,
                        "source_blob": blob_name,
                        "enriched_response": {
                            "summary": {"total_rows": len(elements)},
                            "context": {
                                "account_id": account_id,
                                "spend_currency": context_currency,
                                "currencies": context_currencies,
                            },
                            "elements": elements,
                        },
                    },
                },
            )
            stored = True
            storage_path = blob_name

        out = {
            "status": "success",
            "from_cache": False,
            "request_hash": request_hash,
            "account_id": account_id,
            "date_range": {"start_date": start_date_s, "end_date": end_date_s},
            "elements_count": len(elements),
            "stored": stored,
            "storage_path": storage_path,
            "enriched_storage_path": enriched_blob_name if store_raw else None,
        }
        if len(elements) == 0 and raw_response:
            data = raw_response.get("data")
            debug = {
                "raw_response_keys": list(raw_response.keys()),
                "data_type": type(data).__name__,
                "data_keys": list(data.keys()) if isinstance(data, dict) else None,
            }
            if isinstance(data, dict) and "metrics" in data:
                m = data["metrics"]
                debug["metrics_type"] = type(m).__name__
                if isinstance(m, list):
                    debug["metrics_len"] = len(m)
                    if m and isinstance(m[0], dict):
                        debug["metrics_first_keys"] = list(m[0].keys())
                elif isinstance(m, dict):
                    debug["metrics_keys"] = list(m.keys())
            out["_debug_reddit_response"] = debug
        return jsonify(out)
    except RedditApiError as e:
        logger.error("Reddit API error in fetch_reddit_ad_analytics_report: %s", e.response_text)
        out = {"error": sanitize_error_message(str(e))}
        if e.response_text:
            out["reddit_response"] = sanitize_error_message(e.response_text[:2000])
        return jsonify(out), 500
    except Exception as e:
        logger.error(f"Error in fetch_reddit_ad_analytics_report: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500


# Reddit audience report types: breakdowns (up to 3; 4 for COUNTRY+REGION) + fields including REACH
REDDIT_AUDIENCE_REPORT_TYPES = {
    "interests": {"breakdowns": ["INTEREST"], "fields": ["REACH", "IMPRESSIONS", "CLICKS", "SPEND"]},
    "communities": {"breakdowns": ["COMMUNITY"], "fields": ["REACH", "IMPRESSIONS", "CLICKS", "SPEND"]},
    "country": {"breakdowns": ["COUNTRY"], "fields": ["REACH", "IMPRESSIONS", "CLICKS", "SPEND"]},
    "region": {"breakdowns": ["COUNTRY", "REGION"], "fields": ["REACH", "IMPRESSIONS", "CLICKS", "SPEND"]},
    "dma": {"breakdowns": ["DMA"], "fields": ["REACH", "IMPRESSIONS", "CLICKS", "SPEND"]},
}


@marketing_bp.route('/mcp/tools/fetch_reddit_audience_report', methods=['POST'])
def fetch_reddit_audience_report():
    """
    Fetch Reddit Ads audience/demographics report: interests, communities, country, region, or DMA.
    Returns reach and metrics per segment (e.g. per interest, per community, per country).

    Request JSON:
      - account_id: optional (default REDDIT_AD_ACCOUNT_ID)
      - report_type: "interests" | "communities" | "country" | "region" | "dma" (required)
      - start_date / end_date: YYYY-MM-DD, or relative_range: LAST_7_DAYS | LAST_30_DAYS
      - store_raw: bool (default false) — store raw response in GCS under raw_ads/reddit/audience/
    """
    data = request.json or {}
    today = datetime.utcnow().date()
    default_end = today
    default_start = default_end - timedelta(days=7)

    account_id = (data.get("account_id") or os.environ.get("REDDIT_AD_ACCOUNT_ID") or "").strip()
    if not account_id:
        return jsonify({"error": "account_id is required (or set REDDIT_AD_ACCOUNT_ID)."}), 400

    report_type = (data.get("report_type") or "").strip().lower()
    if report_type not in REDDIT_AUDIENCE_REPORT_TYPES:
        return jsonify({
            "error": "report_type is required and must be one of: " + ", ".join(REDDIT_AUDIENCE_REPORT_TYPES),
            "allowed": list(REDDIT_AUDIENCE_REPORT_TYPES.keys()),
        }), 400

    relative_range_raw = (data.get("relative_range") or "").strip().upper()
    start_date_s = (data.get("start_date") or "").strip()
    end_date_s = (data.get("end_date") or "").strip()
    if not start_date_s or not end_date_s:
        if relative_range_raw == "LAST_7_DAYS":
            end = today - timedelta(days=1)
            start = end - timedelta(days=6)
            start_date_s = start_date_s or start.isoformat()
            end_date_s = end_date_s or end.isoformat()
        elif relative_range_raw == "LAST_30_DAYS":
            end = today - timedelta(days=1)
            start = end - timedelta(days=29)
            start_date_s = start_date_s or start.isoformat()
            end_date_s = end_date_s or end.isoformat()
        else:
            start_date_s = start_date_s or default_start.isoformat()
            end_date_s = end_date_s or default_end.isoformat()
    if not start_date_s:
        start_date_s = default_start.isoformat()
    if not end_date_s:
        end_date_s = default_end.isoformat()

    is_valid, error_msg = validate_date_range(start_date_s, end_date_s)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    try:
        from bigas.resources.marketing.reddit_ads_service import RedditAdsService, RedditApiError
        from bigas.resources.marketing.storage_service import StorageService

        svc = RedditAdsService()
        start_d = date.fromisoformat(start_date_s)
        end_d = date.fromisoformat(end_date_s)
        cfg = REDDIT_AUDIENCE_REPORT_TYPES[report_type]
        campaign_id = (data.get("campaign_id") or "").strip() or None
        result = svc.get_audience_report(
            account_id=account_id,
            start_date=start_d,
            end_date=end_d,
            breakdowns=cfg["breakdowns"],
            fields=cfg.get("fields"),
            campaign_id=campaign_id,
        )
        data_rows = result.get("data") or []
        raw_response = result.get("raw_response") or {}
        if not isinstance(data_rows, list):
            data_rows = []

        store_raw = bool(data.get("store_raw", False))
        storage_path = None
        if store_raw and data_rows:
            storage = StorageService()
            blob_name = f"raw_ads/reddit/audience/{end_date_s}/{report_type}_{account_id.replace(' ', '_')}.json"
            storage.store_json(blob_name, {
                "request": {"account_id": account_id, "report_type": report_type, "start_date": start_date_s, "end_date": end_date_s},
                "raw_response": raw_response,
                "data": data_rows,
            })
            storage_path = blob_name

        out = {
            "status": "success",
            "account_id": account_id,
            "report_type": report_type,
            "date_range": {"start_date": start_date_s, "end_date": end_date_s},
            "breakdowns": cfg["breakdowns"],
            "rows_count": len(data_rows),
            "data": data_rows,
            "storage_path": storage_path,
        }
        if data.get("include_raw_response"):
            out["raw_response"] = raw_response
        return jsonify(out)
    except RedditApiError as e:
        logger.error("Reddit API error in fetch_reddit_audience_report: %s", e.response_text)
        out = {"error": sanitize_error_message(str(e))}
        if e.response_text:
            out["reddit_response"] = sanitize_error_message(e.response_text[:2000])
        return jsonify(out), 500
    except Exception as e:
        logger.error(f"Error in fetch_reddit_audience_report: {traceback.format_exc()}")
        return jsonify({"error": sanitize_error_message(str(e))}), 500


@marketing_bp.route('/mcp/tools/list_linkedin_creatives_for_period', methods=['POST'])
def list_linkedin_creatives_for_period():
    """
    List LinkedIn creatives (ads) that had activity in a given discovery period.

    This is intended as a discovery step for scheduled jobs (e.g. Cloud Scheduler),
    so you don't have to hard-code creative IDs.

    Request JSON:
      - account_urn: urn:li:sponsoredAccount:XXXX (required if not set in env)
      - discovery_start_date / discovery_end_date: YYYY-MM-DD (explicit period)
      - discovery_relative_range: optional alternative to explicit dates, one of:
          * LAST_7_DAYS
          * LAST_30_DAYS
          * LAST_90_DAYS
        If explicit dates are provided, they take precedence.
      - min_impressions: int (default: 1). Only creatives with at least this many
        impressions in the discovery window are returned.
      - store_raw: bool (default: true)
      - force_refresh: bool (default: false)

    Response JSON:
      - status: "success"
      - account_urn
      - date_range
      - creatives: list of { creative_id, creative_urn, impressions, clicks, costInLocalCurrency }
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    today = datetime.utcnow().date()

    account_urn = (data.get("account_urn") or os.environ.get("LINKEDIN_AD_ACCOUNT_URN") or "").strip()
    if not account_urn:
        return jsonify({"error": "account_urn is required (or set LINKEDIN_AD_ACCOUNT_URN)."}), 400
    account_urn = account_urn.strip()
    if account_urn.isdigit():
        account_urn = f"urn:li:sponsoredAccount:{account_urn}"

    # Discovery period: explicit dates win, otherwise discovery_relative_range.
    disc_start_s = (data.get("discovery_start_date") or "").strip()
    disc_end_s = (data.get("discovery_end_date") or "").strip()
    disc_rel = (data.get("discovery_relative_range") or "").strip().upper()

    if not disc_start_s or not disc_end_s:
        if disc_rel:
            if disc_rel == "LAST_7_DAYS":
                end = today - timedelta(days=1)
                start = end - timedelta(days=6)
            elif disc_rel == "LAST_30_DAYS":
                end = today - timedelta(days=1)
                start = end - timedelta(days=29)
            elif disc_rel == "LAST_90_DAYS":
                end = today - timedelta(days=1)
                start = end - timedelta(days=89)
            else:
                return jsonify(
                    {"error": "discovery_relative_range must be one of: LAST_7_DAYS, LAST_30_DAYS, LAST_90_DAYS"}
                ), 400

            if not disc_start_s:
                disc_start_s = start.isoformat()
            if not disc_end_s:
                disc_end_s = end.isoformat()
        else:
            # Default discovery window: last 30 days ending yesterday
            end = today - timedelta(days=1)
            start = end - timedelta(days=29)
            if not disc_start_s:
                disc_start_s = start.isoformat()
            if not disc_end_s:
                disc_end_s = end.isoformat()

    is_valid, error_msg = validate_date_range(disc_start_s, disc_end_s)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    min_impr = int(data.get("min_impressions") or 1)
    store_raw = data.get("store_raw", True)
    force_refresh = bool(data.get("force_refresh", False))

    try:
        from bigas.resources.marketing.linkedin_ads_service import LinkedInAdsService
        from bigas.resources.marketing.storage_service import StorageService

        svc = LinkedInAdsService()
        storage = StorageService()

        start_d = date.fromisoformat(disc_start_s)
        end_d = date.fromisoformat(disc_end_s)

        # Fields for creative rollup (fixed set)
        required_fields = {"dateRange", "pivotValues", "impressions", "clicks", "costInLocalCurrency"}
        final_fields = sorted(required_fields)

        linkedin_version = os.environ.get("LINKEDIN_VERSION") or "202601"
        safe_account = account_urn.split(":")[-1]

        request_signature = {
            "platform": "linkedin",
            "endpoint": "adAnalytics",
            "finder": "analytics",
            "account_urns": [account_urn],
            "start_date": disc_start_s,
            "end_date": disc_end_s,
            "time_granularity": "ALL",
            "pivot": "CREATIVE",
            "pivots": None,
            "campaign_urns": None,
            "campaign_group_urns": None,
            "creative_urns": None,
            "fields": final_fields,
            "version": linkedin_version,
            "include_entity_names": False,
        }
        signature_json = json.dumps(request_signature, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        request_hash = hashlib.sha256(signature_json.encode("utf-8")).hexdigest()
        hash_prefix = request_hash[:12]
        base_name = f"creative_rollup_{safe_account}"
        blob_name = f"raw_ads/linkedin/{disc_end_s}/{base_name}_{hash_prefix}.json"

        raw = None
        from_cache = False

        if store_raw and not force_refresh and storage.blob_exists(blob_name):
            cached = storage.get_json(blob_name) or {}
            payload = cached.get("payload") if isinstance(cached, dict) else {}
            raw = payload.get("response") if isinstance(payload, dict) else {}
            from_cache = True
        else:
            raw = svc.ad_analytics(
                start_date=start_d,
                end_date=end_d,
                time_granularity="ALL",
                pivot="CREATIVE",
                account_urns=[account_urn],
                campaign_urns=None,
                campaign_group_urns=None,
                creative_urns=None,
                fields=final_fields,
            )
            if store_raw:
                storage.store_raw_ads_report_at_blob(
                    platform="linkedin",
                    blob_name=blob_name,
                    report_data={
                        "request": request_signature,
                        "response": raw,
                    },
                    report_date=disc_end_s,
                    metadata={"request_hash": request_hash},
                )

        elements = raw.get("elements", []) if isinstance(raw, dict) else []

        creatives_out = []
        for el in elements:
            if not isinstance(el, dict):
                continue
            pivot_vals = el.get("pivotValues") or []
            if not pivot_vals:
                continue
            creative_urn = str(pivot_vals[0])
            if not creative_urn.startswith("urn:li:sponsoredCreative:"):
                continue

            metrics = el
            impr = metrics.get("impressions") or 0
            clicks = metrics.get("clicks") or 0
            cost = metrics.get("costInLocalCurrency")
            try:
                impr_i = int(impr)
            except Exception:
                impr_i = 0

            if impr_i < min_impr:
                continue

            try:
                clicks_i = int(clicks)
            except Exception:
                clicks_i = 0
            try:
                cost_d = Decimal(str(cost)) if cost is not None else Decimal("0")
            except Exception:
                cost_d = Decimal("0")

            creative_id = creative_urn.split(":")[-1]

            creatives_out.append(
                {
                    "creative_id": creative_id,
                    "creative_urn": creative_urn,
                    "impressions": impr_i,
                    "clicks": clicks_i,
                    "costInLocalCurrency": float(cost_d),
                }
            )

        # Sort creatives by impressions descending for convenience
        creatives_out.sort(key=lambda c: c["impressions"], reverse=True)

        return jsonify(
            {
                "status": "success",
                "account_urn": account_urn,
                "date_range": {"start_date": disc_start_s, "end_date": disc_end_s},
                "from_cache": from_cache,
                "creatives": creatives_out,
            }
        )
    except Exception:
        logger.error("Error in list_linkedin_creatives_for_period: %s", traceback.format_exc())
        sanitized_error = sanitize_error_message(str(traceback.format_exc()))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/fetch_linkedin_creative_demographics_portfolio', methods=['POST'])
def fetch_linkedin_creative_demographics_portfolio():
    """
    Fetch LinkedIn demographic adAnalytics per creative and per dimension (pivot),
    with rate-limit-friendly sequencing and strong caching in GCS.

    This is intended for scheduled runs (e.g. Cloud Scheduler) to prepare
    per-ad, per-dimension data that can later be summarized into a portfolio
    report by another endpoint.

    Request JSON:
      - account_urn: urn:li:sponsoredAccount:XXXX (required if not set in env)
      - start_date / end_date OR relative_range (same semantics as fetch_linkedin_ad_analytics_report)
      - creative_ids: list of numeric creative ids (required)
      - pivots: list of demographic pivots, e.g. ["MEMBER_JOB_TITLE","MEMBER_JOB_FUNCTION"]
      - fields: optional list of fields (defaults similar to fetch_linkedin_ad_analytics_report)
      - store_raw: bool (default: true)
      - force_refresh: bool (default: false)
      - include_entity_names: bool (default: true)
      - max_creatives_per_run: int (default: 10)
      - max_pivots_per_creative: int (default: 3)
      - sleep_ms_between_calls: int (default: 300)

    Returns:
      - For each (creative, pivot), whether it was fetched or came from cache,
        plus the storage paths for raw and enriched blobs (if created).
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    # Date handling: same semantics as fetch_linkedin_ad_analytics_report
    today = datetime.utcnow().date()
    default_end = today
    default_start = default_end - timedelta(days=7)

    account_urn = (data.get("account_urn") or os.environ.get("LINKEDIN_AD_ACCOUNT_URN") or "").strip()

    relative_range_raw = (data.get("relative_range") or "").strip().upper()
    start_date_s = (data.get("start_date") or "").strip()
    end_date_s = (data.get("end_date") or "").strip()

    if not start_date_s or not end_date_s:
        if relative_range_raw:
            if relative_range_raw == "LAST_DAY":
                end = today - timedelta(days=1)
                start = end
            elif relative_range_raw == "LAST_7_DAYS":
                end = today - timedelta(days=1)
                start = end - timedelta(days=6)
            elif relative_range_raw == "LAST_30_DAYS":
                end = today - timedelta(days=1)
                start = end - timedelta(days=29)
            else:
                return jsonify({"error": "relative_range must be one of: LAST_DAY, LAST_7_DAYS, LAST_30_DAYS"}), 400

            if not start_date_s:
                start_date_s = start.isoformat()
            if not end_date_s:
                end_date_s = end.isoformat()

    if not start_date_s:
        start_date_s = default_start.isoformat()
    if not end_date_s:
        end_date_s = default_end.isoformat()

    time_granularity = "ALL"
    pivots = data.get("pivots") or []
    if not isinstance(pivots, list) or not pivots:
        return jsonify({"error": "pivots is required and must be a non-empty list"}), 400
    creative_ids = data.get("creative_ids") or []
    if not isinstance(creative_ids, list) or not creative_ids:
        return jsonify({"error": "creative_ids is required and must be a non-empty list"}), 400

    fields = data.get("fields")
    if fields is not None and not isinstance(fields, list):
        return jsonify({"error": "fields must be a list of field names"}), 400

    store_raw = data.get("store_raw", True)
    force_refresh = bool(data.get("force_refresh", False))
    include_entity_names = bool(data.get("include_entity_names", True))

    max_creatives_per_run = int(data.get("max_creatives_per_run") or 10)
    max_pivots_per_creative = int(data.get("max_pivots_per_creative") or 3)
    sleep_ms_between_calls = int(data.get("sleep_ms_between_calls") or 300)

    is_valid, error_msg = validate_date_range(start_date_s, end_date_s)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    if not account_urn:
        return jsonify({"error": "account_urn is required (or set LINKEDIN_AD_ACCOUNT_URN)."}), 400

    account_urn = account_urn.strip()
    if account_urn.isdigit():
        account_urn = f"urn:li:sponsoredAccount:{account_urn}"

    try:
        from bigas.resources.marketing.linkedin_ads_service import LinkedInAdsService
        from bigas.resources.marketing.storage_service import StorageService

        svc = LinkedInAdsService()
        storage = StorageService()
        start_d = date.fromisoformat(start_date_s)
        end_d = date.fromisoformat(end_date_s)

        cleaned_fields = None
        if fields:
            cleaned_fields = sorted({str(f).strip() for f in fields if str(f).strip()})

        linkedin_version = os.environ.get("LINKEDIN_VERSION") or "202601"

        # Request metrics so the summarizer has impressions/clicks/cost per segment (required for portfolio insights).
        required_fields = {"dateRange", "pivotValues", "impressions", "clicks", "costInLocalCurrency"}
        final_fields = sorted(set((cleaned_fields or [])) | required_fields) if (cleaned_fields is not None) else sorted(required_fields)

        safe_account = account_urn.split(":")[-1]

        results = []

        limited_creatives = creative_ids[:max_creatives_per_run]
        limited_pivots = [str(p).strip().upper() for p in pivots if str(p).strip()][:max_pivots_per_creative]

        total_calls = len(limited_creatives) * len(limited_pivots)
        call_index = 0

        for cid in limited_creatives:
            cid_str = str(cid).strip()
            if not cid_str:
                continue
            creative_urn = f"urn:li:sponsoredCreative:{cid_str}"

            for pivot_name in limited_pivots:
                call_index += 1

                request_signature = {
                    "platform": "linkedin",
                    "endpoint": "adAnalytics",
                    "finder": "analytics",
                    "account_urns": [account_urn],
                    "start_date": start_date_s,
                    "end_date": end_date_s,
                    "time_granularity": time_granularity,
                    "pivot": pivot_name,
                    "pivots": None,
                    "campaign_urns": None,
                    "campaign_group_urns": None,
                    "creative_urns": [creative_urn],
                    "fields": final_fields,
                    "version": linkedin_version,
                    "include_entity_names": include_entity_names,
                }
                signature_json = json.dumps(request_signature, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                request_hash = hashlib.sha256(signature_json.encode("utf-8")).hexdigest()
                hash_prefix = request_hash[:12]
                base_name = f"ad_analytics_{safe_account}_{pivot_name}_{cid_str}"
                blob_name = f"raw_ads/linkedin/{end_date_s}/{base_name}_{hash_prefix}.json"
                enriched_blob_name = f"raw_ads/linkedin/{end_date_s}/{base_name}_{hash_prefix}.enriched.json"

                from_cache = False
                elements_count = None

                if store_raw and not force_refresh and storage.blob_exists(blob_name):
                    cached = storage.get_json(blob_name) or {}
                    cached_payload = cached.get("payload") if isinstance(cached, dict) else {}
                    cached_response = cached_payload.get("response") if isinstance(cached_payload, dict) else {}
                    elements = cached_response.get("elements", []) if isinstance(cached_response, dict) else []
                    elements_count = len(elements) if isinstance(elements, list) else None
                    from_cache = True
                    logger.info(
                        "LinkedIn portfolio: cache hit for creative=%s pivot=%s (elements=%s)",
                        cid_str,
                        pivot_name,
                        elements_count,
                    )
                    # Ensure enriched blob exists when we return enriched_storage_path (summarizer needs it).
                    if include_entity_names and not storage.blob_exists(enriched_blob_name):
                        try:
                            safe_account_id = None
                            try:
                                safe_account_id = int(safe_account)
                            except Exception:
                                safe_account_id = None
                            enriched = _enrich_linkedin_adanalytics_response(
                                cached_response,
                                account_id=safe_account_id,
                                svc=svc,
                                context={"creative_urns": [creative_urn]},
                            )
                            storage.store_json(
                                blob_name=enriched_blob_name,
                                data={
                                    "metadata": {
                                        "platform": "linkedin",
                                        "report_date": end_date_s,
                                        "report_type": "raw_ads_enriched",
                                        "stored_at": datetime.utcnow().isoformat(),
                                        "version": "1.0",
                                        "request_hash": request_hash,
                                    },
                                    "payload": {
                                        "request_hash": request_hash,
                                        "source_blob": blob_name,
                                        "enriched_response": enriched,
                                    },
                                },
                            )
                            logger.info(
                                "LinkedIn portfolio: created missing enriched blob for creative=%s pivot=%s",
                                cid_str,
                                pivot_name,
                            )
                        except Exception:
                            logger.warning(
                                "LinkedIn portfolio enrichment (on cache hit) failed for creative=%s pivot=%s: %s",
                                cid_str,
                                pivot_name,
                                traceback.format_exc(),
                            )
                else:
                    raw = svc.ad_analytics(
                        start_date=start_d,
                        end_date=end_d,
                        time_granularity=time_granularity,
                        pivot=pivot_name,
                        account_urns=[account_urn],
                        campaign_urns=None,
                        campaign_group_urns=None,
                        creative_urns=[creative_urn],
                        fields=final_fields,
                    )
                    elements = raw.get("elements", []) if isinstance(raw, dict) else []
                    elements_count = len(elements) if isinstance(elements, list) else None

                    if store_raw:
                        storage.store_raw_ads_report_at_blob(
                            platform="linkedin",
                            blob_name=blob_name,
                            report_data={
                                "request": request_signature,
                                "response": raw,
                            },
                            report_date=end_date_s,
                            metadata={"request_hash": request_hash},
                        )

                        if include_entity_names:
                            try:
                                safe_account_id = None
                                try:
                                    safe_account_id = int(safe_account)
                                except Exception:
                                    safe_account_id = None

                                enriched = _enrich_linkedin_adanalytics_response(
                                    raw,
                                    account_id=safe_account_id,
                                    svc=svc,
                                    context={"creative_urns": [creative_urn]},
                                )
                                storage.store_json(
                                    blob_name=enriched_blob_name,
                                    data={
                                        "metadata": {
                                            "platform": "linkedin",
                                            "report_date": end_date_s,
                                            "report_type": "raw_ads_enriched",
                                            "stored_at": datetime.utcnow().isoformat(),
                                            "version": "1.0",
                                            "request_hash": request_hash,
                                        },
                                        "payload": {
                                            "request_hash": request_hash,
                                            "source_blob": blob_name,
                                            "enriched_response": enriched,
                                        },
                                    },
                                )
                            except Exception:
                                logger.warning(
                                    "LinkedIn portfolio enrichment failed for creative=%s pivot=%s: %s",
                                    cid_str,
                                    pivot_name,
                                    traceback.format_exc(),
                                )

                    # Gentle delay between LinkedIn calls
                    if sleep_ms_between_calls > 0:
                        time.sleep(sleep_ms_between_calls / 1000.0)

                results.append(
                    {
                        "creative_id": cid_str,
                        "pivot": pivot_name,
                        "request_hash": request_hash,
                        "from_cache": from_cache,
                        "elements_count": elements_count,
                        "storage_path": (blob_name if store_raw else None),
                        "enriched_storage_path": (
                            enriched_blob_name if (store_raw and include_entity_names) else None
                        ),
                    }
                )

        return jsonify(
            {
                "status": "success",
                "account_urn": account_urn,
                "date_range": {"start_date": start_date_s, "end_date": end_date_s},
                "total_calls": call_index,
                "results": results,
            }
        )
    except Exception:
        logger.error("Error in fetch_linkedin_creative_demographics_portfolio: %s", traceback.format_exc())
        sanitized_error = sanitize_error_message(str(traceback.format_exc()))
        return jsonify({"error": sanitized_error}), 500


def _enrich_linkedin_adanalytics_response(
    raw: Any,
    *,
    account_id: Optional[int],
    svc: Any,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Best-effort enrichment of adAnalytics response.

    Adds:
    - Stable structure: {dateRange, pivotValues, metrics}
    - Best-effort URN resolution for common pivotValues:
      - sponsoredCreative -> creative name (via /rest .../creatives) if available
      - title/function/industry/seniority -> localized name (via /v2 standardized-data endpoints)
    """
    if not isinstance(raw, dict):
        return {"elements": []}

    elements = raw.get("elements", [])
    if not isinstance(elements, list):
        return {"elements": []}

    # Local caches to keep enrichment fast.
    creative_cache: Dict[str, Any] = {}
    urn_cache: Dict[str, Any] = {}

    def _resolve_urn(u: str) -> Optional[Dict[str, Any]]:
        u = (u or "").strip()
        if not u:
            return None
        if u in urn_cache:
            return urn_cache[u]
        try:
            if u.startswith("urn:li:geo:"):
                geo_id = u.split(":")[-1]
                urn_cache[u] = svc.get_geo(geo_id)
                return urn_cache[u]
            if u.startswith("urn:li:title:"):
                title_id = u.split(":")[-1]
                urn_cache[u] = svc.get_title(title_id)
                return urn_cache[u]
            if u.startswith("urn:li:function:"):
                fid = u.split(":")[-1]
                urn_cache[u] = svc.get_function(fid)
                return urn_cache[u]
            if u.startswith("urn:li:industry:"):
                iid = u.split(":")[-1]
                urn_cache[u] = svc.get_industry(iid)
                return urn_cache[u]
            if u.startswith("urn:li:seniority:"):
                sid = u.split(":")[-1]
                urn_cache[u] = svc.get_seniority(sid)
                return urn_cache[u]
        except Exception:
            urn_cache[u] = None
            return None
        urn_cache[u] = None
        return None

    def _resolve_creative(creative_urn: str) -> Optional[Dict[str, Any]]:
        creative_urn = (creative_urn or "").strip()
        if not creative_urn or not creative_urn.startswith("urn:li:sponsoredCreative:"):
            return None
        if creative_urn in creative_cache:
            return creative_cache[creative_urn]
        if not account_id:
            creative_cache[creative_urn] = None
            return None
        try:
            creative_cache[creative_urn] = svc.get_creative(ad_account_id=account_id, creative_urn=creative_urn)
            return creative_cache[creative_urn]
        except Exception:
            creative_cache[creative_urn] = None
            return None

    out: Dict[str, Any] = {
        "account_id": account_id,
        "context": {},
        "elements": [],
        "paging": raw.get("paging"),
    }

    # Add explicit context about filters, so demographic pivots can still be attributed to a creative/campaign.
    ctx = context or {}
    creative_urns_ctx = ctx.get("creative_urns") if isinstance(ctx, dict) else None
    if isinstance(creative_urns_ctx, list) and creative_urns_ctx:
        resolved_creatives = []
        for cu in creative_urns_ctx:
            cu_s = str(cu).strip()
            if not cu_s:
                continue
            c = _resolve_creative(cu_s)
            if isinstance(c, dict):
                resolved_creatives.append(
                    {
                        "urn": cu_s,
                        "name": c.get("name"),
                        "campaign": c.get("campaign"),
                        "content": c.get("content"),
                    }
                )
            else:
                resolved_creatives.append({"urn": cu_s})
        out["context"]["creative_urns"] = creative_urns_ctx
        out["context"]["creatives"] = resolved_creatives

    for el in elements:
        if not isinstance(el, dict):
            continue

        pivot_values = el.get("pivotValues") if isinstance(el.get("pivotValues"), list) else []
        resolved = []
        for pv in pivot_values:
            pv_s = str(pv)
            item: Dict[str, Any] = {"urn": pv_s}

            if pv_s.startswith("urn:li:sponsoredCreative:"):
                c = _resolve_creative(pv_s)
                if isinstance(c, dict):
                    item["type"] = "creative"
                    item["name"] = c.get("name")
                    item["campaign"] = c.get("campaign")
                    item["content"] = c.get("content")
                else:
                    item["type"] = "creative"
            else:
                info = _resolve_urn(pv_s)
                if isinstance(info, dict):
                    item["type"] = "standardized"
                    # common shapes:
                    # - {"name":{"localized":{"en_US":"..."}}}
                    # - geo: {"defaultLocalizedName":{"value":"United States"}}
                    name = None
                    if isinstance(info.get("name"), dict):
                        name = (((info.get("name") or {}).get("localized") or {}).get("en_US"))
                    if not name and isinstance(info.get("defaultLocalizedName"), dict):
                        name = (info.get("defaultLocalizedName") or {}).get("value")
                    item["name"] = name
                    item["raw"] = info

            resolved.append(item)

        def _to_decimal(v: Any) -> Optional[Decimal]:
            if v is None:
                return None
            if isinstance(v, (int, float)):
                try:
                    return Decimal(str(v))
                except Exception:
                    return None
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return None
                try:
                    return Decimal(s)
                except InvalidOperation:
                    return None
            return None

        metrics = {k: v for k, v in el.items() if k not in {"dateRange", "pivotValues"}}
        impressions = metrics.get("impressions")
        clicks = metrics.get("clicks")
        cost_local = metrics.get("costInLocalCurrency")

        impressions_i = int(impressions) if isinstance(impressions, int) else None
        clicks_i = int(clicks) if isinstance(clicks, int) else None
        cost_d = _to_decimal(cost_local)

        derived: Dict[str, Any] = {}
        if impressions_i is not None and impressions_i > 0 and clicks_i is not None:
            derived["ctr"] = float(Decimal(clicks_i) / Decimal(impressions_i))
        if clicks_i is not None and clicks_i > 0 and cost_d is not None:
            derived["avg_cpc_local"] = float(cost_d / Decimal(clicks_i))

        out["elements"].append(
            {
                "dateRange": el.get("dateRange"),
                "pivotValues": pivot_values,
                "pivotValuesResolved": resolved,
                "metrics": metrics,
                "derived": derived,
            }
        )

    # Add report-level totals and per-row shares (for UI-like % columns).
    total_impr = 0
    total_clicks = 0
    total_cost: Optional[Decimal] = None
    for row in out["elements"]:
        m = row.get("metrics") if isinstance(row, dict) else None
        if not isinstance(m, dict):
            continue
        if isinstance(m.get("impressions"), int):
            total_impr += int(m["impressions"])
        if isinstance(m.get("clicks"), int):
            total_clicks += int(m["clicks"])
        c = m.get("costInLocalCurrency")
        cd = None
        try:
            cd = Decimal(str(c)) if c is not None else None
        except Exception:
            cd = None
        if cd is not None:
            total_cost = (total_cost or Decimal("0")) + cd

    out["summary"] = {
        "rows": len(out["elements"]),
        "totals": {
            "impressions": total_impr,
            "clicks": total_clicks,
            "costInLocalCurrency": (str(total_cost) if total_cost is not None else None),
        },
    }

    for row in out["elements"]:
        m = row.get("metrics") if isinstance(row, dict) else None
        if not isinstance(m, dict):
            continue
        shares: Dict[str, Any] = {}
        if total_impr > 0 and isinstance(m.get("impressions"), int):
            shares["impressions_share"] = float(Decimal(int(m["impressions"])) / Decimal(total_impr))
        if total_clicks > 0 and isinstance(m.get("clicks"), int):
            shares["clicks_share"] = float(Decimal(int(m["clicks"])) / Decimal(total_clicks))
        if total_cost is not None:
            try:
                cd = Decimal(str(m.get("costInLocalCurrency"))) if m.get("costInLocalCurrency") is not None else None
            except Exception:
                cd = None
            if cd is not None and total_cost != 0:
                shares["cost_share_local"] = float(cd / total_cost)
        row["shares"] = shares

    return out


@marketing_bp.route('/mcp/tools/summarize_linkedin_ad_analytics', methods=['POST'])
def summarize_linkedin_ad_analytics():
    """
    Generate an AI-written LinkedIn ads performance summary and post it to Discord.

    This endpoint is designed to be triggered from e.g. Google Cloud Scheduler
    after a raw/enriched report has been generated and stored in GCS.

    Request JSON:
      - enriched_storage_path: GCS path to the enriched LinkedIn report JSON
          (e.g. raw_ads/linkedin/2026-02-09/ad_analytics_516183054_MEMBER_JOB_TITLE_xxx.enriched.json)
      - llm_model: optional OpenAI model name (default: gpt-4)
      - discord_webhook_env: optional env var name for Discord webhook.
          Defaults to DISCORD_WEBHOOK_URL_MARKETING, then DISCORD_WEBHOOK_URL.

    Behaviour:
      - If the enriched report has no elements, a short "no data" message is posted to Discord.
      - Otherwise, the endpoint sends a compact version of the report to OpenAI and
        posts the resulting analysis to Discord.
    """
    data = request.json or {}
    enriched_path = (data.get("enriched_storage_path") or "").strip()
    if not enriched_path:
        return jsonify({"error": "enriched_storage_path is required"}), 400

    if not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY is not configured on the server"}), 500

    # Resolve Discord webhook
    webhook_env = (data.get("discord_webhook_env") or "").strip() or "DISCORD_WEBHOOK_URL_MARKETING"
    webhook_url = os.environ.get(webhook_env) or os.environ.get("DISCORD_WEBHOOK_URL")

    try:
        from bigas.resources.marketing.storage_service import StorageService

        storage = StorageService()
        obj = storage.get_json(enriched_path)
        if not isinstance(obj, dict):
            return jsonify({"error": f"Enriched report at {enriched_path} is not a JSON object"}), 500

        payload = obj.get("payload") or {}
        enriched = payload.get("enriched_response") or {}
        if not isinstance(enriched, dict):
            return jsonify({"error": "enriched_response missing or invalid in enriched report"}), 500

        elements = enriched.get("elements") or []
        summary = enriched.get("summary") or {}
        context = enriched.get("context") or {}

        # If there is no data, post a simple no-data notice to Discord.
        if not elements:
            no_data_message = (
                "## 📊 LinkedIn Ads Report\n\n"
                "No LinkedIn ad data was available for the selected period.\n\n"
                f"Source: `{enriched_path}`"
            )
            if webhook_url:
                post_to_discord(webhook_url, no_data_message)
            return jsonify(
                {
                    "status": "success",
                    "had_data": False,
                    "discord_posted": bool(webhook_url),
                    "enriched_storage_path": enriched_path,
                }
            )

        # Build a compact analytics payload for the LLM.
        # To keep token usage reasonable, include:
        #   - summary (totals)
        #   - context (creative names etc.)
        #   - a compact sample of rows as examples (names + key metrics only)
        sample_limit = int(data.get("sample_limit") or 50)

        compact_rows = []
        for el in elements[:sample_limit]:
            if not isinstance(el, dict):
                continue
            pivots_resolved = el.get("pivotValuesResolved") or []
            seg_names = []
            for pv in pivots_resolved:
                if not isinstance(pv, dict):
                    continue
                name = pv.get("name") or pv.get("urn")
                if name:
                    seg_names.append(str(name))
            metrics = el.get("metrics") or {}
            derived = el.get("derived") or {}
            compact_rows.append(
                {
                    "segments": seg_names,
                    "metrics": {
                        "impressions": metrics.get("impressions"),
                        "clicks": metrics.get("clicks"),
                        "costInLocalCurrency": metrics.get("costInLocalCurrency"),
                    },
                    "derived": {
                        "ctr": derived.get("ctr"),
                        "avg_cpc_local": derived.get("avg_cpc_local"),
                    },
                }
            )

        analytics_payload = {
            "platform": "linkedin",
            "source_blob": enriched_path,
            "summary": summary,
            "context": context,
            "sample_rows": compact_rows,
        }

        # Use a smaller, more cost‑efficient model by default, and keep prompts compact.
        model = (data.get("llm_model") or "gpt-4.1-mini").strip()

        prompt_cfg = AD_SUMMARY_PROMPTS.get(("linkedin", "ad_analytics"))
        if not prompt_cfg:
            return jsonify({"error": "Prompt configuration missing for LinkedIn ad analytics"}), 500

        system_prompt = prompt_cfg["system"]
        user_prompt = prompt_cfg["user_template"].format(
            platform="linkedin",
            payload=json.dumps(analytics_payload, indent=2),
        )

        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        completion = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=900,
            temperature=0.4,
            timeout=40,
        )
        analysis_text = completion.choices[0].message.content.strip()

        discord_message = (
            "## 📊 LinkedIn Ads Performance Report\n\n"
            f"{analysis_text}\n\n"
            f"---\n_Source blob: `{enriched_path}`_"
        )

        if webhook_url:
            post_long_to_discord(webhook_url, discord_message)

        return jsonify(
            {
                "status": "success",
                "had_data": True,
                "discord_posted": bool(webhook_url),
                "enriched_storage_path": enriched_path,
                "used_model": model,
            }
        )
    except Exception as e:
        logger.error(f"Error in summarize_linkedin_ad_analytics: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/summarize_reddit_ad_analytics', methods=['POST'])
def summarize_reddit_ad_analytics():
    """
    Summarize a Reddit ads report from an enriched GCS blob. Option A: currencies as-reported.

    Request JSON:
      - enriched_storage_path: GCS path to enriched blob (required)
      - webhook_url or use DISCORD_WEBHOOK_URL_MARKETING
      - llm_model: optional (default: gpt-4.1-mini)
      - sample_limit: optional max rows to send to LLM (default: 50)
    """
    data = request.json or {}
    enriched_path = (data.get("enriched_storage_path") or "").strip()
    if not enriched_path:
        return jsonify({"error": "enriched_storage_path is required"}), 400
    if not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY is not configured on the server"}), 500

    webhook_url = (
        (data.get("webhook_url") or "").strip()
        or os.environ.get("DISCORD_WEBHOOK_URL_MARKETING")
        or os.environ.get("DISCORD_WEBHOOK_URL")
    )

    try:
        from bigas.resources.marketing.storage_service import StorageService

        storage = StorageService()
        obj = storage.get_json(enriched_path)
        if not obj or not isinstance(obj, dict):
            return jsonify({"error": f"Enriched report not found or invalid: {enriched_path}"}), 404

        payload = obj.get("payload") or {}
        enriched = payload.get("enriched_response") or {}
        if not isinstance(enriched, dict):
            return jsonify({"error": "enriched_response missing or invalid in enriched report"}), 500

        elements = enriched.get("elements") or []
        summary = enriched.get("summary") or {}
        context = enriched.get("context") or {}

        def _to_int(value: Any, default: int = 0) -> int:
            try:
                if value is None:
                    return default
                return int(value)
            except (TypeError, ValueError):
                return default

        def _to_float(value: Any, default: float = 0.0) -> float:
            try:
                if value is None:
                    return default
                return float(value)
            except (TypeError, ValueError):
                return default

        calc_impressions = 0
        calc_clicks = 0
        calc_spend = 0.0
        for el in elements:
            if not isinstance(el, dict):
                continue
            metrics = el.get("metrics") or {}
            if not isinstance(metrics, dict):
                continue
            calc_impressions += _to_int(metrics.get("impressions"), 0)
            calc_clicks += _to_int(metrics.get("clicks"), 0)
            calc_spend += _to_float(metrics.get("spend"), 0.0)

        total_impressions = _to_int(summary.get("total_impressions"), calc_impressions)
        total_clicks = _to_int(summary.get("total_clicks"), calc_clicks)
        total_spend = _to_float(summary.get("total_spend"), calc_spend)
        total_ctr_pct_val = summary.get("total_ctr_pct")
        if total_ctr_pct_val is None:
            total_ctr_pct = round((100.0 * total_clicks / total_impressions), 2) if total_impressions > 0 else None
        else:
            try:
                total_ctr_pct = round(float(total_ctr_pct_val), 2)
            except (TypeError, ValueError):
                total_ctr_pct = round((100.0 * total_clicks / total_impressions), 2) if total_impressions > 0 else None

        total_cpc_val = summary.get("total_cpc")
        if total_cpc_val is None:
            total_cpc = round((total_spend / total_clicks), 2) if total_clicks > 0 else None
        else:
            try:
                total_cpc = round(float(total_cpc_val), 2)
            except (TypeError, ValueError):
                total_cpc = round((total_spend / total_clicks), 2) if total_clicks > 0 else None

        spend_currency = (
            (context.get("spend_currency") or summary.get("total_spend_currency") or "EUR")
            if isinstance(context, dict)
            else (summary.get("total_spend_currency") or "EUR")
        )
        if isinstance(spend_currency, str):
            spend_currency = spend_currency.strip().upper() or "EUR"
        else:
            spend_currency = "EUR"

        metrics_summary = {
            "impressions": total_impressions,
            "clicks": total_clicks,
            "spend": round(total_spend, 2),
            "spend_currency": spend_currency,
            "ctr_pct": total_ctr_pct,
            "cpc": total_cpc,
            "rows_count": len(elements),
        }

        if not elements:
            no_data_message = (
                "## 📊 Reddit Ads Report\n\n"
                "No Reddit ad data was available for the selected period.\n\n"
                f"Source: `{enriched_path}`"
            )
            if webhook_url:
                post_to_discord(webhook_url, no_data_message)
            return jsonify(
                {
                    "status": "success",
                    "had_data": False,
                    "discord_posted": bool(webhook_url),
                    "enriched_storage_path": enriched_path,
                    "metrics_summary": {
                        "impressions": 0,
                        "clicks": 0,
                        "spend": 0.0,
                        "spend_currency": spend_currency,
                        "ctr_pct": None,
                        "cpc": None,
                        "rows_count": 0,
                    },
                }
            )

        sample_limit = int(data.get("sample_limit") or 50)
        compact_rows = []
        for el in elements[:sample_limit]:
            if not isinstance(el, dict):
                continue
            compact_rows.append({
                "segments": el.get("segments") or [],
                "metrics": el.get("metrics") or {},
                "derived": el.get("derived") or {},
            })

        analytics_payload = {
            "platform": "reddit",
            "source_blob": enriched_path,
            "summary": summary,
            "context": context,
            "sample_rows": compact_rows,
            "note": "Spend in context.spend_currency; do not sum across platforms/currencies.",
        }

        model = (data.get("llm_model") or "gpt-4.1-mini").strip()
        prompt_cfg = AD_SUMMARY_PROMPTS.get(("reddit", "ad_analytics"))
        if not prompt_cfg:
            return jsonify({"error": "Prompt configuration missing for Reddit ad analytics"}), 500

        system_prompt = prompt_cfg["system"]
        user_prompt = prompt_cfg["user_template"].format(
            platform="reddit",
            payload=json.dumps(analytics_payload, indent=2),
        )

        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        completion = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=900,
            temperature=0.4,
            timeout=40,
        )
        analysis_text = completion.choices[0].message.content.strip()

        discord_message = (
            "## 📊 Reddit Ads Performance Report\n\n"
            f"{analysis_text}\n\n"
            f"---\n_Source blob: `{enriched_path}`_"
        )

        if webhook_url:
            post_long_to_discord(webhook_url, discord_message)

        return jsonify(
            {
                "status": "success",
                "had_data": True,
                "discord_posted": bool(webhook_url),
                "enriched_storage_path": enriched_path,
                "used_model": model,
                "metrics_summary": metrics_summary,
            }
        )
    except Exception as e:
        logger.error(f"Error in summarize_reddit_ad_analytics: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/summarize_linkedin_creative_portfolio', methods=['POST'])
def summarize_linkedin_creative_portfolio():
    """
    Summarize a LinkedIn creative portfolio: per-ad top segments + overall recommendations.

    This expects enriched per-creative, per-pivot reports produced by
    fetch_linkedin_creative_demographics_portfolio.

    Request JSON:
      - items: list of objects, each with:
          * creative_id: string or int
          * pivot: e.g. MEMBER_JOB_TITLE
          * enriched_storage_path: GCS path to enriched blob
      - llm_model: optional OpenAI model name (default: gpt-4.1-mini)

    Behavior:
      - Aggregates per-creative totals and per-dimension top segments (Top 5 by CTR, with a
        small minimum impression threshold) from the provided enriched blobs.
      - If there is no data across all items, posts a "no data" message to Discord and
        skips OpenAI.
      - Otherwise sends a compact portfolio payload to OpenAI and posts the structured
        analysis to Discord.
    """
    data = request.json or {}
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"error": "items is required and must be a non-empty list"}), 400

    if not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY is not configured on the server"}), 500

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_MARKETING") or os.environ.get("DISCORD_WEBHOOK_URL")

    try:
        from bigas.resources.marketing.storage_service import StorageService

        storage = StorageService()

        # Aggregate: creatives -> dimensions -> segments
        # Structure: { creative_id: { 'name': ..., 'pivots': { pivot_name: { segment_name: metrics } } } }
        creatives: Dict[str, Dict[str, Any]] = {}

        total_impr_all = 0
        total_clicks_all = 0

        for itm in items:
            creative_id_raw = itm.get("creative_id")
            pivot = (itm.get("pivot") or "").strip().upper()
            enriched_path = (itm.get("enriched_storage_path") or "").strip()
            if not creative_id_raw or not pivot or not enriched_path:
                continue

            cid = str(creative_id_raw).strip()
            try:
                obj = storage.get_json(enriched_path) or {}
            except Exception:
                logger.warning("Portfolio summarizer: failed to read %s", enriched_path)
                continue

            payload = obj.get("payload") or {}
            enriched = payload.get("enriched_response") or {}
            elements = enriched.get("elements") or []
            context = enriched.get("context") or {}
            logger.info(
                "summarize_linkedin_creative_portfolio: read path=%s creative=%s pivot=%s elements=%s",
                enriched_path,
                cid,
                pivot,
                len(elements),
            )

            creatives.setdefault(cid, {"id": cid, "name": None, "context": context, "pivots": {}})

            # Try to get ad name from context.creatives if present
            if not creatives[cid].get("name"):
                ctx_creatives = context.get("creatives") or []
                for c in ctx_creatives:
                    if not isinstance(c, dict):
                        continue
                    urn = c.get("urn") or ""
                    if urn.endswith(f":{cid}"):
                        creatives[cid]["name"] = c.get("name")
                        break

            pivot_map = creatives[cid]["pivots"].setdefault(pivot, {})

            for el in elements:
                if not isinstance(el, dict):
                    continue
                metrics = el.get("metrics") or {}
                derived = el.get("derived") or {}
                piv_resolved = el.get("pivotValuesResolved") or []

                # Segment key: join resolved names or URNs
                seg_parts = []
                for pv in piv_resolved:
                    if not isinstance(pv, dict):
                        continue
                    seg_name = pv.get("name") or pv.get("urn")
                    if seg_name:
                        seg_parts.append(str(seg_name))
                if not seg_parts:
                    continue

                seg_key = " / ".join(seg_parts)
                impr = metrics.get("impressions") or 0
                clicks = metrics.get("clicks") or 0
                cost = metrics.get("costInLocalCurrency")

                try:
                    impr_i = int(impr)
                except Exception:
                    impr_i = 0
                try:
                    clicks_i = int(clicks)
                except Exception:
                    clicks_i = 0
                try:
                    cost_d = Decimal(str(cost)) if cost is not None else Decimal("0")
                except Exception:
                    cost_d = Decimal("0")

                agg = pivot_map.setdefault(
                    seg_key,
                    {"impressions": 0, "clicks": 0, "costInLocalCurrency": Decimal("0")},
                )
                agg["impressions"] += impr_i
                agg["clicks"] += clicks_i
                agg["costInLocalCurrency"] += cost_d

                total_impr_all += impr_i
                total_clicks_all += clicks_i

        logger.info(
            "summarize_linkedin_creative_portfolio: aggregated total_impr_all=%s total_clicks_all=%s creatives=%s",
            total_impr_all,
            total_clicks_all,
            len(creatives),
        )

        # No data across all items?
        if total_impr_all == 0 and total_clicks_all == 0:
            # Log first item structure to help troubleshoot (path + first element keys/metrics)
            first_path = (items[0].get("enriched_storage_path") or "").strip() if items else ""
            first_el_sample: Dict[str, Any] = {}
            if first_path and creatives:
                try:
                    obj0 = storage.get_json(first_path) or {}
                    els = (obj0.get("payload") or {}).get("enriched_response") or {}
                    el_list = els.get("elements") or []
                    if el_list and isinstance(el_list[0], dict):
                        first_el = el_list[0]
                        first_el_sample = {
                            "keys": list(first_el.keys()),
                            "metrics_keys": list((first_el.get("metrics") or {}).keys()),
                            "metrics_sample": first_el.get("metrics"),
                        }
                except Exception as e:
                    first_el_sample = {"read_error": str(e)}
            logger.warning(
                "summarize_linkedin_creative_portfolio: no impressions/clicks; first_path=%s first_element_sample=%s",
                first_path,
                first_el_sample,
            )
            no_data_message = (
                "## 📊 LinkedIn Creative Portfolio Report\n\n"
                "No LinkedIn ad data was available for the specified creatives and period.\n\n"
                "No OpenAI analysis was generated."
            )
            if webhook_url:
                post_to_discord(webhook_url, no_data_message)
            return jsonify(
                {
                    "status": "success",
                    "had_data": False,
                    "discord_posted": bool(webhook_url),
                }
            )

        # Build compact per-ad summaries with Top 5 segments per pivot.
        # Apply a small impression threshold to avoid noisy 1/1 rows.
        min_impressions = int(data.get("min_impressions") or 10)
        top_k = int(data.get("top_k") or 5)

        ads_payload = []
        for cid, info in creatives.items():
            pivots_payload = {}
            totals_impr = 0
            totals_clicks = 0
            totals_cost = Decimal("0")

            for pivot_name, segs in info["pivots"].items():
                rows = []
                for seg, m in segs.items():
                    impr = m.get("impressions") or 0
                    clicks = m.get("clicks") or 0
                    cost = m.get("costInLocalCurrency") or Decimal("0")
                    if impr < min_impressions:
                        continue
                    ctr = (Decimal(clicks) / Decimal(impr) * Decimal(100)) if impr else None
                    avg_cpc = (cost / Decimal(clicks)) if clicks else None
                    rows.append(
                        {
                            "segment": seg,
                            "impressions": impr,
                            "clicks": clicks,
                            "ctr_pct": float(ctr) if ctr is not None else None,
                            "cost_local": float(cost),
                            "avg_cpc_local": float(avg_cpc) if avg_cpc is not None else None,
                        }
                    )
                    totals_impr += impr
                    totals_clicks += clicks
                    totals_cost += cost

                # Sort by CTR descending, then impressions
                rows.sort(
                    key=lambda r: ((r["ctr_pct"] or 0.0), r["impressions"]),
                    reverse=True,
                )
                pivots_payload[pivot_name] = rows[:top_k]

            ctr_total = (Decimal(totals_clicks) / Decimal(totals_impr) * Decimal(100)) if totals_impr else None
            avg_cpc_total = (totals_cost / Decimal(totals_clicks)) if totals_clicks else None

            ads_payload.append(
                {
                    "creative_id": cid,
                    "name": info.get("name"),
                    "totals": {
                        "impressions": totals_impr,
                        "clicks": totals_clicks,
                        "ctr_pct": float(ctr_total) if ctr_total is not None else None,
                        "cost_local": float(totals_cost),
                        "avg_cpc_local": float(avg_cpc_total) if avg_cpc_total is not None else None,
                    },
                    "pivots": pivots_payload,
                }
            )

        analytics_payload = {
            "platform": "linkedin",
            "ads": ads_payload,
        }

        model = (data.get("llm_model") or "gpt-4.1-mini").strip()

        prompt_cfg = AD_SUMMARY_PROMPTS.get(("linkedin", "creative_portfolio"))
        if not prompt_cfg:
            return jsonify({"error": "Prompt configuration missing for LinkedIn creative portfolio"}), 500

        system_prompt = prompt_cfg["system"]
        user_prompt = prompt_cfg["user_template"].format(
            platform="linkedin",
            payload=json.dumps(analytics_payload, indent=2),
        )

        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        completion = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1100,
            temperature=0.4,
            timeout=40,
        )
        analysis_text = completion.choices[0].message.content.strip()

        discord_message = (
            "## 📊 LinkedIn Creative Portfolio Report\n\n"
            f"{analysis_text}\n\n"
            "---\n"
            "_This report is based on per-creative, per-dimension LinkedIn adAnalytics data._"
        )

        if webhook_url:
            post_long_to_discord(webhook_url, discord_message)

        return jsonify(
            {
                "status": "success",
                "had_data": True,
                "discord_posted": bool(webhook_url),
                "used_model": model,
                "ads_count": len(ads_payload),
            }
        )
    except Exception:
        logger.error("Error in summarize_linkedin_creative_portfolio: %s", traceback.format_exc())
        sanitized_error = sanitize_error_message(str(traceback.format_exc()))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/run_linkedin_portfolio_report_async', methods=['POST'])
def run_linkedin_portfolio_report_async():
    """
    Async entrypoint for LinkedIn portfolio report.
    Enqueues a background job and returns job metadata immediately.
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    timeout_seconds = int(data.get("timeout_seconds") or 300)
    timeout_seconds = max(10, min(timeout_seconds, 900))

    app_obj = current_app._get_current_object()
    access_header = app_obj.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
    request_key = (request.headers.get(access_header) or "").strip()
    if request_key:
        data["_internal_access_key"] = request_key
    job_id = _create_async_job(data, timeout_seconds=timeout_seconds)

    t = threading.Thread(
        target=_run_linkedin_portfolio_job,
        args=(app_obj, job_id, data),
        daemon=True,
    )
    t.start()

    return jsonify(
        {
            "status": "accepted",
            "job_id": job_id,
            "poll_after_seconds": 5,
            "timeout_seconds": timeout_seconds,
        }
    )


@marketing_bp.route('/mcp/tools/get_job_status', methods=['POST'])
def get_job_status():
    data = request.json or {}
    job_id = (data.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    job = _get_async_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404

    return jsonify(
        {
            "job_id": job["job_id"],
            "status": job["status"],
            "progress_pct": job.get("progress_pct", 0),
            "stage": job.get("stage", "unknown"),
            "updated_at": job.get("updated_at"),
            "error": job.get("error"),
            "result_available": bool(job.get("result")) and job.get("status") == "succeeded",
        }
    )


@marketing_bp.route('/mcp/tools/get_job_result', methods=['POST'])
def get_job_result():
    data = request.json or {}
    job_id = (data.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    job = _get_async_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404

    if job["status"] != "succeeded":
        return jsonify(
            {
                "job_id": job["job_id"],
                "status": job["status"],
                "error": job.get("error"),
                "result": job.get("result"),
            }
        )

    return jsonify(
        {
            "job_id": job["job_id"],
            "status": "succeeded",
            "result": job.get("result"),
        }
    )


@marketing_bp.route('/mcp/tools/run_linkedin_portfolio_report', methods=['POST'])
def run_linkedin_portfolio_report():
    """
    Run the full LinkedIn portfolio pipeline in one request: discover creatives for a period,
    fetch per-creative demographic data (job title, job function, country), then summarize
    with the creative-portfolio summarizer and post to Discord.

    Uses job title, function, and country pivots so the report includes segment insights.
    If no demographic data is available, falls back to CREATIVE-level ad analytics + ad-analytics summarizer.

    Request JSON:
      - account_urn: urn:li:sponsoredAccount:XXXX (required if not set in env)
      - discovery_relative_range: LAST_7_DAYS | LAST_30_DAYS | LAST_90_DAYS (default: LAST_30_DAYS). Or use discovery_start_date/end_date.
      - discovery_start_date / discovery_end_date: YYYY-MM-DD (optional; overrides relative_range)
      - min_impressions: only creatives with at least this many impressions in discovery (default: 1)
      - store_raw, force_refresh: same as list/fetch endpoints (defaults: true, false)
      - include_entity_names: for enriched responses (default: true)
      - max_creatives_per_run: max creatives to fetch demographics for (default: 10)
      - llm_model: passed to summarizer (default: gpt-4.1-mini)

    Response: status, creatives_discovered, had_data, discord_posted, used_model, report_type (portfolio | ad_analytics), and any error.
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    # For MCP clients with hard per-call limits (e.g. 30s), return immediately and poll.
    run_async = bool(data.get("async", False)) and not bool(data.get("_internal_async_worker", False))
    timeout_seconds = int(data.get("timeout_seconds") or 300)
    timeout_seconds = max(10, min(timeout_seconds, 900))
    if run_async:
        app_obj = current_app._get_current_object()
        access_header = app_obj.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
        request_key = (request.headers.get(access_header) or "").strip()
        if request_key:
            data["_internal_access_key"] = request_key
        job_id = _create_async_job(data, timeout_seconds=timeout_seconds)
        t = threading.Thread(
            target=_run_linkedin_portfolio_job,
            args=(app_obj, job_id, data),
            daemon=True,
        )
        t.start()
        return jsonify(
            {
                "status": "accepted",
                "job_id": job_id,
                "poll_after_seconds": 5,
                "timeout_seconds": timeout_seconds,
            }
        )

    account_urn = (data.get("account_urn") or os.environ.get("LINKEDIN_AD_ACCOUNT_URN") or "").strip()
    if not account_urn:
        return jsonify({"error": "account_urn is required (or set LINKEDIN_AD_ACCOUNT_URN)."}), 400
    if account_urn.isdigit():
        account_urn = f"urn:li:sponsoredAccount:{account_urn}"

    # 1) Discovery: list creatives for period (default LAST_30_DAYS)
    discovery_payload = {
        "account_urn": account_urn,
        "discovery_relative_range": data.get("discovery_relative_range") or "LAST_30_DAYS",
        "discovery_start_date": (data.get("discovery_start_date") or "").strip() or None,
        "discovery_end_date": (data.get("discovery_end_date") or "").strip() or None,
        "min_impressions": int(data.get("min_impressions") or 1),
        "store_raw": data.get("store_raw", True),
        "force_refresh": bool(data.get("force_refresh", False)),
    }
    discovery_payload = {k: v for k, v in discovery_payload.items() if v is not None}

    try:
        with current_app.test_request_context(
            path="/mcp/tools/list_linkedin_creatives_for_period",
            method="POST",
            json=discovery_payload,
        ):
            disc_resp = list_linkedin_creatives_for_period()
        if isinstance(disc_resp, tuple):
            disc_resp, disc_status = disc_resp[0], disc_resp[1]
        else:
            disc_status = disc_resp.status_code if hasattr(disc_resp, "status_code") else 200
        disc_body = disc_resp.get_json() if hasattr(disc_resp, "get_json") else {}
        if disc_status != 200 or (isinstance(disc_body, dict) and disc_body.get("error")):
            return (
                jsonify(disc_body if isinstance(disc_body, dict) else {"error": "Discovery failed"}),
                disc_status if disc_status >= 400 else 500,
            )

        creatives_list = disc_body.get("creatives") or []
        creative_ids = [c["creative_id"] for c in creatives_list if c.get("creative_id")]

        if not creative_ids:
            webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_MARKETING") or os.environ.get("DISCORD_WEBHOOK_URL")
            no_creatives_msg = (
                "## 📊 LinkedIn Creative Portfolio Report\n\n"
                "No creatives with activity in the discovery period (or none above min_impressions).\n\n"
                "No report or summary was run."
            )
            if webhook_url:
                post_to_discord(webhook_url, no_creatives_msg)
            date_range_out = disc_body.get("date_range") or {}
            return jsonify(
                {
                    "status": "success",
                    "creatives_discovered": 0,
                    "had_data": False,
                    "discord_posted": bool(webhook_url),
                    "message": "no_creatives",
                    "date_range": date_range_out,
                }
            )

        date_range = disc_body.get("date_range") or {}
        start_date_s = date_range.get("start_date")
        end_date_s = date_range.get("end_date")
        if not start_date_s or not end_date_s:
            return jsonify({"error": "Discovery did not return date_range"}), 500

        max_creatives = int(data.get("max_creatives_per_run") or 10)
        limited_creative_ids = creative_ids[:max_creatives]

        # 2) Fetch per-creative demographics (job title, function, country) for portfolio insights
        demographics_payload = {
            "account_urn": account_urn,
            "start_date": start_date_s,
            "end_date": end_date_s,
            "creative_ids": limited_creative_ids,
            "pivots": list(LINKEDIN_PORTFOLIO_REPORT_PIVOTS),
            "store_raw": data.get("store_raw", True),
            "force_refresh": bool(data.get("force_refresh", False)),
            "include_entity_names": bool(data.get("include_entity_names", True)),
        }
        with current_app.test_request_context(
            path="/mcp/tools/fetch_linkedin_creative_demographics_portfolio",
            method="POST",
            json=demographics_payload,
        ):
            demo_resp = fetch_linkedin_creative_demographics_portfolio()
        if isinstance(demo_resp, tuple):
            demo_resp, demo_status = demo_resp[0], demo_resp[1]
        else:
            demo_status = demo_resp.status_code if hasattr(demo_resp, "status_code") else 200
        demo_body = demo_resp.get_json() if hasattr(demo_resp, "get_json") else {}
        if demo_status != 200 or (isinstance(demo_body, dict) and demo_body.get("error")):
            return (
                jsonify(demo_body if isinstance(demo_body, dict) else {"error": "Demographics fetch failed"}),
                demo_status if demo_status >= 400 else 500,
            )

        # Build items for portfolio summarizer: need creative_id, pivot, enriched_storage_path
        results = demo_body.get("results") or []
        portfolio_items = [
            {"creative_id": r["creative_id"], "pivot": r["pivot"], "enriched_storage_path": r["enriched_storage_path"]}
            for r in results
            if r.get("enriched_storage_path")
        ]

        if portfolio_items:
            # 3a) Summarize with creative-portfolio summarizer (includes job title, function, country insights)
            summarize_payload = {
                "items": portfolio_items,
                "llm_model": (data.get("llm_model") or "gpt-4.1-mini").strip(),
            }
            with current_app.test_request_context(
                path="/mcp/tools/summarize_linkedin_creative_portfolio",
                method="POST",
                json=summarize_payload,
            ):
                sum_resp = summarize_linkedin_creative_portfolio()
            if isinstance(sum_resp, tuple):
                sum_resp, sum_status = sum_resp[0], sum_resp[1]
            else:
                sum_status = sum_resp.status_code if hasattr(sum_resp, "status_code") else 200
            sum_body = sum_resp.get_json() if hasattr(sum_resp, "get_json") else {}
            if sum_status != 200 or (isinstance(sum_body, dict) and sum_body.get("error")):
                return (
                    jsonify(sum_body if isinstance(sum_body, dict) else {"error": "Portfolio summarize failed"}),
                    sum_status if sum_status >= 400 else 500,
                )
            # Provide a single CREATIVE-level enriched path for cross-platform (fetch reuses cache when possible)
            ad_analytics_payload = {
                "account_urn": account_urn,
                "start_date": start_date_s,
                "end_date": end_date_s,
                "pivot": "CREATIVE",
                "store_raw": data.get("store_raw", True),
                "force_refresh": False,
                "include_entity_names": bool(data.get("include_entity_names", True)),
            }
            with current_app.test_request_context(
                path="/mcp/tools/fetch_linkedin_ad_analytics_report",
                method="POST",
                json=ad_analytics_payload,
            ):
                fetch_resp = fetch_linkedin_ad_analytics_report()
            if isinstance(fetch_resp, tuple):
                fetch_resp = fetch_resp[0]
            fetch_body = fetch_resp.get_json() if hasattr(fetch_resp, "get_json") else {}
            enriched_path = (fetch_body.get("enriched_storage_path") or "").strip() if isinstance(fetch_body, dict) else None
            return jsonify(
                {
                    "status": "success",
                    "creatives_discovered": len(creative_ids),
                    "had_data": sum_body.get("had_data", True),
                    "discord_posted": sum_body.get("discord_posted", False),
                    "used_model": sum_body.get("used_model"),
                    "report_type": "portfolio",
                    "date_range": {"start_date": start_date_s, "end_date": end_date_s},
                    "enriched_storage_path": enriched_path or None,
                }
            )

        # 3b) Fallback: no demographic data — use CREATIVE-level ad analytics + ad-analytics summarizer
        logger.warning(
            "run_linkedin_portfolio_report: no portfolio items with enriched_storage_path (results=%s); falling back to CREATIVE ad analytics",
            len(results),
        )
        ad_analytics_payload = {
            "account_urn": account_urn,
            "start_date": start_date_s,
            "end_date": end_date_s,
            "pivot": "CREATIVE",
            "store_raw": data.get("store_raw", True),
            "force_refresh": bool(data.get("force_refresh", False)),
            "include_entity_names": bool(data.get("include_entity_names", True)),
        }
        with current_app.test_request_context(
            path="/mcp/tools/fetch_linkedin_ad_analytics_report",
            method="POST",
            json=ad_analytics_payload,
        ):
            fetch_resp = fetch_linkedin_ad_analytics_report()
        if isinstance(fetch_resp, tuple):
            fetch_resp, fetch_status = fetch_resp[0], fetch_resp[1]
        else:
            fetch_status = fetch_resp.status_code if hasattr(fetch_resp, "status_code") else 200
        fetch_body = fetch_resp.get_json() if hasattr(fetch_resp, "get_json") else {}
        if fetch_status != 200 or (isinstance(fetch_body, dict) and fetch_body.get("error")):
            return (
                jsonify(fetch_body if isinstance(fetch_body, dict) else {"error": "Ad analytics fetch failed"}),
                fetch_status if fetch_status >= 400 else 500,
            )

        enriched_path = fetch_body.get("enriched_storage_path") if isinstance(fetch_body, dict) else None
        if not enriched_path:
            webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_MARKETING") or os.environ.get("DISCORD_WEBHOOK_URL")
            no_data_msg = (
                "## 📊 LinkedIn Creative Portfolio Report\n\n"
                "No demographic data was available and ad analytics fetch did not return an enriched path.\n\n"
                "No OpenAI analysis was generated."
            )
            if webhook_url:
                post_to_discord(webhook_url, no_data_msg)
            return jsonify(
                {
                    "status": "success",
                    "creatives_discovered": len(creative_ids),
                    "had_data": False,
                    "discord_posted": bool(webhook_url),
                    "message": "no_enriched_path",
                    "report_type": "ad_analytics",
                    "date_range": {"start_date": start_date_s, "end_date": end_date_s},
                }
            )

        summarize_payload = {
            "enriched_storage_path": enriched_path,
            "llm_model": (data.get("llm_model") or "gpt-4.1-mini").strip(),
            "sample_limit": int(data.get("sample_limit") or 50),
        }
        with current_app.test_request_context(
            path="/mcp/tools/summarize_linkedin_ad_analytics",
            method="POST",
            json=summarize_payload,
        ):
            sum_resp = summarize_linkedin_ad_analytics()
        if isinstance(sum_resp, tuple):
            sum_resp, sum_status = sum_resp[0], sum_resp[1]
        else:
            sum_status = sum_resp.status_code if hasattr(sum_resp, "status_code") else 200
        sum_body = sum_resp.get_json() if hasattr(sum_resp, "get_json") else {}
        if sum_status != 200 or (isinstance(sum_body, dict) and sum_body.get("error")):
            return (
                jsonify(sum_body if isinstance(sum_body, dict) else {"error": "Summarize failed"}),
                sum_status if sum_status >= 400 else 500,
            )

        return jsonify(
            {
                "status": "success",
                "creatives_discovered": len(creative_ids),
                "had_data": sum_body.get("had_data", True),
                "discord_posted": sum_body.get("discord_posted", False),
                "enriched_storage_path": enriched_path,
                "used_model": sum_body.get("used_model"),
                "report_type": "ad_analytics",
                "date_range": {"start_date": start_date_s, "end_date": end_date_s},
            }
        )
    except Exception:
        logger.error("Error in run_linkedin_portfolio_report: %s", traceback.format_exc())
        sanitized_error = sanitize_error_message(str(traceback.format_exc()))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/run_google_ads_portfolio_report_async', methods=['POST'])
def run_google_ads_portfolio_report_async():
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    timeout_seconds = int(data.get("timeout_seconds") or 300)
    timeout_seconds = max(10, min(timeout_seconds, 900))

    app_obj = current_app._get_current_object()
    access_header = app_obj.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
    request_key = (request.headers.get(access_header) or "").strip()
    if request_key:
        data["_internal_access_key"] = request_key

    job_id = _create_async_job(data, timeout_seconds=timeout_seconds)
    t = threading.Thread(
        target=_run_google_ads_portfolio_job,
        args=(app_obj, job_id, data),
        daemon=True,
    )
    t.start()

    return jsonify(
        {
            "status": "accepted",
            "job_id": job_id,
            "poll_after_seconds": 5,
            "timeout_seconds": timeout_seconds,
        }
    )


@marketing_bp.route('/mcp/tools/run_google_ads_portfolio_report', methods=['POST'])
def run_google_ads_portfolio_report():
    """
    Run a Google Ads performance portfolio report.

    Request JSON:
      - customer_id: optional (default: GOOGLE_ADS_CUSTOMER_ID)
      - login_customer_id: optional (default: GOOGLE_ADS_LOGIN_CUSTOMER_ID)
      - report_level: campaign | ad | audience_breakdown (default: campaign)
      - breakdowns: optional list for audience_breakdown (device | network | day_of_week)
      - start_date, end_date: YYYY-MM-DD (optional; default last 30 days)
      - store_raw: optional bool (default: false)
      - store_enriched: optional bool (default: false)
      - post_to_discord: optional bool (default: false)
      - discord_webhook_url: optional override (else DISCORD_WEBHOOK_URL_MARKETING / DISCORD_WEBHOOK_URL)
      - llm_model: optional model name for AI summary (default: gpt-4.1-mini)
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    run_async = bool(data.get("async", False)) and not bool(data.get("_internal_async_worker", False))
    timeout_seconds = int(data.get("timeout_seconds") or 300)
    timeout_seconds = max(10, min(timeout_seconds, 900))
    if run_async:
        app_obj = current_app._get_current_object()
        access_header = app_obj.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
        request_key = (request.headers.get(access_header) or "").strip()
        if request_key:
            data["_internal_access_key"] = request_key
        job_id = _create_async_job(data, timeout_seconds=timeout_seconds)
        t = threading.Thread(
            target=_run_google_ads_portfolio_job,
            args=(app_obj, job_id, data),
            daemon=True,
        )
        t.start()
        return jsonify(
            {
                "status": "accepted",
                "job_id": job_id,
                "poll_after_seconds": 5,
                "timeout_seconds": timeout_seconds,
            }
        )

    start_date_s = (data.get("start_date") or "").strip() or None
    end_date_s = (data.get("end_date") or "").strip() or None
    customer_id = (data.get("customer_id") or os.environ.get("GOOGLE_ADS_CUSTOMER_ID") or "").strip()
    login_customer_id = (data.get("login_customer_id") or os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or "").strip() or None
    report_level = (data.get("report_level") or "campaign").strip().lower()
    if report_level not in {"campaign", "ad", "audience_breakdown"}:
        return jsonify({"error": "report_level must be one of: campaign, ad, audience_breakdown"}), 400
    raw_breakdowns = data.get("breakdowns") or []
    if raw_breakdowns and not isinstance(raw_breakdowns, list):
        return jsonify({"error": "breakdowns must be a list when provided"}), 400
    breakdowns = [str(b).strip() for b in raw_breakdowns if str(b).strip()]

    store_raw = bool(data.get("store_raw", False))
    store_enriched = bool(data.get("store_enriched", False))
    post_to_discord = bool(data.get("post_to_discord", False))
    webhook_url = (
        (data.get("discord_webhook_url") or "").strip()
        or os.environ.get("DISCORD_WEBHOOK_URL_MARKETING")
        or os.environ.get("DISCORD_WEBHOOK_URL")
    )
    model = (data.get("llm_model") or "gpt-4.1-mini").strip()

    try:
        storage = None
        if store_raw or store_enriched:
            from bigas.resources.marketing.storage_service import StorageService
            storage = StorageService()

        result = run_google_ads_campaign_portfolio(
            start_date_s=start_date_s,
            end_date_s=end_date_s,
            customer_id=customer_id,
            login_customer_id=login_customer_id,
            report_level=report_level,
            breakdowns=breakdowns,
            store_raw=store_raw,
            store_enriched=store_enriched,
            storage=storage,
        )

        discord_info = None
        if post_to_discord and webhook_url:
            discord_info = _post_google_ads_portfolio_to_discord(
                webhook_url=webhook_url,
                model=model,
                portfolio_result=result,
            )

        payload = dict(result)
        if discord_info:
            payload["discord"] = discord_info
        return jsonify(payload)
    except Exception:
        logger.error("Error in run_google_ads_portfolio_report: %s", traceback.format_exc())
        sanitized_error = sanitize_error_message(str(traceback.format_exc()))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/run_meta_portfolio_report_async', methods=['POST'])
def run_meta_portfolio_report_async():
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    timeout_seconds = int(data.get("timeout_seconds") or 300)
    timeout_seconds = max(10, min(timeout_seconds, 900))

    app_obj = current_app._get_current_object()
    access_header = app_obj.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
    request_key = (request.headers.get(access_header) or "").strip()
    if request_key:
        data["_internal_access_key"] = request_key

    job_id = _create_async_job(data, timeout_seconds=timeout_seconds)
    t = threading.Thread(
        target=_run_meta_portfolio_job,
        args=(app_obj, job_id, data),
        daemon=True,
    )
    t.start()

    return jsonify(
        {
            "status": "accepted",
            "job_id": job_id,
            "poll_after_seconds": 5,
            "timeout_seconds": timeout_seconds,
        }
    )


@marketing_bp.route('/mcp/tools/run_meta_portfolio_report', methods=['POST'])
def run_meta_portfolio_report():
    """
    Run a Meta (Facebook/Instagram) Ads portfolio report.

    Request JSON:
      - account_id: optional (default: META_AD_ACCOUNT_ID)
      - report_level: campaign | ad | audience_breakdown (default: campaign)
      - breakdowns: optional list of breakdown dimensions for audience_breakdown
      - include_targeting: optional bool (default: false) include ad set targeting config snapshot
      - start_date, end_date: YYYY-MM-DD (optional; default last 30 days)
      - store_raw: optional bool (default: false)
      - store_enriched: optional bool (default: false)
      - post_to_discord: optional bool (default: false)
      - discord_webhook_url: optional override
      - llm_model: optional (default: gpt-4.1-mini)
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    run_async = bool(data.get("async", False)) and not bool(data.get("_internal_async_worker", False))
    timeout_seconds = int(data.get("timeout_seconds") or 300)
    timeout_seconds = max(10, min(timeout_seconds, 900))
    if run_async:
        app_obj = current_app._get_current_object()
        access_header = app_obj.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
        request_key = (request.headers.get(access_header) or "").strip()
        if request_key:
            data["_internal_access_key"] = request_key
        job_id = _create_async_job(data, timeout_seconds=timeout_seconds)
        t = threading.Thread(
            target=_run_meta_portfolio_job,
            args=(app_obj, job_id, data),
            daemon=True,
        )
        t.start()
        return jsonify(
            {
                "status": "accepted",
                "job_id": job_id,
                "poll_after_seconds": 5,
                "timeout_seconds": timeout_seconds,
            }
        )

    start_date_s = (data.get("start_date") or "").strip() or None
    end_date_s = (data.get("end_date") or "").strip() or None
    account_id = (data.get("account_id") or os.environ.get("META_AD_ACCOUNT_ID") or "").strip()
    report_level = (data.get("report_level") or "campaign").strip().lower()
    if report_level not in {"campaign", "ad", "audience_breakdown"}:
        return jsonify({"error": "report_level must be one of: campaign, ad, audience_breakdown"}), 400
    raw_breakdowns = data.get("breakdowns") or []
    if raw_breakdowns and not isinstance(raw_breakdowns, list):
        return jsonify({"error": "breakdowns must be a list when provided"}), 400
    breakdowns = [str(b).strip() for b in raw_breakdowns if str(b).strip()]
    include_targeting = bool(data.get("include_targeting", False))
    if not account_id:
        return jsonify({"error": "account_id is required (or set META_AD_ACCOUNT_ID)."}), 400

    store_raw = bool(data.get("store_raw", False))
    store_enriched = bool(data.get("store_enriched", False))
    post_to_discord = bool(data.get("post_to_discord", False))
    webhook_url = (
        (data.get("discord_webhook_url") or "").strip()
        or os.environ.get("DISCORD_WEBHOOK_URL_MARKETING")
        or os.environ.get("DISCORD_WEBHOOK_URL")
    )
    model = (data.get("llm_model") or "gpt-4.1-mini").strip()

    try:
        storage = None
        if store_raw or store_enriched:
            from bigas.resources.marketing.storage_service import StorageService
            storage = StorageService()

        result = run_meta_campaign_portfolio(
            start_date_s=start_date_s,
            end_date_s=end_date_s,
            account_id=account_id,
            report_level=report_level,
            breakdowns=breakdowns,
            include_targeting=include_targeting,
            store_raw=store_raw,
            store_enriched=store_enriched,
            storage=storage,
        )

        discord_info = None
        if post_to_discord and webhook_url:
            discord_info = _post_meta_portfolio_to_discord(
                webhook_url=webhook_url,
                model=model,
                portfolio_result=result,
            )

        payload = dict(result)
        if discord_info:
            payload["discord"] = discord_info
        return jsonify(payload)
    except Exception:
        logger.error("Error in run_meta_portfolio_report: %s", traceback.format_exc())
        sanitized_error = sanitize_error_message(str(traceback.format_exc()))
        return jsonify({"error": sanitized_error}), 500


def _run_reddit_audience_fetch(
    report_type: str,
    account_id: str,
    start_date_s: str,
    end_date_s: str,
    campaign_id: Optional[str] = None,
    return_raw: bool = False,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Fetch one audience report and return (top rows by clicks, optional raw API response).
    Rows are capped at 25. If campaign_id is set, request is campaign-scoped.
    When return_raw is True, also request and return the raw Reddit API response for troubleshooting."""
    payload = {
        "account_id": account_id,
        "report_type": report_type,
        "start_date": start_date_s,
        "end_date": end_date_s,
    }
    if campaign_id:
        payload["campaign_id"] = campaign_id
    if return_raw:
        payload["include_raw_response"] = True
    try:
        with current_app.test_request_context(
            path="/mcp/tools/fetch_reddit_audience_report",
            method="POST",
            json=payload,
        ):
            resp = fetch_reddit_audience_report()
        if isinstance(resp, tuple):
            resp, status = resp[0], resp[1]
        else:
            status = resp.status_code if hasattr(resp, "status_code") else 200
        body = resp.get_json() if hasattr(resp, "get_json") else {}
        if status != 200 or (isinstance(body, dict) and body.get("error")):
            if campaign_id and status == 200 and (body.get("data") or []) == []:
                return ([], body.get("raw_response") if return_raw else None)
            if campaign_id and (status != 200 or body.get("error")):
                logger.info(
                    "Reddit audience %s with campaign_id failed, retrying account-level: %s",
                    report_type,
                    body.get("error", status),
                )
                return _run_reddit_audience_fetch(
                    report_type, account_id, start_date_s, end_date_s, campaign_id=None, return_raw=return_raw
                )
            return ([], None)
        rows = body.get("data") or []
        if not isinstance(rows, list):
            return ([], body.get("raw_response") if return_raw else None)
        def _sort_key(r: Dict[str, Any]) -> tuple:
            if not isinstance(r, dict):
                return (0, 0, 0)
            clicks = int(r.get("clicks") or 0)
            imp = int(r.get("impressions") or 0)
            reach = int(r.get("reach") or 0)
            return (-clicks, -imp, -reach)
        rows = sorted(rows, key=_sort_key)
        raw = body.get("raw_response") if return_raw else None
        return (rows[:25], raw)
    except Exception as e:
        logger.warning("Reddit audience fetch %s failed: %s", report_type, e)
        if campaign_id:
            try:
                return _run_reddit_audience_fetch(
                    report_type, account_id, start_date_s, end_date_s, campaign_id=None, return_raw=return_raw
                )
            except Exception:
                return ([], None)
        return ([], None)


@marketing_bp.route('/mcp/tools/run_reddit_portfolio_report_async', methods=['POST'])
def run_reddit_portfolio_report_async():
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    timeout_seconds = int(data.get("timeout_seconds") or 300)
    timeout_seconds = max(10, min(timeout_seconds, 900))

    app_obj = current_app._get_current_object()
    access_header = app_obj.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
    request_key = (request.headers.get(access_header) or "").strip()
    if request_key:
        data["_internal_access_key"] = request_key

    job_id = _create_async_job(data, timeout_seconds=timeout_seconds)
    t = threading.Thread(
        target=_run_reddit_portfolio_job,
        args=(app_obj, job_id, data),
        daemon=True,
    )
    t.start()

    return jsonify(
        {
            "status": "accepted",
            "job_id": job_id,
            "poll_after_seconds": 5,
            "timeout_seconds": timeout_seconds,
        }
    )


@marketing_bp.route('/mcp/tools/run_reddit_portfolio_report', methods=['POST'])
def run_reddit_portfolio_report():
    """
    Full Reddit Ads portfolio report (like LinkedIn): fetch performance + audience data,
    then generate facts, summary, and recommendations and post to Discord.

    Request JSON:
      - account_id: optional (default: REDDIT_AD_ACCOUNT_ID)
      - relative_range: LAST_7_DAYS | LAST_30_DAYS, or start_date / end_date
      - store_raw, force_refresh: optional
      - llm_model: optional (default: gpt-4.1-mini)
      - include_audience: optional (default: true) — fetch interests, communities, country, region, DMA
      - debug_audience: optional (default: false) — if true, response includes troubleshooting with exact
        interest/community payload and raw Reddit API responses so you can compare to the UI (same date range and campaign).
      - post_to_discord: optional (default: true) — if false, do not post the full report to Discord (e.g. when called from cross-platform).
    """
    data = request.json or {}
    run_async = bool(data.get("async", False)) and not bool(data.get("_internal_async_worker", False))
    timeout_seconds = int(data.get("timeout_seconds") or 300)
    timeout_seconds = max(10, min(timeout_seconds, 900))
    if run_async:
        app_obj = current_app._get_current_object()
        access_header = app_obj.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
        request_key = (request.headers.get(access_header) or "").strip()
        if request_key:
            data["_internal_access_key"] = request_key
        job_id = _create_async_job(data, timeout_seconds=timeout_seconds)
        t = threading.Thread(
            target=_run_reddit_portfolio_job,
            args=(app_obj, job_id, data),
            daemon=True,
        )
        t.start()
        return jsonify(
            {
                "status": "accepted",
                "job_id": job_id,
                "poll_after_seconds": 5,
                "timeout_seconds": timeout_seconds,
            }
        )

    post_reddit_to_discord = bool(data.get("post_to_discord", True))
    account_id = (data.get("account_id") or os.environ.get("REDDIT_AD_ACCOUNT_ID") or "").strip()
    if not account_id:
        return jsonify({"error": "account_id is required (or set REDDIT_AD_ACCOUNT_ID)."}), 400

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_MARKETING") or os.environ.get("DISCORD_WEBHOOK_URL")
    include_audience = bool(data.get("include_audience", True))
    debug_audience = bool(data.get("debug_audience", False))

    try:
        fetch_payload = {
            "account_id": account_id,
            "relative_range": data.get("relative_range") or "LAST_7_DAYS",
            "store_raw": data.get("store_raw", True),
            "force_refresh": bool(data.get("force_refresh", False)) or debug_audience,
        }
        if data.get("start_date"):
            fetch_payload["start_date"] = data["start_date"]
        if data.get("end_date"):
            fetch_payload["end_date"] = data["end_date"]

        with current_app.test_request_context(
            path="/mcp/tools/fetch_reddit_ad_analytics_report",
            method="POST",
            json=fetch_payload,
        ):
            fetch_resp = fetch_reddit_ad_analytics_report()
        if isinstance(fetch_resp, tuple):
            fetch_resp, fetch_status = fetch_resp[0], fetch_resp[1]
        else:
            fetch_status = fetch_resp.status_code if hasattr(fetch_resp, "status_code") else 200
        fetch_body = fetch_resp.get_json() if hasattr(fetch_resp, "get_json") else {}
        if fetch_status != 200 or (isinstance(fetch_body, dict) and fetch_body.get("error")):
            return (
                jsonify(fetch_body if isinstance(fetch_body, dict) else {"error": "Reddit ad analytics fetch failed"}),
                fetch_status if fetch_status >= 400 else 500,
            )

        date_range = fetch_body.get("date_range") or {}
        start_date_s = date_range.get("start_date") or fetch_payload.get("start_date") or ""
        end_date_s = date_range.get("end_date") or fetch_payload.get("end_date") or ""
        enriched_path = fetch_body.get("enriched_storage_path") if isinstance(fetch_body, dict) else None

        # When debugging audience, load raw Reddit performance API response to inspect structure (e.g. why no campaign_id).
        raw_performance_response: Optional[Dict[str, Any]] = None
        if debug_audience and isinstance(fetch_body, dict) and fetch_body.get("storage_path"):
            try:
                from bigas.resources.marketing.storage_service import StorageService
                _storage = StorageService()
                _raw_obj = _storage.get_json(fetch_body["storage_path"])
                if isinstance(_raw_obj, dict):
                    raw_performance_response = _raw_obj.get("payload", {}).get("raw_response")
            except Exception as e:
                logger.warning("Could not load raw Reddit performance blob for debug: %s", e)

        # Load performance data from enriched blob (if we have it).
        # Re-normalize spend in elements (blob may have been stored before micro fix) and add total_spend so the model sees one clear EUR total.
        performance_payload = None
        if enriched_path:
            try:
                from bigas.resources.marketing.storage_service import StorageService
                storage = StorageService()
                obj = storage.get_json(enriched_path)
                if obj and isinstance(obj, dict):
                    payload = obj.get("payload") or {}
                    enriched = payload.get("enriched_response") or {}
                    raw_elements = (enriched.get("elements") or [])[:30]
                    elements = []
                    total_spend = 0.0
                    total_impressions = 0
                    total_clicks = 0
                    for el in raw_elements:
                        if not isinstance(el, dict):
                            elements.append(el)
                            continue
                        el = dict(el)
                        metrics = el.get("metrics") or {}
                        if isinstance(metrics, dict):
                            if metrics.get("spend") is not None:
                                norm = _normalize_reddit_spend(metrics.get("spend"), metrics)
                                if norm is not None:
                                    metrics = dict(metrics)
                                    metrics["spend"] = norm
                                    total_spend += norm
                                el["metrics"] = metrics
                            try:
                                total_impressions += int(metrics.get("impressions") or 0)
                                total_clicks += int(metrics.get("clicks") or 0)
                            except (TypeError, ValueError):
                                pass
                        elements.append(el)
                    summary = dict(enriched.get("summary") or {})
                    summary["total_spend"] = round(total_spend, 2)
                    summary["total_spend_currency"] = "EUR"
                    summary["total_impressions"] = total_impressions
                    summary["total_clicks"] = total_clicks
                    if total_impressions > 0:
                        summary["total_ctr_pct"] = round(100.0 * total_clicks / total_impressions, 2)
                    else:
                        summary["total_ctr_pct"] = None
                    if total_clicks > 0:
                        summary["total_cpc"] = round(total_spend / total_clicks, 2)
                    else:
                        summary["total_cpc"] = None
                    context = dict(enriched.get("context") or {})
                    context["spend_currency"] = "EUR"
                    performance_payload = {
                        "summary": summary,
                        "context": context,
                        "elements": elements,
                    }
            except Exception as e:
                logger.warning("Could not load Reddit enriched blob %s: %s", enriched_path, e)

        # Discover all campaigns from performance (active/paused/done in period), sorted by spend desc (like LinkedIn discovery).
        discovered_campaigns: List[Dict[str, Any]] = []
        campaign_spend: Dict[str, float] = {}
        campaign_name: Dict[str, Optional[str]] = {}
        if performance_payload and performance_payload.get("elements"):
            for el in performance_payload["elements"]:
                if not isinstance(el, dict):
                    continue
                cid = el.get("campaign_id")
                name = el.get("campaign_name")
                if cid is None and isinstance(el.get("segments"), list):
                    for seg in el["segments"]:
                        if isinstance(seg, str) and seg.startswith("campaign_id="):
                            cid = seg.split("=", 1)[1].strip()
                            break
                spend = 0.0
                m = el.get("metrics") or {}
                if isinstance(m, dict) and m.get("spend") is not None:
                    try:
                        spend = float(m["spend"])
                    except (TypeError, ValueError):
                        pass
                if cid is not None:
                    campaign_spend[cid] = campaign_spend.get(cid, 0.0) + spend
                    if cid not in campaign_name and name:
                        campaign_name[cid] = name
            for cid, total in sorted(campaign_spend.items(), key=lambda x: -x[1]):
                discovered_campaigns.append({
                    "campaign_id": cid,
                    "campaign_name": campaign_name.get(cid),
                    "spend": round(total, 2),
                })
        top_campaign_id = discovered_campaigns[0]["campaign_id"] if discovered_campaigns else None
        top_campaign_name = (discovered_campaigns[0].get("campaign_name") if discovered_campaigns else None)

        # Fetch audience (interests, communities, etc.) per discovered campaign, like LinkedIn per-creative.
        MAX_CAMPAIGNS_FOR_AUDIENCE = 10
        audience_payload: Dict[str, List[Dict[str, Any]]] = {}
        audience_by_campaign: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        raw_reddit_interests: Optional[Dict[str, Any]] = None
        raw_reddit_communities: Optional[Dict[str, Any]] = None
        if include_audience and start_date_s and end_date_s:
            campaigns_to_fetch = discovered_campaigns[:MAX_CAMPAIGNS_FOR_AUDIENCE] if discovered_campaigns else []
            for report_type in ("interests", "communities", "country", "region", "dma"):
                # Aggregate account-level for country/region/dma (once); per-campaign only for interests and communities.
                if report_type in ("interests", "communities") and campaigns_to_fetch:
                    for camp in campaigns_to_fetch:
                        cid = camp["campaign_id"]
                        rows, raw_response = _run_reddit_audience_fetch(
                            report_type, account_id, start_date_s, end_date_s,
                            campaign_id=cid,
                            return_raw=debug_audience and cid == top_campaign_id and report_type in ("interests", "communities"),
                        )
                        if debug_audience and cid == top_campaign_id and report_type == "interests":
                            raw_reddit_interests = raw_response
                        if debug_audience and cid == top_campaign_id and report_type == "communities":
                            raw_reddit_communities = raw_response
                        out_rows = _normalize_audience_rows(rows)
                        audience_by_campaign.setdefault(cid, {})[report_type] = out_rows
                    # Top campaign's data for main audience payload (backward compat + summary)
                    audience_payload[report_type] = (audience_by_campaign.get(top_campaign_id) or {}).get(report_type, []) if top_campaign_id else []
                else:
                    # Single fetch: account-level for country/region/dma, or top campaign when no discovery
                    rows, _ = _run_reddit_audience_fetch(
                        report_type, account_id, start_date_s, end_date_s,
                        campaign_id=top_campaign_id if report_type in ("interests", "communities") else None,
                        return_raw=False,
                    )
                    audience_payload[report_type] = _normalize_audience_rows(rows)
                    if report_type in ("interests", "communities") and top_campaign_id and top_campaign_id not in audience_by_campaign:
                        audience_by_campaign.setdefault(top_campaign_id, {})[report_type] = audience_payload[report_type]

        combined = {
            "account_id": account_id,
            "date_range": {"start_date": start_date_s, "end_date": end_date_s},
            "performance": performance_payload,
            "audience": audience_payload,
            "audience_scope": "campaign" if top_campaign_id else "account",
            "audience_campaign_id": top_campaign_id,
            "audience_campaign_name": top_campaign_name,
            "discovered_campaigns": discovered_campaigns,
            "audience_by_campaign": audience_by_campaign,
        }

        has_any_data = bool(performance_payload and (performance_payload.get("elements") or performance_payload.get("summary"))) or any(audience_payload.get(k) for k in ("interests", "communities", "country", "region", "dma"))

        if not has_any_data:
            no_data_msg = (
                "## 📊 Reddit Ads Portfolio Report\n\n"
                "No performance or audience data was available for the period.\n\n"
                "No analysis was generated."
            )
            if post_reddit_to_discord and webhook_url:
                post_to_discord(webhook_url, no_data_msg)
            return jsonify(
                {
                    "status": "success",
                    "had_data": False,
                    "discord_posted": bool(post_reddit_to_discord and webhook_url),
                    "message": "no_data",
                    "date_range": {"start_date": start_date_s, "end_date": end_date_s},
                }
            )

        model = (data.get("llm_model") or "gpt-4.1-mini").strip()
        prompt_cfg = AD_SUMMARY_PROMPTS.get(("reddit", "portfolio"))
        if not prompt_cfg:
            return jsonify({"error": "Prompt configuration missing for Reddit portfolio"}), 500
        system_prompt = prompt_cfg["system"]
        user_prompt = prompt_cfg["user_template"].format(
            platform="reddit",
            payload=json.dumps(combined, indent=2),
        )

        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        completion = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1100,
            temperature=0.4,
            timeout=40,
        )
        analysis_text = completion.choices[0].message.content.strip()

        scope_note = ""
        if discovered_campaigns:
            scope_note = f" Discovered {len(discovered_campaigns)} campaign(s); audience data fetched per campaign (top by spend: {top_campaign_name or top_campaign_id})."
        elif top_campaign_id:
            scope_note = f" Audience is for top campaign by spend: {top_campaign_name or top_campaign_id}."
        discord_message = (
            "## 📊 Reddit Ads Portfolio Report\n\n"
            f"{analysis_text}\n\n"
            "---\n"
            f"_Performance + audience for {start_date_s}–{end_date_s}.{scope_note}_"
        )
        if post_reddit_to_discord and webhook_url:
            post_long_to_discord(webhook_url, discord_message)

        out = {
            "status": "success",
            "had_data": True,
            "discord_posted": bool(post_reddit_to_discord and webhook_url),
            "enriched_storage_path": enriched_path,
            "used_model": model,
            "date_range": {"start_date": start_date_s, "end_date": end_date_s},
        }
        if debug_audience:
            elements = (performance_payload or {}).get("elements") or []
            first_el = elements[0] if elements and isinstance(elements[0], dict) else None
            first_el_sample = None
            if first_el:
                segs = first_el.get("segments") or []
                first_el_sample = {
                    "campaign_id": first_el.get("campaign_id"),
                    "campaign_name": first_el.get("campaign_name"),
                    "segments_preview": segs[:5] if isinstance(segs, list) else str(segs)[:200],
                }
            out["troubleshooting"] = {
                "note": "Compare these numbers to Reddit Ads Manager UI: set the same date range (start_date–end_date) and, if audience_scope is campaign, select that campaign in the UI. Reference: last 7 days in UI shows News & Education = 19 clicks; compare API interests to this.",
                "date_range": {"start_date": start_date_s, "end_date": end_date_s},
                "audience_scope": "campaign" if top_campaign_id else "account",
                "top_campaign_id": top_campaign_id,
                "top_campaign_name": top_campaign_name,
                "discovered_campaigns_count": len(discovered_campaigns),
                "discovered_campaigns": discovered_campaigns,
                "audience_by_campaign_keys": list(audience_by_campaign.keys()),
                "performance_elements_count": len(elements),
                "first_element_has_campaign_id": first_el is not None and (first_el.get("campaign_id") is not None or any(isinstance(s, str) and s.startswith("campaign_id=") for s in (first_el.get("segments") or []))),
                "first_element_keys": list(first_el.keys()) if first_el else None,
                "first_element_sample": first_el_sample,
                "interests_payload": audience_payload.get("interests", []),
                "communities_payload": audience_payload.get("communities", []),
                "interests_count": len(audience_payload.get("interests") or []),
                "communities_count": len(audience_payload.get("communities") or []),
            }
            if raw_reddit_interests is not None:
                out["troubleshooting"]["raw_reddit_interests"] = raw_reddit_interests
            if raw_reddit_communities is not None:
                out["troubleshooting"]["raw_reddit_communities"] = raw_reddit_communities
            if raw_performance_response is not None:
                out["troubleshooting"]["raw_performance_response"] = raw_performance_response
        return jsonify(out)
    except Exception:
        logger.error("Error in run_reddit_portfolio_report: %s", traceback.format_exc())
        sanitized_error = sanitize_error_message(str(traceback.format_exc()))
        return jsonify({"error": sanitized_error}), 500


@marketing_bp.route('/mcp/tools/run_cross_platform_marketing_analysis', methods=['POST'])
def run_cross_platform_marketing_analysis():
    """
    Run fresh LinkedIn, Reddit, Google Ads, and Meta portfolio reports (default last 30 days), then compare
    them with an AI marketing analyst and post a single Discord report: summary, key data
    points, and recommendation on where to spend more budget (e.g. LinkedIn focus X, Reddit focus Y, Google Ads focus Z, Meta focus W).

    Request JSON:
      - relative_range: LAST_30_DAYS | LAST_7_DAYS | LAST_90_DAYS (default: LAST_30_DAYS)
      - account_urn: optional LinkedIn account (default: LINKEDIN_AD_ACCOUNT_URN)
      - account_id: optional Reddit account (default: REDDIT_AD_ACCOUNT_ID)
      - customer_id: optional Google Ads customer (default: GOOGLE_ADS_CUSTOMER_ID); if missing, Google Ads is skipped
      - meta_account_id: optional Meta ad account (default: META_AD_ACCOUNT_ID); if missing, Meta is skipped
      - llm_model: optional (default: gpt-4.1-mini)
      - sample_limit: optional max rows per platform for comparison (default: 50)

    Flow: run LinkedIn, Reddit, Google Ads, and Meta portfolio reports in parallel (each posts its report + progress),
    then build combined payload -> OpenAI analyst -> post cross-platform summary to Discord.
    For long runs, increase Cloud Run request timeout (e.g. gcloud run services update
    mcp-marketing --timeout=3600 --region=europe-north1 for 1 hour).
    """
    data = request.json or {}
    relative_range = (data.get("relative_range") or "LAST_30_DAYS").strip().upper()
    if relative_range not in ("LAST_7_DAYS", "LAST_30_DAYS", "LAST_90_DAYS"):
        relative_range = "LAST_30_DAYS"

    # Compute date range for Google Ads (and fallback) from relative_range
    _end = date.today()
    if relative_range == "LAST_7_DAYS":
        _start = _end - timedelta(days=6)
    elif relative_range == "LAST_90_DAYS":
        _start = _end - timedelta(days=89)
    else:
        _start = _end - timedelta(days=29)
    _start_s = _start.isoformat()
    _end_s = _end.isoformat()

    account_urn = (data.get("account_urn") or os.environ.get("LINKEDIN_AD_ACCOUNT_URN") or "").strip()
    if account_urn.isdigit():
        account_urn = f"urn:li:sponsoredAccount:{account_urn}"

    account_id = (data.get("account_id") or os.environ.get("REDDIT_AD_ACCOUNT_ID") or "").strip()
    if not account_id:
        return jsonify({"error": "account_id is required (or set REDDIT_AD_ACCOUNT_ID)."}), 400

    google_ads_customer_id = (data.get("customer_id") or os.environ.get("GOOGLE_ADS_CUSTOMER_ID") or "").strip()
    meta_account_id = (data.get("meta_account_id") or os.environ.get("META_AD_ACCOUNT_ID") or "").strip()

    if not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY is not configured on the server"}), 500

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_MARKETING") or os.environ.get("DISCORD_WEBHOOK_URL")
    sample_limit = int(data.get("sample_limit") or 50)
    model = (data.get("llm_model") or "gpt-4.1-mini").strip()

    try:
        import concurrent.futures

        from bigas.resources.marketing.storage_service import StorageService
        storage = StorageService()

        if not account_urn:
            return jsonify({"error": "account_urn is required (or set LINKEDIN_AD_ACCOUNT_URN)."}), 400

        linkedin_payload_req = {
            "account_urn": account_urn,
            "discovery_relative_range": relative_range,
            "store_raw": data.get("store_raw", True),
            "force_refresh": bool(data.get("force_refresh", False)),
            "llm_model": model,
            "post_to_discord": True,
        }
        reddit_payload_req = {
            "account_id": account_id,
            "relative_range": relative_range,
            "store_raw": data.get("store_raw", True),
            "force_refresh": bool(data.get("force_refresh", False)),
            "llm_model": model,
            "post_to_discord": True,
        }
        google_ads_payload_req = {
            "start_date": _start_s,
            "end_date": _end_s,
            "customer_id": google_ads_customer_id or None,
            "store_raw": data.get("store_raw", False),
            "store_enriched": data.get("store_enriched", False),
            "post_to_discord": True,
        }
        meta_payload_req = {
            "start_date": _start_s,
            "end_date": _end_s,
            "account_id": meta_account_id or None,
            "store_raw": data.get("store_raw", False),
            "store_enriched": data.get("store_enriched", False),
            "post_to_discord": True,
        }

        app_obj = current_app._get_current_object()

        def _call_local_endpoint(func, *, path: str, json_payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
            with app_obj.app_context():
                with app_obj.test_request_context(path=path, method="POST", json=json_payload):
                    resp = func()
            if isinstance(resp, tuple):
                resp, status = resp[0], resp[1]
            else:
                status = resp.status_code if hasattr(resp, "status_code") else 200
            body = resp.get_json() if hasattr(resp, "get_json") else {}
            return int(status or 0), (body if isinstance(body, dict) else {})

        platforms_note = "LinkedIn + Reddit" + (" + Google Ads" if google_ads_customer_id else "") + (" + Meta" if meta_account_id else "")
        if webhook_url:
            post_to_discord(
                webhook_url,
                f"⏳ **Cross-platform run started** for `{relative_range}`. Running {platforms_note} portfolio reports in parallel…",
            )

        li_status = rd_status = ga_status = meta_status = 0
        li_body = rd_body = ga_body = meta_body = {}

        futures = []
        fut_li = fut_rd = fut_ga = fut_meta = None
        max_workers = 2 + (1 if google_ads_customer_id else 0) + (1 if meta_account_id else 0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            fut_li = ex.submit(
                _call_local_endpoint,
                run_linkedin_portfolio_report,
                path="/mcp/tools/run_linkedin_portfolio_report",
                json_payload=linkedin_payload_req,
            )
            fut_rd = ex.submit(
                _call_local_endpoint,
                run_reddit_portfolio_report,
                path="/mcp/tools/run_reddit_portfolio_report",
                json_payload=reddit_payload_req,
            )
            futures = [fut_li, fut_rd]
            if google_ads_customer_id:
                fut_ga = ex.submit(
                    _call_local_endpoint,
                    run_google_ads_portfolio_report,
                    path="/mcp/tools/run_google_ads_portfolio_report",
                    json_payload=google_ads_payload_req,
                )
                futures.append(fut_ga)
            if meta_account_id:
                fut_meta = ex.submit(
                    _call_local_endpoint,
                    run_meta_portfolio_report,
                    path="/mcp/tools/run_meta_portfolio_report",
                    json_payload=meta_payload_req,
                )
                futures.append(fut_meta)

            for fut in concurrent.futures.as_completed(futures):
                status, body = fut.result()
                if fut is fut_li:
                    li_status, li_body = status, body
                    if webhook_url:
                        _dr = li_body.get("date_range") or {}
                        _start = (_dr.get("start_date") or "?")
                        _end = (_dr.get("end_date") or "?")
                        post_to_discord(
                            webhook_url,
                            f"📊 **Cross-platform run:** LinkedIn portfolio step finished for {_start}–{_end}.",
                        )
                elif fut is fut_rd:
                    rd_status, rd_body = status, body
                    if webhook_url:
                        _dr = rd_body.get("date_range") or {}
                        _start = (_dr.get("start_date") or "?")
                        _end = (_dr.get("end_date") or "?")
                        post_to_discord(
                            webhook_url,
                            f"📊 **Cross-platform run:** Reddit portfolio step finished for {_start}–{_end}.",
                        )
                elif fut is fut_ga:
                    ga_status, ga_body = status, body
                    if webhook_url:
                        _meta = ga_body.get("request_metadata") or {}
                        _start = _meta.get("start_date") or "?"
                        _end = _meta.get("end_date") or "?"
                        post_to_discord(
                            webhook_url,
                            f"📊 **Cross-platform run:** Google Ads portfolio step finished for {_start}–{_end}.",
                        )
                elif fut is fut_meta:
                    meta_status, meta_body = status, body
                    if webhook_url:
                        _meta_meta = meta_body.get("request_metadata") or {}
                        _start = _meta_meta.get("start_date") or "?"
                        _end = _meta_meta.get("end_date") or "?"
                        post_to_discord(
                            webhook_url,
                            f"📊 **Cross-platform run:** Meta portfolio step finished for {_start}–{_end}.",
                        )

        if li_status != 200 or li_body.get("error"):
            return (
                jsonify(li_body if isinstance(li_body, dict) else {"error": "LinkedIn portfolio report failed"}),
                li_status if li_status >= 400 else 500,
            )
        if rd_status != 200 or rd_body.get("error"):
            return (
                jsonify(rd_body if isinstance(rd_body, dict) else {"error": "Reddit portfolio report failed"}),
                rd_status if rd_status >= 400 else 500,
            )

        li_date_range = li_body.get("date_range") or {}
        rd_date_range = rd_body.get("date_range") or {}

        li_enriched_path = (li_body.get("enriched_storage_path") or "").strip()
        rd_enriched_path = (rd_body.get("enriched_storage_path") or "").strip()

        _meta_req = (meta_body.get("request_metadata") or {}) if meta_account_id else {}
        start_date_s = (li_date_range.get("start_date") or rd_date_range.get("start_date") or _meta_req.get("start_date") or _start_s)
        end_date_s = (li_date_range.get("end_date") or rd_date_range.get("end_date") or _meta_req.get("end_date") or _end_s)

        # If LinkedIn used portfolio summarizer (no single enriched path), fetch CREATIVE ad analytics for comparison
        if not li_enriched_path and start_date_s and end_date_s:
            fetch_li_payload = {
                "account_urn": account_urn,
                "start_date": start_date_s,
                "end_date": end_date_s,
                "pivot": "CREATIVE",
                "store_raw": True,
                "force_refresh": False,
                "include_entity_names": True,
            }
            fetch_status, fetch_body = _call_local_endpoint(
                fetch_linkedin_ad_analytics_report,
                path="/mcp/tools/fetch_linkedin_ad_analytics_report",
                json_payload=fetch_li_payload,
            )
            if fetch_status == 200:
                li_enriched_path = (fetch_body.get("enriched_storage_path") or "").strip()

        # Build compact payloads for comparison
        linkedin_compact = _build_linkedin_compact_payload(storage, li_enriched_path, sample_limit) if li_enriched_path else None
        if linkedin_compact and account_urn:
            try:
                from bigas.resources.marketing.linkedin_ads_service import LinkedInAdsService
                svc = LinkedInAdsService()
                acc = svc.get_ad_account(account_urn)
                api_currency = (acc.get("currency") or "").strip().upper()
                if api_currency:
                    linkedin_compact["currency"] = api_currency
            except Exception as e:
                logger.warning("LinkedIn ad account currency from API failed (using existing): %s", e)
        reddit_compact = _build_reddit_compact_payload(storage, rd_enriched_path, sample_limit) if rd_enriched_path else None
        google_ads_compact = None
        if google_ads_customer_id and ga_status == 200 and not ga_body.get("error"):
            ga_summary = ga_body.get("summary") or {}
            google_ads_compact = {
                "platform": "google_ads",
                "currency": (ga_summary.get("currency") or "").strip().upper() or None,
                "summary": ga_summary,
                "request_metadata": ga_body.get("request_metadata") or {},
                "sample_rows": (ga_body.get("rows") or [])[:sample_limit],
            }
        meta_compact = None
        if meta_account_id and meta_status == 200 and not meta_body.get("error"):
            meta_summary = meta_body.get("summary") or {}
            meta_compact = {
                "platform": "meta",
                "currency": (meta_summary.get("currency") or "").strip().upper() or None,
                "summary": meta_summary,
                "request_metadata": meta_body.get("request_metadata") or {},
                "sample_rows": (meta_body.get("rows") or [])[:sample_limit],
            }

        date_range_str = f"{start_date_s or '?'} to {end_date_s or '?'}"
        ga4_property_id = os.environ.get("GA4_PROPERTY_ID") or ""
        ga4_attribution = _get_ga4_paid_social_attribution(
            ga4_property_id,
            start_date_s or _start_s,
            end_date_s or _end_s,
        ) if (ga4_property_id and start_date_s and end_date_s) else {"note": "GA4 attribution skipped (no property ID or date range)", "by_source": []}
        combined = {
            "date_range": date_range_str,
            "platforms": {
                "linkedin": linkedin_compact if linkedin_compact else {"note": "No LinkedIn data available for comparison."},
                "reddit": reddit_compact if reddit_compact else {"note": "No Reddit data available for comparison."},
                "google_ads": google_ads_compact if google_ads_compact else {"note": "No Google Ads data available for comparison."},
                "meta": meta_compact if meta_compact else {"note": "No Meta Ads data available for comparison."},
            },
            "ga4_attribution": ga4_attribution,
        }

        if not linkedin_compact and not reddit_compact and not google_ads_compact and not meta_compact:
            no_data_msg = (
                "## 📊 Cross-Platform Marketing Budget Analysis\n\n"
                "No LinkedIn, Reddit, Google Ads, or Meta data was available for the period.\n\n"
                f"Date range: {date_range_str}\n\n"
                "No comparison was generated."
            )
            if webhook_url:
                post_long_to_discord(webhook_url, no_data_msg)
            return jsonify(
                {
                    "status": "success",
                    "had_data": False,
                    "discord_posted": bool(webhook_url),
                    "message": "no_data",
                    "date_range": {"start_date": start_date_s, "end_date": end_date_s},
                }
            )

        prompt_cfg = AD_SUMMARY_PROMPTS.get(("cross_platform", "budget_analysis"))
        if not prompt_cfg:
            return jsonify({"error": "Prompt configuration missing for cross-platform budget analysis"}), 500

        system_prompt = prompt_cfg["system"]
        user_prompt = prompt_cfg["user_template"].format(
            date_range=date_range_str,
            payload=json.dumps(combined, indent=2),
        )

        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        completion = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1200,
            temperature=0.4,
            timeout=60,
        )
        analysis_text = completion.choices[0].message.content.strip()

        discord_message = (
            "## 📊 Cross-Platform Marketing Budget Analysis\n\n"
            f"{analysis_text}\n\n"
            "---\n"
            f"_LinkedIn + Reddit + Google Ads + Meta comparison for {date_range_str}. Sources: platform portfolio reports._"
        )
        if webhook_url:
            post_long_to_discord(webhook_url, discord_message)

        return jsonify(
            {
                "status": "success",
                "had_data": True,
                "discord_posted": bool(webhook_url),
                "used_model": model,
                "date_range": {"start_date": start_date_s, "end_date": end_date_s},
                "linkedin_enriched_path": li_enriched_path or None,
                "reddit_enriched_path": rd_enriched_path or None,
                "google_ads_included": bool(google_ads_compact),
                "meta_included": bool(meta_compact),
            }
        )
    except Exception as e:
        logger.error("Error in run_cross_platform_marketing_analysis: %s", traceback.format_exc())
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500



@marketing_bp.route('/mcp/tools/weekly_analytics_report', methods=['POST'])
def weekly_analytics_report():
    import os  # Import os at function level to avoid UnboundLocalError
    
    # Check deployment mode
    deployment_mode = os.environ.get("DEPLOYMENT_MODE", "standalone")
    
    if deployment_mode == "saas":
        # In SaaS mode, get credentials from request payload
        request_data = request.get_json() or {}
        credentials = request_data.get("credentials", {})
        webhook_url = credentials.get("discord_webhook_url", "")
        property_id = credentials.get("ga4_property_id")
        
        if not property_id:
            return jsonify({"error": "GA4_PROPERTY_ID not provided in request credentials."}), 400
            
        # Override environment variables with SaaS credentials
        os.environ["GA4_PROPERTY_ID"] = str(property_id)
        if webhook_url:
            os.environ["DISCORD_WEBHOOK_URL_MARKETING"] = webhook_url
    else:
        # Standalone mode - use environment variables
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_MARKETING") or os.environ.get("DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return jsonify({"error": "DISCORD_WEBHOOK_URL_MARKETING not set."}), 500
        property_id = os.environ.get("GA4_PROPERTY_ID")
        if not property_id:
            return jsonify({"error": "GA4_PROPERTY_ID not set."}), 500
    
    # Only post initial Discord message if webhook URL is provided and valid
    should_post_to_discord = webhook_url and webhook_url.strip() and not webhook_url.startswith("placeholder") and webhook_url != "placeholder"
    if should_post_to_discord:
        post_to_discord(webhook_url, "# 📊 Weekly Analytics Report on its way...")
    
    questions = [
      "What are the key trends in our website performance over the last 30 days, including user growth, session patterns, and any significant changes?",
      "What are the primary traffic sources (e.g., organic search, direct, referral, paid search, social, email) contributing to total sessions, and what is their respective share?",
      "What is the average session duration and pages per session across all users?",
      "Which pages are the most visited, and how do they contribute to conversions (e.g., product pages, category pages, blog posts)?",
      "Which pages or sections (e.g., blog, product pages, landing pages) drive the most engagement (e.g., time on page, low bounce rate)?",
      "Are there underperforming pages with high traffic but low conversions?",
      "How do blog posts or content pages contribute to conversions (e.g., assisted conversions, last-click conversions)?",
      "Where are new visitors coming from?"
    ]
    
    # Map questions to template keys for robust, deterministic analytics
    question_to_template = {
        questions[0]: "trend_analysis",  # Special handling for trend analysis
        questions[1]: "traffic_sources",
        questions[2]: "session_quality", 
        questions[3]: "top_pages_conversions",
        questions[4]: "engagement_pages",
        questions[5]: "underperforming_pages",
        questions[6]: "blog_conversion",
        questions[7]: "new_visitor_sources"
    }
    
    service = MarketingAnalyticsService(OPENAI_API_KEY)
    
    full_report = ""
    report_data = {
        "questions": [],
        "summary": "",
        "generated_at": datetime.now().isoformat(),
        "total_questions": len(questions)
    }

    # Get the current property ID (could be from SaaS request or environment)
    current_property_id = os.environ.get("GA4_PROPERTY_ID")
    
    for idx, q in enumerate(questions, 1):
        logger.info(f"Processing question {idx}/{len(questions)}: {q}")
        try:
            template_key = question_to_template[q]
            # Use template-driven approach for reliability
            if template_key == "traffic_sources":
                answer = service.answer_traffic_sources()
                raw_data = None  # Traffic sources doesn't need raw data
            elif template_key == "trend_analysis":
                # Special handling for trend analysis using service - use current property ID
                result = service.trend_analysis_service.get_weekly_trend_analysis(current_property_id)
                answer = result["ai_insights"]
                raw_data = result.get("data", {})  # Store trend data
            else:
                # For other templates, use the template service with OpenAI summarization - use current property ID
                raw_data = service.template_service.run_template_query(template_key, current_property_id)
                answer = service.openai_service.format_response_obj(raw_data, q)
            
            # Special handling for underperforming pages: scrape and analyze the page content
            page_content_analysis = None
            underperforming_page_url = None
            max_sessions = 0
            underperforming_metrics_snapshot = ""
            
            if template_key == "underperforming_pages" and raw_data:
                try:
                    print(f"🔍 Processing underperforming pages data for scraping")
                    # Extract the most underperforming page from raw_data
                    
                    # Look for page data in raw_data
                    if isinstance(raw_data, dict) and "rows" in raw_data:
                        print(f"🔍 Found {len(raw_data['rows'])} rows in raw_data")
                        # Build a small metrics snapshot for Discord (top pages by sessions)
                        try:
                            snapshot_rows = sorted(
                                raw_data["rows"],
                                key=lambda r: int((r.get("metric_values") or ["0"])[0] or 0),
                                reverse=True,
                            )[:5]
                            snapshot_lines = []
                            for r in snapshot_rows:
                                metric_values = r.get("metric_values", []) or []
                                dimension_values = r.get("dimension_values", []) or []
                                if len(metric_values) >= 2 and len(dimension_values) >= 2:
                                    sessions = int(metric_values[0])
                                    key_events = int(metric_values[1])
                                    page_path = dimension_values[0]
                                    hostname = dimension_values[1]
                                    page_url = f"https://{hostname}{page_path}" if (hostname and page_path) else (page_path or "")
                                    per_100 = (key_events / sessions * 100) if sessions else 0
                                    snapshot_lines.append(f"- {page_url} — {sessions} sessions, {key_events} key events ({per_100:.1f} per 100 sessions)")
                            if snapshot_lines:
                                underperforming_metrics_snapshot = "\n\n**Metrics snapshot (top pages)**\n" + "\n".join(snapshot_lines)
                        except Exception:
                            underperforming_metrics_snapshot = ""

                        for row in raw_data["rows"]:
                            try:
                                # GA4 returns metric_values and dimension_values as arrays of strings
                                metric_values = row.get("metric_values", [])
                                dimension_values = row.get("dimension_values", [])
                                
                                if len(metric_values) >= 2 and len(dimension_values) >= 2:
                                    sessions = int(metric_values[0])
                                    # Note: template uses keyEvents as the second metric.
                                    # We keep the variable name for minimal changes, but it represents key events.
                                    conversions = int(metric_values[1])
                                    
                                    # Find page with most sessions but low/no key events
                                    if sessions > max_sessions and conversions == 0:
                                        max_sessions = sessions
                                        page_path = dimension_values[0]
                                        hostname = dimension_values[1]
                                        
                                        # Construct full URL
                                        if page_path and hostname:
                                            underperforming_page_url = f"https://{hostname}{page_path}"
                                            print(f"🎯 Found underperforming page: {underperforming_page_url} ({sessions} sessions, 0 key events)")
                            except (ValueError, IndexError, KeyError, TypeError) as e:
                                print(f"⚠️ Error processing row: {e}")
                                continue
                    
                    # If we found an underperforming page, scrape and analyze it
                    if underperforming_page_url:
                        print(f"🔍 Scraping underperforming page: {underperforming_page_url}")
                        page_content_analysis = analyze_page_content(underperforming_page_url)
                        print(f"✅ Page content scraped: {page_content_analysis.get('title', 'Unknown')}")
                        print(f"   - CTAs: {len(page_content_analysis.get('cta_buttons', []))}, Forms: {len(page_content_analysis.get('forms', []))}")
                    else:
                        print(f"⚠️ No underperforming page URL found in raw_data")
                        
                except Exception as e:
                    print(f"⚠️ Failed to scrape underperforming page: {e}")
                    import traceback
                    traceback.print_exc()
                    page_content_analysis = None
            
            # Generate focused recommendation for this specific question
            try:
                # Get target keywords and company context based on deployment mode
                if deployment_mode == "saas":
                    request_data = request.get_json() or {}
                    credentials = request_data.get("credentials", {})
                    target_keywords_str = credentials.get("target_keywords", "")
                    target_keywords = [kw.strip() for kw in target_keywords_str.split(':') if kw.strip()] if target_keywords_str else []
                    
                    company_context = {
                        "name": request_data.get("company_name", "Unknown Company"),
                        "domain": request_data.get("company_domain", ""),
                        "description": "Business website requiring analytics optimization"
                    }
                else:
                    # Standalone mode
                    target_keywords_str = os.environ.get("TARGET_KEYWORDS", "")
                    target_keywords = [kw.strip() for kw in target_keywords_str.split(':') if kw.strip()] if target_keywords_str else []
                    
                    company_context = {
                        "name": "Your Business",
                        "domain": "your-website.com", 
                        "description": "Business website requiring analytics optimization"
                    }
                
                # Generate question-specific recommendation
                recommendation = None
                
                # Special handling for underperforming pages with page content analysis
                if template_key == "underperforming_pages" and page_content_analysis and not page_content_analysis.get('error'):
                    print(f"🔍 Using page content analysis for underperforming pages recommendation")
                    
                    # Extract page name from path for readability
                    page_name = "Homepage" if underperforming_page_url.endswith("//") or underperforming_page_url.split("/")[-1] == "" else underperforming_page_url.split("/")[-1].replace("-", " ").title()
                    if "/" in underperforming_page_url.rstrip("/"):
                        page_path = "/" + "/".join(underperforming_page_url.rstrip("/").split("/")[3:])
                    else:
                        page_path = "/"
                    
                    # Generate page-content-aware recommendation using OpenAI
                    page_analysis_prompt = f"""
You are an expert Digital Marketing Strategist. Analyze this underperforming page and provide ONE specific recommendation.

Page: {page_path} ({page_name})
Full URL: {underperforming_page_url}
Analytics: {max_sessions} sessions, 0 conversions (0% conversion rate)

Page Content Analysis:
- Title: {page_content_analysis.get('title', 'No title')}
- Meta Description: {page_content_analysis.get('meta_description', 'None')}
- H1 Tags: {page_content_analysis.get('seo_elements', {}).get('h1_count', 0)}
- CTA Buttons: {len(page_content_analysis.get('cta_buttons', []))}
- Forms: {len(page_content_analysis.get('forms', []))}
- Contact Info: {page_content_analysis.get('has_contact_info', False)}
- Testimonials: {page_content_analysis.get('ux_elements', {}).get('has_testimonials', False)}
- Social Proof: {page_content_analysis.get('has_social_proof', False)}

Generate ONE recommendation in this EXACT JSON format:
{{{{
  "fact": "Page path/name + what is wrong (include numbers: sessions, CTAs, forms, etc.)",
  "recommendation": "Concrete action to fix it (specific and implementable)",
  "category": "conversion",
  "priority": "high"
}}}}

CRITICAL: The fact MUST start with the page identifier (e.g., "Homepage" or the page path)

EXAMPLES:
{{{{
  "fact": "Homepage (/) has {max_sessions} sessions, 0 conversions, and 0 CTA buttons",
  "recommendation": "Add prominent 'Contact Us' button in hero section",
  "category": "conversion",
  "priority": "high"
}}}}

{{{{
  "fact": "/about-us page has {max_sessions} sessions, 0% conversion rate, no testimonials",
  "recommendation": "Add customer testimonials section below company story",
  "category": "conversion",
  "priority": "high"
}}}}

Return ONLY the JSON, no explanation.
"""
                    
                    try:
                        response = openai.OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
                            model="gpt-4",
                            messages=[{"role": "user", "content": page_analysis_prompt}],
                            max_tokens=200,
                            temperature=0.3
                        )
                        
                        content = response.choices[0].message.content.strip()
                        # Extract JSON from response
                        if "```json" in content:
                            content = content.split("```json")[1].split("```")[0].strip()
                        elif "```" in content:
                            content = content.split("```")[1].split("```")[0].strip()
                        
                        recommendation = json.loads(content)
                        print(f"✅ Page-content-aware recommendation: {recommendation.get('fact', '')[:60]}...")
                    except Exception as e:
                        print(f"⚠️ Failed to generate page-content recommendation: {e}")
                        recommendation = None
                else:
                    # Generate question-specific recommendation based on answer and raw data
                    recommendation_prompt = f"""
You are a marketing analyst. Based on this analytics question and answer, generate ONE specific, actionable recommendation.

Question: {q}

Answer: {answer[:500]}

Raw Data Summary: {str(raw_data)[:300] if raw_data else 'No raw data'}

Generate ONE recommendation in this EXACT JSON format:
{{{{
  "fact": "Specific finding from the data with actual numbers",
  "recommendation": "Concrete, implementable action (max 80 chars)",
  "category": "traffic|content|conversion|seo|engagement",
  "priority": "high|medium|low"
}}}}

CRITICAL REQUIREMENTS:
- Include SPECIFIC NUMBERS from the data in the fact (percentages, counts, durations)
- If discussing specific pages, ALWAYS include the page name/path at the start of the fact
- Make recommendation ACTIONABLE and CONCRETE (not generic)
- Keep recommendation under 80 characters

EXAMPLES:
{{{{
  "fact": "Direct traffic is 62.5% while organic search is only 25% of total sessions",
  "recommendation": "Create 5 SEO-optimized blog posts targeting key product keywords",
  "category": "seo",
  "priority": "high"
}}}}

{{{{
  "fact": "/about-us page has 11 sessions but 0 conversions (0% conversion rate)",
  "recommendation": "Add customer testimonials and clear CTA in about section",
  "category": "conversion",
  "priority": "high"
}}}}

{{{{
  "fact": "Average session duration is 129 seconds vs 135 second industry benchmark",
  "recommendation": "Add FAQ section to high-traffic pages to increase engagement",
  "category": "engagement",
  "priority": "medium"
}}}}

Return ONLY the JSON, no explanation.
"""
                    
                    try:
                        response = openai.OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
                            model="gpt-4",
                            messages=[{"role": "user", "content": recommendation_prompt}],
                            max_tokens=200,
                            temperature=0.3
                        )
                        
                        content = response.choices[0].message.content.strip()
                        # Extract JSON from response
                        if "```json" in content:
                            content = content.split("```json")[1].split("```")[0].strip()
                        elif "```" in content:
                            content = content.split("```")[1].split("```")[0].strip()
                        
                        recommendation = json.loads(content)
                        print(f"✅ Question-specific recommendation: {recommendation.get('fact', '')[:60]}...")
                    except Exception as e:
                        print(f"⚠️ Failed to generate recommendation for question: {e}")
                        recommendation = None
                
            except Exception as e:
                print(f"⚠️ Failed to generate recommendation for question: {e}")
                print(f"⚠️ Full error traceback:")
                import traceback
                traceback.print_exc()
                recommendation = None
            
            message = f"**Q: {q}**\nA: {answer}"
            if template_key == "underperforming_pages" and underperforming_metrics_snapshot:
                message += underperforming_metrics_snapshot
            if should_post_to_discord:
                post_to_discord(webhook_url, message)
            full_report += message + "\n\n"
            
            # Store question and answer in report data with recommendation
            report_data["questions"].append({
                "question": q,
                "answer": answer,
                "template_key": template_key,
                "raw_data": raw_data,
                "recommendation": recommendation  # Store the specific recommendation for this question
            })
            
        except Exception as e:
            error_message = f"Could not answer question '{q}': {e}"
            logger.error(error_message)
            if should_post_to_discord:
                post_to_discord(webhook_url, error_message)
            
            # Store error in report data
            report_data["questions"].append({
                "question": q,
                "answer": f"Error: {str(e)}",
                "template_key": question_to_template.get(q, "unknown"),
                "error": True,
                "raw_data": None
            })

    # Generate summary from individual question recommendations
    try:
        recommendations_text = []
        for question_data in report_data["questions"]:
            rec = question_data.get("recommendation")
            if rec:
                fact = rec.get("fact", "Analytics data available")
                recommendation = rec.get("recommendation", "Review data")
                priority = rec.get("priority", "medium")
                recommendations_text.append(f"• {fact} → {recommendation} (Priority: {priority.title()})")
        
        if recommendations_text:
            summary = "Executive Summary - Key Recommendations:\n\n" + "\n".join(recommendations_text)
        else:
            summary = "Executive Summary: Analytics data processed successfully. Individual recommendations generated for each question."
            
        if should_post_to_discord:
            post_to_discord(webhook_url, f"📊 Enhanced Analytics Summary:\n{summary}")
        
        # Store summary in report data
        report_data["summary"] = summary
        
    except Exception as e:
        logger.error(f"Could not generate summary from recommendations: {e}")
        if should_post_to_discord:
            post_to_discord(webhook_url, "Could not generate summary from recommendations.")
        report_data["summary"] = f"Error generating summary: {str(e)}"

    # Store the report in Google Cloud Storage
    try:
        stored_path = service.storage_service.store_weekly_report(report_data)
        logger.info(f"Weekly report stored successfully at: {stored_path}")
    except Exception as e:
        logger.error(f"Failed to store weekly report: {e}")
        # Don't fail the entire request if storage fails

    # Generate structured recommendations from individual question recommendations for SaaS mode
    if deployment_mode == "saas":
        structured_recommendations = []
        try:
            # Extract structured recommendations from individual question recommendations
            for question_data in report_data["questions"]:
                rec = question_data.get("recommendation")
                if rec:
                    structured_recommendations.append({
                        "title": rec.get("recommendation", "Review Analytics Data"),
                        "description": f"📊 {rec.get('fact', 'Analytics data available')} | Priority: {rec.get('priority', 'medium').title()} | Category: {rec.get('category', 'general').title()}"
                    })
            
            print(f"✅ Generated {len(structured_recommendations)} structured recommendations from individual questions")
            
            # Fallback if no recommendations generated
            if not structured_recommendations:
                structured_recommendations = [{
                    "title": "Analyze Website Performance",
                    "description": "📊 Analytics data collected successfully. Individual question analysis completed. | Priority: High | Category: General"
                }]
                
        except Exception as e:
            logger.error(f"Could not generate structured recommendations: {e}")
            structured_recommendations = [{
                "title": "Review Individual Question Analysis",
                "description": "📊 Analytics data processed. Check individual question recommendations for detailed insights. | Priority: High | Category: General"
            }]
        
        report_data["structured_recommendations"] = structured_recommendations
    
    # Prepare response based on deployment mode
    if deployment_mode == "saas":
        # Return full report data for SaaS consumption wrapped in "report" field
        return jsonify({
            "status": "Weekly report process completed successfully.",
            "stored": True,
            "storage_path": stored_path if 'stored_path' in locals() else None,
            "report": {
                "summary": report_data.get("summary", ""),
                "questions": report_data.get("questions", []),
                "structured_recommendations": structured_recommendations
            }
        })
    else:
        # Standalone mode - minimal response
        return jsonify({
            "status": "Weekly report process completed and sent to Discord.",
            "stored": True,
            "storage_path": stored_path if 'stored_path' in locals() else None
        })

@marketing_bp.route('/mcp/tools/get_stored_reports', methods=['GET'])
def get_stored_reports():
    """Retrieve a list of all available stored weekly reports."""
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        reports = service.storage_service.list_available_reports()
        
        return jsonify({
            "status": "success",
            "reports": reports,
            "total_reports": len(reports)
        })
    except Exception as e:
        logger.error(f"Error retrieving stored reports: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@marketing_bp.route('/mcp/tools/get_latest_report', methods=['GET'])
def get_latest_report():
    """Retrieve the most recent weekly analytics report."""
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        report_data = service.storage_service.get_latest_weekly_report()
        
        if not report_data:
            return jsonify({"error": "No weekly reports found"}), 404
        
        # Create a summary of the report
        summary = service.storage_service.get_report_summary(report_data)
        
        return jsonify({
            "status": "success",
            "report": report_data,
            "summary": summary
        })
    except Exception as e:
        logger.error(f"Error retrieving latest report: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@marketing_bp.route('/mcp/tools/analyze_underperforming_pages', methods=['POST'])
def analyze_underperforming_pages():
    """Analyze underperforming pages from stored reports and suggest improvements."""
    data = request.json or {}
    report_date = data.get('report_date')  # Optional: specific date, otherwise uses latest
    max_pages = data.get('max_pages', 3)  # Limit number of pages to analyze to prevent timeouts
    
    # Get target keywords from environment variable
    target_keywords_str = os.environ.get('TARGET_KEYWORDS', '')
    target_keywords = [kw.strip() for kw in target_keywords_str.split(':') if kw.strip()] if target_keywords_str else []
    
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        
        # Get the report data
        if report_date:
            report_data = service.storage_service.get_weekly_report_by_date(report_date)
            if not report_data:
                return jsonify({"error": f"No report found for date: {report_date}"}), 404
        else:
            report_data = service.storage_service.get_latest_weekly_report()
            if not report_data:
                return jsonify({"error": "No weekly reports found"}), 404
        
        # Extract underperforming pages information
        underperforming_data = []
        page_urls_data = []
        
        for question_data in report_data.get("report", {}).get("questions", []):
            if "underperforming" in question_data.get("question", "").lower():
                underperforming_data.append({
                    "question": question_data.get("question"),
                    "answer": question_data.get("answer")
                })
                
                # Extract page URLs from the raw data if available
                raw_data = question_data.get("raw_data", {})
                if raw_data:
                    extracted_urls = service.storage_service._extract_page_urls_from_raw_data(raw_data)
                    page_urls_data.extend(extracted_urls)
        
        if not underperforming_data:
            return jsonify({
                "status": "success",
                "message": "No underperforming pages data found in the report",
                "underperforming_pages": [],
                "page_urls": []
            })
        
        # Limit the number of pages to analyze to prevent timeouts
        underperforming_pages = [p for p in page_urls_data if p.get('is_underperforming')][:max_pages]
        
        # Post to Discord if webhook is available (marketing channel)
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_MARKETING") or os.environ.get("DISCORD_WEBHOOK_URL")
        discord_messages_sent = 0
        
        if webhook_url:
            # Send header message
            header_message = f"# 🔧 Underperforming Pages Analysis\n\n"
            header_message += f"📅 **Report Analyzed**: {report_data.get('metadata', {}).get('report_date', 'Unknown')} (when report was generated)\n"
            header_message += f"📊 **Pages to Analyze**: {len(underperforming_pages)} (limited to {max_pages} for performance)\n"
            if target_keywords:
                header_message += f"🎯 **Target Keywords**: {', '.join(target_keywords)}\n"
            
            # Metrics snapshot (top pages by sessions) so we can see sessions + key events in Discord
            try:
                snapshot = sorted(page_urls_data, key=lambda p: int(p.get("sessions", 0) or 0), reverse=True)[:8]
                if snapshot:
                    header_message += "\n\n## 📌 Metrics snapshot (top pages)\n"
                    for p in snapshot:
                        page_url = p.get("page_url", "")
                        sessions = int(p.get("sessions", 0) or 0)
                        key_events = int(p.get("key_events", 0) or 0)
                        per_100 = (key_events / sessions * 100) if sessions else 0
                        header_message += f"- {page_url} — {sessions} sessions, {key_events} key events ({per_100:.1f} per 100 sessions)\n"
            except Exception:
                # If snapshot fails, don't break the endpoint
                pass

            header_message += f"\nI'll analyze each page and provide specific improvement suggestions..."
            post_to_discord(webhook_url, header_message)
            discord_messages_sent += 1
            
            # If we have page URLs, analyze each underperforming page individually
            if underperforming_pages:
                for page in underperforming_pages:
                    try:
                        # Analyze the actual page content with timeout
                        page_content = analyze_page_content(page.get('page_url', ''))
                        
                        # Create a detailed analysis prompt using the actual page content
                        page_analysis_prompt = f"""
                        You are an expert Digital Marketing Strategist specializing in Conversion Rate Optimization (CRO), SEO, and User Experience (UX). Your goal is to analyze the provided webpage data and generate actionable recommendations to significantly increase both conversion rates and organic page visits.

                        Page Details:
                        - URL: {page.get('page_url', 'Unknown')}
                        - Sessions: {page.get('sessions', 0)}
                        - Conversions: {page.get('conversions', 0)}
                        - Conversion Rate: {page.get('conversion_rate', 0):.1%}
                        {f"- Target Keywords: {', '.join(target_keywords)}" if target_keywords else ""}

                        Page Content Analysis:
                        - Title: {page_content.get('title', 'No title')}
                        - Meta Description: {page_content.get('meta_description', 'No meta description')}
                        - Main Headings: {[h['text'] for h in page_content.get('headings', [])[:5]]}
                        - CTA Buttons Found: {len(page_content.get('cta_buttons', []))}
                        - Forms Found: {len(page_content.get('forms', []))}
                        - Has Contact Info: {page_content.get('has_contact_info', False)}
                        - Has Social Proof: {page_content.get('has_social_proof', False)}
                        - Word Count: {page_content.get('page_structure', {}).get('word_count', 0)}
                        - Main Content Preview: {page_content.get('text_content', '')[:200]}...

                        Page Structure:
                        - Navigation: {page_content.get('page_structure', {}).get('has_navigation', False)}
                        - Footer: {page_content.get('page_structure', {}).get('has_footer', False)}
                        - Responsive: {page_content.get('page_structure', {}).get('is_responsive', False)}
                        - Paragraphs: {page_content.get('page_structure', {}).get('paragraph_count', 0)}
                        - Lists: {page_content.get('page_structure', {}).get('list_count', 0)}
                        - Breadcrumbs: {page_content.get('page_structure', {}).get('has_breadcrumbs', False)}
                        - Search: {page_content.get('page_structure', {}).get('has_search', False)}

                        SEO Elements:
                        - Title Length: {page_content.get('seo_elements', {}).get('title_length', 0)} chars
                        - Meta Description Length: {page_content.get('seo_elements', {}).get('meta_desc_length', 0)} chars
                        - H1 Count: {page_content.get('seo_elements', {}).get('h1_count', 0)}
                        - H2 Count: {page_content.get('seo_elements', {}).get('h2_count', 0)}
                        - H3 Count: {page_content.get('seo_elements', {}).get('h3_count', 0)}
                        - Internal Links: {page_content.get('seo_elements', {}).get('internal_links', 0)}
                        - External Links: {page_content.get('seo_elements', {}).get('external_links', 0)}
                        - Canonical URL: {page_content.get('seo_elements', {}).get('has_canonical', False)}
                        - Open Graph: {page_content.get('seo_elements', {}).get('has_open_graph', False)}
                        - Schema Markup: {page_content.get('seo_elements', {}).get('has_schema_markup', False)}
                        {f"- Keyword Analysis: {json.dumps(page_content.get('seo_elements', {}).get('keyword_analysis', {}), indent=2)}" if target_keywords and page_content.get('seo_elements', {}).get('keyword_analysis') else ""}

                        UX Elements:
                        - Hero Section: {page_content.get('ux_elements', {}).get('has_hero_section', False)}
                        - Testimonials: {page_content.get('ux_elements', {}).get('has_testimonials', False)}
                        - Pricing: {page_content.get('ux_elements', {}).get('has_pricing', False)}
                        - FAQ: {page_content.get('ux_elements', {}).get('has_faq', False)}
                        - Newsletter Signup: {page_content.get('ux_elements', {}).get('has_newsletter_signup', False)}
                        - Live Chat: {page_content.get('ux_elements', {}).get('has_live_chat', False)}

                        Performance Indicators:
                        - Total Images: {page_content.get('performance_indicators', {}).get('total_images', 0)}
                        - Images Without Alt: {page_content.get('performance_indicators', {}).get('images_without_alt', 0)}
                        - Total Links: {page_content.get('performance_indicators', {}).get('total_links', 0)}
                        - Inline Styles: {page_content.get('performance_indicators', {}).get('inline_styles', 0)}
                        - External Scripts: {page_content.get('performance_indicators', {}).get('external_scripts', 0)}

                        Based on this comprehensive analysis of the actual page content, please provide:

                        ## 🎯 CONVERSION RATE OPTIMIZATION (CRO)
                        1. **Critical Issues**: What are the top 3-5 conversion killers on this page?
                        2. **CTA Optimization**: Specific improvements for calls-to-action (placement, copy, design)
                        3. **Trust & Credibility**: How to build trust and reduce friction
                        4. **Value Proposition**: How to make the value clearer and more compelling
                        5. **User Journey**: Optimize the path from visitor to conversion

                        ## 🔍 SEARCH ENGINE OPTIMIZATION (SEO)
                        1. **On-Page SEO**: Title, meta description, headings, and content optimization
                        {f"2. **Keyword Strategy**: How well does this page target the keywords '{', '.join(target_keywords)}'? What specific improvements are needed for these keywords?" if target_keywords else "2. **Keyword Strategy**: Target keywords and content gaps"}
                        3. **Technical SEO**: Page speed, mobile-friendliness, and technical issues
                        4. **Content Quality**: How to improve content depth and relevance
                        5. **Internal Linking**: Opportunities for better site structure

                        ## 👥 USER EXPERIENCE (UX)
                        1. **Visual Hierarchy**: How to improve content flow and readability
                        2. **Mobile Experience**: Mobile-specific optimizations
                        3. **Page Speed**: Performance improvements
                        4. **Accessibility**: Making the page more accessible
                        5. **User Intent**: Aligning content with user expectations

                        ## 📊 PRIORITY ACTION PLAN
                        - **High Priority** (Quick wins with high impact)
                        - **Medium Priority** (Moderate effort, good impact)
                        - **Low Priority** (Long-term improvements)

                        ## 🎯 EXPECTED IMPACT
                        - Specific metrics improvements to expect
                        - Timeline for implementation
                        - Resource requirements

                        Focus on practical, implementable recommendations based on the actual page content. Be specific about what to change, add, or remove. Provide concrete examples and actionable steps.
                        """
                        
                        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
                        page_analysis_response = openai_client.chat.completions.create(
                            model="gpt-4",
                            messages=[{"role": "user", "content": page_analysis_prompt}],
                            max_tokens=800,
                            temperature=0.7,
                            timeout=30  # Increased timeout for OpenAI call
                        )
                        page_analysis = page_analysis_response.choices[0].message.content.strip()
                        
                        # Create a formatted Discord message for this page
                        page_message = f"## 📄 {page.get('page_url', 'Unknown Page')}\n\n"
                        page_message += f"📊 **Analytics**: {page.get('sessions', 0)} sessions, {page.get('conversions', 0)} conversions ({page.get('conversion_rate', 0):.1%})\n"
                        page_message += f"📝 **Page Title**: {page_content.get('title', 'No title')}\n"
                        page_message += f"🔗 **CTAs Found**: {len(page_content.get('cta_buttons', []))} | Forms: {len(page_content.get('forms', []))}\n"
                        page_message += f"📈 **SEO Score**: H1: {page_content.get('seo_elements', {}).get('h1_count', 0)}, Internal Links: {page_content.get('seo_elements', {}).get('internal_links', 0)}\n"
                        page_message += f"⚡ **Performance**: Images: {page_content.get('performance_indicators', {}).get('total_images', 0)}, Missing Alt: {page_content.get('performance_indicators', {}).get('images_without_alt', 0)}\n\n"
                        page_message += f"🎯 **Expert Analysis & Recommendations**:\n\n{page_analysis}\n\n"
                        page_message += "---\n💡 *This analysis is based on actual page content analysis by an expert Digital Marketing Strategist*"
                        
                        post_to_discord(webhook_url, page_message)
                        discord_messages_sent += 1
                        
                        # Add a small delay between messages to avoid rate limiting
                        time.sleep(1)
                        
                    except Exception as e:
                        logger.error(f"Error analyzing page {page.get('page_url')}: {e}")
                        
                        # Create clear error message with guidance on fixing coupling issues
                        error_message = f"## ❌ Page Analysis Failed: {page.get('page_url', 'Unknown Page')}\n\n"
                        error_message += f"📊 **Analytics Data Available**: {page.get('sessions', 0)} sessions, {page.get('conversions', 0)} conversions ({page.get('conversion_rate', 0):.1%})\n"
                        error_message += f"⚠️ **Analysis Status**: FAILED - Insufficient data for expert recommendations\n"
                        error_message += f"🔍 **Error Details**: {str(e)}\n\n"
                        
                        error_message += f"## 🚫 No Recommendations Available\n\n"
                        error_message += f"**Why no recommendations?**\n"
                        error_message += f"• Page content analysis failed due to timeout/access issues\n"
                        error_message += f"• Without actual page content, recommendations would be generic and not actionable\n"
                        error_message += f"• Expert analysis requires real data to provide specific, implementable improvements\n\n"
                        
                        error_message += f"## 🔧 How to Fix This Issue\n\n"
                        error_message += f"**Immediate Actions**:\n"
                        error_message += f"1. **Check Website Performance**: The site may be slow or experiencing issues\n"
                        error_message += f"2. **Verify URL Accessibility**: Ensure the page is publicly accessible\n"
                        error_message += f"3. **Check for Blocking**: The site may be blocking automated requests\n"
                        error_message += f"4. **Test Manually**: Visit the page manually to confirm it loads\n\n"
                        
                        error_message += f"**Technical Solutions**:\n"
                        error_message += f"• **Improve Site Speed**: Optimize page load times (current timeout: 10 seconds)\n"
                        error_message += f"• **Remove Bot Blocking**: Allow legitimate analysis tools\n"
                        error_message += f"• **Fix Server Issues**: Resolve any server-side problems\n"
                        error_message += f"• **Update DNS**: Ensure proper domain resolution\n"
                        error_message += f"• **CDN Optimization**: Use a CDN to improve global response times\n\n"
                        
                        error_message += f"**Alternative Analysis**:\n"
                        error_message += f"• **Manual Review**: Analyze the page manually using the analytics data\n"
                        error_message += f"• **Retry Later**: The issue may be temporary\n"
                        error_message += f"• **Contact Support**: If the issue persists\n\n"
                        
                        error_message += f"---\n💡 *Expert recommendations require actual page content analysis. Generic advice without real data is not actionable.*"
                        
                        post_to_discord(webhook_url, error_message)
                        discord_messages_sent += 1
            else:
                # If no page URLs extracted, send a general analysis
                general_analysis_prompt = f"""
                You are an expert Digital Marketing Strategist specializing in Conversion Rate Optimization (CRO), SEO, and User Experience (UX). Your goal is to analyze the provided analytics data and generate actionable recommendations to significantly increase both conversion rates and organic page visits.

                Underperforming Pages Data:
                {json.dumps(underperforming_data, indent=2)}

                Based on this analytics data, please provide:

                ## 🎯 CONVERSION RATE OPTIMIZATION (CRO)
                1. **Critical Issues**: What are the most common conversion killers across underperforming pages?
                2. **CTA Strategy**: How to optimize calls-to-action for better conversion rates
                3. **Trust Building**: Strategies to build credibility and reduce friction
                4. **Value Communication**: How to make value propositions clearer and more compelling
                5. **User Journey Optimization**: Improve the path from visitor to conversion

                ## 🔍 SEARCH ENGINE OPTIMIZATION (SEO)
                1. **Content Strategy**: How to improve content quality and relevance
                2. **Keyword Optimization**: Target keyword opportunities and content gaps
                3. **Technical Improvements**: Page speed, mobile-friendliness, and technical SEO
                4. **On-Page SEO**: Title, meta description, and heading optimization
                5. **Site Structure**: Internal linking and navigation improvements

                ## 👥 USER EXPERIENCE (UX)
                1. **Visual Design**: How to improve visual hierarchy and readability
                2. **Mobile Experience**: Mobile-specific optimization strategies
                3. **Performance**: Page speed and loading time improvements
                4. **Accessibility**: Making pages more accessible to all users
                5. **User Intent Alignment**: Better matching content with user expectations

                ## 📊 PRIORITY ACTION PLAN
                - **High Priority** (Quick wins with high impact)
                - **Medium Priority** (Moderate effort, good impact)
                - **Low Priority** (Long-term improvements)

                ## 🎯 EXPECTED IMPACT
                - Specific metrics improvements to expect
                - Timeline for implementation
                - Resource requirements

                Format your response as a structured analysis with clear action items. Focus on practical recommendations that a solo founder or small team can implement.
                """
                
                openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
                general_analysis_response = openai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": general_analysis_prompt}],
                    max_tokens=800,
                    temperature=0.7,
                    timeout=30  # Increased timeout for OpenAI call
                )
                general_analysis = general_analysis_response.choices[0].message.content.strip()
                
                general_message = f"## 📊 General Underperforming Pages Analysis\n\n"
                general_message += f"🎯 **Analysis & Recommendations**:\n\n{general_analysis}\n\n"
                general_message += "---\n💡 *This analysis is based on conversion data and best practices*"
                
                post_to_discord(webhook_url, general_message)
                discord_messages_sent += 1
        
        return jsonify({
            "status": "success",
            "report_date": report_data.get("metadata", {}).get("report_date"),
            "underperforming_pages": underperforming_data,
            "page_urls": page_urls_data,
            "pages_analyzed": len(underperforming_pages),
            "max_pages_limit": max_pages,
            "discord_posted": webhook_url is not None,
            "discord_messages_sent": discord_messages_sent
        })
        
    except Exception as e:
        logger.error(f"Error analyzing underperforming pages: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@marketing_bp.route('/mcp/tools/cleanup_old_reports', methods=['POST'])
def cleanup_old_reports():
    """Clean up old weekly reports to manage storage costs."""
    data = request.json or {}
    keep_days = data.get('keep_days', 30)  # Default to keeping 30 days
    max_reports_to_delete = data.get('max_reports_to_delete', 50)  # Limit to prevent timeouts
    
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        deleted_count = service.storage_service.delete_old_reports(keep_days, max_reports_to_delete)
        
        return jsonify({
            "status": "success",
            "deleted_reports": deleted_count,
            "keep_days": keep_days,
            "max_reports_to_delete": max_reports_to_delete,
            "message": f"Cleaned up {deleted_count} old reports, keeping reports from the last {keep_days} days"
        })
    except Exception as e:
        logger.error(f"Error cleaning up old reports: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


def post_long_to_discord(webhook_url, message, chunk_size: int = 1900):
    """
    Post message to Discord; if over 2000 chars, split into multiple messages (like LinkedIn/product flow).
    Discord limit is 2000; we use chunk_size to leave margin and try to split on newlines.
    """
    if not webhook_url or webhook_url.strip() == "" or webhook_url.startswith("placehoder"):
        return
    msg = (message or "").strip()
    if not msg:
        return
    if len(msg) <= 2000:
        post_to_discord(webhook_url, msg)
        return
    start = 0
    while start < len(msg):
        end = min(start + chunk_size, len(msg))
        # Prefer splitting on a newline for readability
        nl = msg.rfind("\n", start, end)
        if nl > start + 200:
            end = nl + 1
        post_to_discord(webhook_url, msg[start:end].strip())
        start = end


def post_to_discord(webhook_url, message: str):
    """
    Post a single message to Discord, truncating hard at 2000 characters.

    NOTE: For longer, multi-part messages use post_long_to_discord instead.
    """
    # Skip Discord posting if webhook URL is empty, not provided, or placeholder
    if (
        not webhook_url
        or webhook_url.strip() == ""
        or webhook_url.strip().lower().startswith("placeholder")
    ):
        logger.info("Discord webhook URL not provided or is placeholder, skipping Discord notification")
        return

    if len(message) > 2000:
        message = message[:1997] + "..."
    data = {"content": message}
    try:
        response = requests.post(
            webhook_url,
            json=data,
            timeout=DISCORD_HTTP_TIMEOUT,
        )
        if response.status_code != 204:
            logger.error(f"Failed to post to Discord: {response.status_code}, {response.text}")
        else:
            logger.info("Successfully posted to Discord")
    except Exception as e:
        logger.error(f"Error posting to Discord: {e}")


def post_long_to_discord(webhook_url: str, text: str, chunk_size: int = 1900):
    """
    Post a long markdown-like text to Discord, splitting it into multiple
    messages that respect Discord's 2000 character limit.

    Splits on newline boundaries where possible to keep sections readable.
    """
    if not webhook_url or webhook_url.strip() == "" or webhook_url.startswith("placehoder"):
        logger.info("Discord webhook URL not provided or is placeholder, skipping Discord notification")
        return

    lines = text.split("\n")
    parts = []
    current_lines = []
    current_len = 0

    for line in lines:
        # +1 accounts for the newline we'll reinsert
        projected = current_len + len(line) + 1
        if projected > chunk_size and current_lines:
            parts.append("\n".join(current_lines))
            current_lines = [line]
            current_len = len(line) + 1
        else:
            current_lines.append(line)
            current_len += len(line) + 1

    if current_lines:
        parts.append("\n".join(current_lines))

    for part in parts:
        post_to_discord(webhook_url, part)

@marketing_bp.route('/openapi.json', methods=['GET'])
def openapi_spec():
    # This should also be dynamically generated in a mature version
    try:
        return send_file('openapi.json', mimetype='application/json')
    except FileNotFoundError:
        return jsonify({"error": "openapi.json not found."}), 404

def analyze_page_content(page_url: str) -> Dict[str, Any]:
    """
    Scrape and analyze a web page for conversion optimization opportunities.
    
    Args:
        page_url: The URL of the page to analyze
        
    Returns:
        Dictionary containing page analysis data
    """
    try:
        # Validate URL format
        if not page_url.startswith(('http://', 'https://')):
            return {
                'url': page_url,
                'error': 'Invalid URL format. Must start with http:// or https://',
                'title': 'Error analyzing page',
                'meta_description': '',
                'headings': [],
                'cta_buttons': [],
                'forms': [],
                'images': [],
                'text_content': '',
                'has_contact_info': False,
                'has_social_proof': False,
                'page_structure': {}
            }
        
        # Basic URL security check
        if len(page_url) > 500:  # Prevent extremely long URLs
            return {
                'url': page_url,
                'error': 'URL too long',
                'title': 'Error analyzing page',
                'meta_description': '',
                'headings': [],
                'cta_buttons': [],
                'forms': [],
                'images': [],
                'text_content': '',
                'has_contact_info': False,
                'has_social_proof': False,
                'page_structure': {}
            }
        
        # Fetch the page content with shorter timeout and better error handling
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        try:
            response = requests.get(page_url, headers=headers, timeout=10)  # Increased timeout to 10 seconds
            response.raise_for_status()
        except requests.exceptions.Timeout:
            return {
                'url': page_url,
                'error': 'Page request timed out - website may be slow or blocking requests',
                'title': 'Timeout Error',
                'meta_description': '',
                'headings': [],
                'cta_buttons': [],
                'forms': [],
                'images': [],
                'text_content': '',
                'has_contact_info': False,
                'has_social_proof': False,
                'page_structure': {},
                'seo_elements': {},
                'ux_elements': {},
                'performance_indicators': {}
            }
        except requests.exceptions.RequestException as e:
            return {
                'url': page_url,
                'error': f'Failed to fetch page: {str(e)}',
                'title': 'Fetch Error',
                'meta_description': '',
                'headings': [],
                'cta_buttons': [],
                'forms': [],
                'images': [],
                'text_content': '',
                'has_contact_info': False,
                'has_social_proof': False,
                'page_structure': {},
                'seo_elements': {},
                'ux_elements': {},
                'performance_indicators': {}
            }
        
        # Check content size to prevent memory issues (reduced limit)
        if len(response.content) > 2 * 1024 * 1024:  # 2MB limit (reduced from 5MB)
            return {
                'url': page_url,
                'error': 'Page content too large (max 2MB)',
                'title': 'Error analyzing page',
                'meta_description': '',
                'headings': [],
                'cta_buttons': [],
                'forms': [],
                'images': [],
                'text_content': '',
                'has_contact_info': False,
                'has_social_proof': False,
                'page_structure': {}
            }
        
        # Parse the HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract key elements for analysis
        analysis = {
            'url': page_url,
            'title': soup.title.string if soup.title else 'No title found',
            'meta_description': '',
            'headings': [],
            'cta_buttons': [],
            'forms': [],
            'images': [],
            'text_content': '',
            'has_contact_info': False,
            'has_social_proof': False,
            'page_structure': {},
            'seo_elements': {},
            'ux_elements': {},
            'performance_indicators': {}
        }
        
        # Extract meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            analysis['meta_description'] = meta_desc.get('content', '')[:200]  # Limit length
        
        # Extract SEO elements
        analysis['seo_elements'] = {
            'has_canonical': bool(soup.find('link', attrs={'rel': 'canonical'})),
            'has_robots_meta': bool(soup.find('meta', attrs={'name': 'robots'})),
            'has_open_graph': bool(soup.find('meta', attrs={'property': 'og:title'})),
            'has_twitter_card': bool(soup.find('meta', attrs={'name': 'twitter:card'})),
            'has_schema_markup': bool(soup.find(attrs={'itemtype': True})),
            'title_length': len(analysis['title']),
            'meta_desc_length': len(analysis['meta_description']),
            'h1_count': len(soup.find_all('h1')),
            'h2_count': len(soup.find_all('h2')),
            'h3_count': len(soup.find_all('h3')),
            'internal_links': len([a for a in soup.find_all('a', href=True) if not a['href'].startswith(('http://', 'https://'))]),
            'external_links': len([a for a in soup.find_all('a', href=True) if a['href'].startswith(('http://', 'https://'))])
        }
        
        # Add keyword analysis if target keywords are configured
        target_keywords_str = os.environ.get('TARGET_KEYWORDS', '')
        target_keywords = [kw.strip() for kw in target_keywords_str.split(':') if kw.strip()] if target_keywords_str else []
        
        if target_keywords:
            page_text = soup.get_text().lower()
            title_text = analysis['title'].lower()
            meta_text = analysis['meta_description'].lower()
            
            keyword_analysis = {}
            for keyword in target_keywords:
                keyword_lower = keyword.lower()
                keyword_analysis[keyword] = {
                    'in_title': keyword_lower in title_text,
                    'in_meta': keyword_lower in meta_text,
                    'in_content': keyword_lower in page_text,
                    'title_position': title_text.find(keyword_lower) if keyword_lower in title_text else -1,
                    'meta_position': meta_text.find(keyword_lower) if keyword_lower in meta_text else -1
                }
            
            analysis['seo_elements']['keyword_analysis'] = keyword_analysis
        
        # Extract headings (limit to first 10)
        for heading in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])[:10]:
            analysis['headings'].append({
                'level': heading.name,
                'text': heading.get_text(strip=True)[:100]  # Limit heading text length
            })
        
        # Extract CTA buttons and links (limit to first 20)
        cta_keywords = ['buy', 'shop', 'order', 'get', 'start', 'sign up', 'subscribe', 'download', 'learn more', 'contact', 'call', 'try', 'demo', 'free', 'now', 'today']
        for link in soup.find_all('a', href=True)[:20]:  # Limit to first 20 links
            link_text = link.get_text(strip=True).lower()
            if any(keyword in link_text for keyword in cta_keywords):
                analysis['cta_buttons'].append({
                    'text': link.get_text(strip=True)[:50],  # Limit text length
                    'href': link.get('href'),
                    'type': 'link',
                    'is_primary': any(primary in link_text for primary in ['buy', 'order', 'sign up', 'start', 'get'])
                })
        
        # Extract buttons (limit to first 10)
        for button in soup.find_all('button')[:10]:
            button_text = button.get_text(strip=True).lower()
            if any(keyword in button_text for keyword in cta_keywords):
                analysis['cta_buttons'].append({
                    'text': button.get_text(strip=True)[:50],  # Limit text length
                    'type': 'button',
                    'is_primary': any(primary in button_text for primary in ['buy', 'order', 'sign up', 'start', 'get'])
                })
        
        # Extract forms (limit to first 5)
        for form in soup.find_all('form')[:5]:
            form_action = form.get('action', '')
            form_method = form.get('method', 'get')
            form_fields = []
            for input_field in form.find_all('input')[:10]:  # Limit fields per form
                field_type = input_field.get('type', 'text')
                field_name = input_field.get('name', '')
                if field_type != 'hidden':
                    form_fields.append({'type': field_type, 'name': field_name})
            
            analysis['forms'].append({
                'action': form_action,
                'method': form_method,
                'fields': form_fields,
                'field_count': len(form_fields)
            })
        
        # Extract images (limit to first 10)
        for img in soup.find_all('img')[:10]:
            src = img.get('src', '')
            alt = img.get('alt', '')
            if src:
                analysis['images'].append({
                    'src': src,
                    'alt': alt[:100],  # Limit alt text length
                    'has_alt': bool(alt.strip())
                })
        
        # Extract main text content (reduced limit)
        main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|main|body'))
        if main_content:
            analysis['text_content'] = main_content.get_text(strip=True)[:500]  # Reduced from 1000 chars
        else:
            # Fallback to body text
            body = soup.find('body')
            if body:
                analysis['text_content'] = body.get_text(strip=True)[:500]  # Reduced from 1000 chars
        
        # Check for contact information (use smaller text sample)
        contact_patterns = [r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b']
        page_text = soup.get_text()[:5000]  # Limit text for pattern matching
        analysis['has_contact_info'] = any(re.search(pattern, page_text) for pattern in contact_patterns)
        
        # Check for social proof elements (use smaller text sample)
        social_proof_keywords = ['testimonial', 'review', 'customer', 'client', 'trust', 'certified', 'award', 'rating', 'star', 'verified', 'guarantee', 'money-back']
        analysis['has_social_proof'] = any(keyword in page_text.lower() for keyword in social_proof_keywords)
        
        # Analyze page structure
        analysis['page_structure'] = {
            'has_navigation': bool(soup.find('nav')),
            'has_footer': bool(soup.find('footer')),
            'has_sidebar': bool(soup.find('aside')),
            'is_responsive': 'viewport' in str(soup.find('meta', attrs={'name': 'viewport'})),
            'word_count': len(page_text.split()),
            'paragraph_count': len(soup.find_all('p')),
            'list_count': len(soup.find_all(['ul', 'ol'])),
            'has_breadcrumbs': bool(soup.find(attrs={'class': re.compile(r'breadcrumb', re.I)})),
            'has_search': bool(soup.find('input', attrs={'type': 'search'})) or bool(soup.find(attrs={'class': re.compile(r'search', re.I)}))
        }
        
        # UX Elements analysis
        analysis['ux_elements'] = {
            'has_hero_section': bool(soup.find(attrs={'class': re.compile(r'hero|banner|header', re.I)})),
            'has_testimonials': bool(soup.find(attrs={'class': re.compile(r'testimonial|review', re.I)})),
            'has_pricing': bool(soup.find(attrs={'class': re.compile(r'pricing|price', re.I)})),
            'has_faq': bool(soup.find(attrs={'class': re.compile(r'faq|question', re.I)})),
            'has_newsletter_signup': bool(soup.find(attrs={'class': re.compile(r'newsletter|subscribe', re.I)})),
            'has_live_chat': bool(soup.find(attrs={'class': re.compile(r'chat|support', re.I)})),
            'has_progress_indicators': bool(soup.find(attrs={'class': re.compile(r'progress|step', re.I)})),
            'has_loading_states': bool(soup.find(attrs={'class': re.compile(r'loading|spinner', re.I)}))
        }
        
        # Performance indicators
        analysis['performance_indicators'] = {
            'total_images': len(soup.find_all('img')),
            'images_without_alt': len([img for img in soup.find_all('img') if not img.get('alt', '').strip()]),
            'total_links': len(soup.find_all('a')),
            'broken_links': 0,  # Would need to check each link
            'inline_styles': len(soup.find_all(attrs={'style': True})),
            'external_scripts': len(soup.find_all('script', attrs={'src': True})),
            'has_compression_hints': 'gzip' in str(soup) or 'deflate' in str(soup),
            'has_caching_hints': bool(soup.find('meta', attrs={'http-equiv': 'cache-control'}))
        }
        
        return analysis
        
    except Exception as e:
        logger.error(f"Error analyzing page {page_url}: {e}")
        return {
            'url': page_url,
            'error': str(e),
            'title': 'Error analyzing page',
            'meta_description': '',
            'headings': [],
            'cta_buttons': [],
            'forms': [],
            'images': [],
            'text_content': '',
            'has_contact_info': False,
            'has_social_proof': False,
            'page_structure': {}
        }
