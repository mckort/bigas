"""Live evidence pack for OKR research (brand, GA4, site, repo, board)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from bigas.okr.model import cycle_end_for, parse_iso
from bigas.portfolio import (
    brand_name,
    ga4_property_for_project,
    repo_map,
    site_urls_for_project,
)

logger = logging.getLogger(__name__)

_UA = "bigas-okr-research/1.0"


def resolve_project_key(ticket: Dict[str, Any]) -> str:
    key = str(ticket.get("project_key") or "").strip().upper()
    if key:
        return key
    issue_key = str(ticket.get("key") or "").strip()
    if "-" in issue_key:
        return issue_key.split("-", 1)[0].upper()
    board_id = str(ticket.get("board_id") or "").strip()
    if board_id:
        try:
            from bigas.tickets.store import get_ticket_store

            board = get_ticket_store().get_board(board_id) or {}
            return str(board.get("project_key") or "").strip().upper()
        except Exception:
            logger.warning("Could not resolve board project for OKR context", exc_info=True)
    return ""


def _cycle_window(ticket: Dict[str, Any]) -> Dict[str, str]:
    created = str(ticket.get("created_at") or "")
    cycle = str(ticket.get("okr_cycle") or "").strip()
    end = cycle_end_for(cycle, created_at=created)
    start_dt = parse_iso(created) or datetime.now(timezone.utc)
    end_dt = parse_iso(end) or (start_dt + timedelta(days=90))
    now = datetime.now(timezone.utc)
    remaining = max(0, int((end_dt - now).total_seconds() // 86400))
    total = max(1, int((end_dt - start_dt).total_seconds() // 86400))
    return {
        "cycle_label": cycle or f"{total}-day window from created_at",
        "start": start_dt.date().isoformat(),
        "end": end_dt.date().isoformat(),
        "days_remaining": str(remaining),
        "days_total": str(total),
    }


def _flatten_ga4_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    headers_d = result.get("dimension_headers") or []
    headers_m = result.get("metric_headers") or []
    rows: List[Dict[str, Any]] = []
    for raw in result.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        if "dimension_values" in raw or "metric_values" in raw:
            item: Dict[str, Any] = {}
            for name, val in zip(headers_d, raw.get("dimension_values") or []):
                item[str(name)] = val
            for name, val in zip(headers_m, raw.get("metric_values") or []):
                item[str(name)] = val
            rows.append(item)
        else:
            rows.append(raw)
    return rows


def _ga4_query(
    service: Any,
    *,
    property_id: str,
    metrics: List[str],
    dimensions: List[str],
    start_date: str,
    end_date: str,
    limit: Optional[int] = None,
    order_field: Optional[str] = None,
) -> List[Dict[str, Any]]:
    template: Dict[str, Any] = {"metrics": metrics, "dimensions": dimensions}
    if limit is not None:
        template["limit"] = limit
    if order_field:
        template["order_by"] = [{"field": order_field, "direction": "DESCENDING"}]
    result = service.run_template_query(
        property_id=property_id,
        template=template,
        date_range={"start_date": start_date, "end_date": end_date},
    )
    return _flatten_ga4_rows(result if isinstance(result, dict) else {})


def _sum_metric(rows: List[Dict[str, Any]], name: str) -> float:
    total = 0.0
    for row in rows:
        try:
            total += float(row.get(name) or 0)
        except (TypeError, ValueError):
            continue
    return total


def fetch_ga4_snapshot(project_key: str, *, days: int = 28) -> str:
    prop = ga4_property_for_project(project_key)
    if not prop:
        return "(No GA4 property mapped for this project. Do not invent analytics numbers.)"
    try:
        from bigas.resources.marketing.ga4_service import GA4Service
        from bigas.resources.marketing.utils import get_date_range_strings

        service = GA4Service()
        current_start, current_end = get_date_range_strings(days)
        prev_end_dt = datetime.strptime(current_start, "%Y-%m-%d") - timedelta(days=1)
        prev_start_dt = prev_end_dt - timedelta(days=days - 1)
        prev_start, prev_end = prev_start_dt.strftime("%Y-%m-%d"), prev_end_dt.strftime("%Y-%m-%d")

        lines = [
            f"GA4 property {prop} for {project_key}.",
            f"Current window {current_start} → {current_end}; previous {prev_start} → {prev_end}.",
        ]

        current = _ga4_query(
            service,
            property_id=prop,
            metrics=["sessions", "totalUsers", "screenPageViews"],
            dimensions=["date"],
            start_date=current_start,
            end_date=current_end,
        )
        previous = _ga4_query(
            service,
            property_id=prop,
            metrics=["sessions", "totalUsers", "screenPageViews"],
            dimensions=["date"],
            start_date=prev_start,
            end_date=prev_end,
        )
        lines.append(
            "Traffic: "
            f"sessions {int(_sum_metric(current, 'sessions'))} "
            f"(prev {int(_sum_metric(previous, 'sessions'))}), "
            f"users {int(_sum_metric(current, 'totalUsers'))} "
            f"(prev {int(_sum_metric(previous, 'totalUsers'))}), "
            f"pageviews {int(_sum_metric(current, 'screenPageViews'))} "
            f"(prev {int(_sum_metric(previous, 'screenPageViews'))})."
        )

        extras = [
            (
                "Purchases / revenue",
                ["ecommercePurchases", "purchaseRevenue"],
                ["date"],
                None,
            ),
            (
                "Key events",
                ["keyEvents"],
                ["eventName"],
                "keyEvents",
            ),
            (
                "Engagement",
                ["averageSessionDuration", "engagementRate", "sessions"],
                ["date"],
                None,
            ),
            (
                "Channels",
                ["sessions", "keyEvents"],
                ["sessionDefaultChannelGroup"],
                "sessions",
            ),
            (
                "Top countries",
                ["sessions"],
                ["country"],
                "sessions",
            ),
            (
                "Top pages",
                ["screenPageViews", "sessions"],
                ["pagePath"],
                "screenPageViews",
            ),
        ]
        for label, metrics, dimensions, order_field in extras:
            try:
                rows = _ga4_query(
                    service,
                    property_id=prop,
                    metrics=metrics,
                    dimensions=dimensions,
                    start_date=current_start,
                    end_date=current_end,
                    limit=8,
                    order_field=order_field,
                )
                if not rows:
                    lines.append(f"{label}: no rows (metric may be unconfigured).")
                    continue
                preview = []
                for row in rows[:6]:
                    bits = [f"{k}={v}" for k, v in row.items()]
                    preview.append(", ".join(bits))
                lines.append(f"{label}:")
                lines.extend(f"  - {item}" for item in preview)
            except Exception as exc:
                lines.append(f"{label}: unavailable ({exc}).")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("GA4 snapshot failed for %s: %s", project_key, exc)
        return f"(GA4 unavailable for {project_key}: {exc}. Do not invent analytics numbers.)"


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"(?i)<meta[^>]+(?:name|property)=['\"](?:description|og:title|og:description)['\"][^>]*>", lambda m: f" {m.group(0)} ", text)
    titles = re.findall(r"(?is)<title[^>]*>(.*?)</title>", text)
    metas = re.findall(
        r"(?is)<meta[^>]+(?:name|property)=['\"](?:description|og:title|og:description)['\"][^>]+content=['\"](.*?)['\"]",
        html or "",
    )
    body = re.sub(r"<[^>]+>", " ", text)
    body = re.sub(r"\s+", " ", body).strip()
    parts = []
    if titles:
        parts.append("Title: " + re.sub(r"\s+", " ", titles[0]).strip())
    if metas:
        parts.append("Meta: " + " | ".join(re.sub(r"\s+", " ", m).strip() for m in metas[:4] if m.strip()))
    if body:
        parts.append(body[:3500])
    return "\n".join(parts)


def fetch_site_snapshot(urls: List[str]) -> str:
    if not urls:
        return "(No website mapped for this project.)"
    chunks: List[str] = []
    for url in urls[:3]:
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": _UA},
                timeout=15,
                allow_redirects=True,
            )
            if resp.status_code >= 400:
                chunks.append(f"{url}: HTTP {resp.status_code}")
                continue
            text = _strip_html(resp.text or "")
            chunks.append(f"{url}\n{text[:4000]}")
        except Exception as exc:
            chunks.append(f"{url}: unavailable ({exc})")
    return "\n\n".join(chunks) if chunks else "(Website fetch returned nothing.)"


def fetch_board_snapshot(ticket: Dict[str, Any]) -> str:
    board_id = str(ticket.get("board_id") or "").strip()
    if not board_id:
        return "(No board id on this objective.)"
    try:
        from bigas.tickets.store import get_ticket_store

        tickets = get_ticket_store().list_tickets(board_id)
    except Exception as exc:
        logger.warning("Board snapshot failed: %s", exc)
        return f"(Board tickets unavailable: {exc})"
    lines = []
    self_key = str(ticket.get("key") or "")
    for item in tickets[:25]:
        key = str(item.get("key") or "")
        if key == self_key:
            continue
        itype = str(item.get("issue_type") or "Task")
        status = str(item.get("status") or "")
        title = str(item.get("title") or "")
        lines.append(f"- {key} [{itype}/{status}]: {title}")
    return "\n".join(lines) if lines else "(No other tickets on this board yet.)"


def fetch_repo_snapshot(project_key: str, *, hints: Optional[List[str]] = None) -> str:
    repo = repo_map().get((project_key or "").strip().upper()) or ""
    if not repo:
        return "(No GitHub repo mapped for this project.)"
    try:
        from bigas.resources.product.jira_automation.github_context import GitHubRepoContext

        return GitHubRepoContext().fetch_context(repo, query_hints=hints or [])
    except Exception as exc:
        logger.warning("Repo snapshot failed for %s: %s", repo, exc)
        return f"(GitHub context unavailable: {exc})"


def gather_okr_evidence(ticket: Dict[str, Any]) -> Dict[str, str]:
    """Best-effort evidence pack. Never raises — missing sources become explicit notes."""
    project_key = resolve_project_key(ticket)
    brand = brand_name(project_key) if project_key else "Unknown brand"
    urls = site_urls_for_project(project_key)
    window = _cycle_window(ticket)
    title = str(ticket.get("title") or "").strip()
    brief = str(ticket.get("description") or "").strip()
    hints = [h for h in [title, brand, project_key] if h]

    ga4 = fetch_ga4_snapshot(project_key) if project_key else "(No project key — cannot query GA4.)"
    site = fetch_site_snapshot(urls)
    try:
        from bigas.resources.product.jira_automation.web_research import fetch_web_snippets

        host = ""
        if urls:
            host = re.sub(r"^https?://", "", urls[0]).split("/")[0]
        query = f"{brand} {title}".strip()
        if host:
            query = f"site:{host} {title} {brand}"
        web = fetch_web_snippets(query) or "(No public web snippets.)"
    except Exception as exc:
        web = f"(Web search unavailable: {exc})"

    return {
        "project_key": project_key or "(none)",
        "brand": brand,
        "site_urls": ", ".join(urls) or "(none)",
        "repo": repo_map().get(project_key) or "(none)",
        "cycle_label": window["cycle_label"],
        "cycle_start": window["start"],
        "cycle_end": window["end"],
        "days_remaining": window["days_remaining"],
        "days_total": window["days_total"],
        "title": title,
        "brief": brief or "(empty)",
        "ga4": ga4,
        "website": site,
        "web_snippets": web,
        "repo_context": fetch_repo_snapshot(project_key, hints=hints),
        "board_tickets": fetch_board_snapshot(ticket),
    }


def format_evidence_pack(evidence: Dict[str, str]) -> str:
    return f"""## Brand
Project: {evidence.get('project_key')}
Brand: {evidence.get('brand')}
Website: {evidence.get('site_urls')}
Repo: {evidence.get('repo')}

## Timebox
Cycle: {evidence.get('cycle_label')}
Window: {evidence.get('cycle_start')} → {evidence.get('cycle_end')}
Days remaining: {evidence.get('days_remaining')} of {evidence.get('days_total')}

## Objective
Title: {evidence.get('title')}
Brief:
{evidence.get('brief')}

## Google Analytics
{evidence.get('ga4')}

## Website
{evidence.get('website')}

## Public web snippets
{evidence.get('web_snippets')}

## Repo
{evidence.get('repo_context')}

## Other tickets on this board
{evidence.get('board_tickets')}
"""
