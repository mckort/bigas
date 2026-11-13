"""Money tiles for the cockpit: month-to-date books per company and AI spend.

Slow sources (Accounted, usage providers such as BigQuery billing), so the
cockpit loads this separately from ``/api/cockpit`` and the result is cached
for ``CACHE_SECONDS``. Each source fails on its own; the tile shows the error.

Books need ``ACCOUNTED_API_KEY`` and ``ACCOUNTED_CFO_COMPANIES`` (one
``Label:company-uuid`` per line). AI spend uses ``BIGAS_USAGE_PROVIDERS``.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CACHE_SECONDS = 15 * 60
_cache: Dict[str, Any] = {"at": 0.0, "value": None}
_lock = threading.Lock()


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


_PROVIDER_LABELS = {
    "cursor": "Cursor",
    "llm_logs": "LLM logs",
    "tavily": "Tavily",
    "gcp_billing": "Google Cloud",
}
_APP_LABELS = {
    "bigas": "Bigas",
    "vcfieldassistant": "VC Field Assistant",
}
_TIER_LABELS = {
    "judgment": "Pro",
    "helper": "Flash",
}
_FEATURE_LABELS = {
    "cto_pr_review": "PR review",
    "cto_autofix": "Autofix",
    "llm.living_analysis": "Living analysis",
    "llm.analysis": "Company analysis",
}
_FEATURE_LIMIT = 12


def _label(name: str, known: Dict[str, str]) -> str:
    key = str(name or "").strip()
    if key in known:
        return known[key]
    text = key.replace(".", " · ").replace("_", " ")
    return text[:1].upper() + text[1:] if text else "Other"


def _rows(mapping: Any, *, known: Dict[str, str], calls: Optional[Dict[str, int]] = None, limit: Optional[int] = None):
    rows = []
    if not isinstance(mapping, dict):
        return rows, None
    for name, value in mapping.items():
        amount = _num(value)
        if amount is None or abs(amount) < 0.005:
            continue
        row: Dict[str, Any] = {"name": str(name), "label": _label(str(name), known), "amount": amount}
        if calls is not None and calls.get(str(name)):
            row["calls"] = calls[str(name)]
        rows.append(row)
    rows.sort(key=lambda row: -abs(row["amount"]))
    more = None
    if limit is not None and len(rows) > limit:
        rest = rows[limit:]
        rows = rows[:limit]
        more = {"count": len(rest), "amount": round(sum(item["amount"] for item in rest), 2)}
    return rows, more


def _event_cost(event: Dict[str, Any]) -> Optional[float]:
    if event.get("est_cost_usd") is None:
        return None
    try:
        return float(event["est_cost_usd"])
    except (TypeError, ValueError):
        return None


def _is_invoice(event: Dict[str, Any]) -> bool:
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    if str(meta.get("cost_kind") or "").strip() == "invoice":
        return True
    return event.get("cost_estimate") is False


def _sum_events(events: list, *, invoice: bool) -> Dict[str, Dict[str, float]]:
    by_provider: Dict[str, float] = {}
    by_feature: Dict[str, float] = {}
    by_app: Dict[str, float] = {}
    by_tier: Dict[str, float] = {}
    for event in events:
        if not isinstance(event, dict) or _is_invoice(event) != invoice:
            continue
        cost = _event_cost(event)
        if cost is None:
            continue
        provider = str(event.get("provider") or "other")
        feature = str(event.get("feature") or "other")
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        app = str(meta.get("app") or "").strip() or "bigas"
        by_provider[provider] = by_provider.get(provider, 0.0) + cost
        by_feature[feature] = by_feature.get(feature, 0.0) + cost
        by_app[app] = by_app.get(app, 0.0) + cost
        tier = str(meta.get("model_tier") or "").strip()
        if tier:
            by_tier[tier] = by_tier.get(tier, 0.0) + cost
    return {"providers": by_provider, "features": by_feature, "apps": by_app, "tiers": by_tier}


def _invoice_lines(events: list) -> list:
    buckets: Dict[tuple, float] = {}
    for event in events:
        if not isinstance(event, dict) or not _is_invoice(event):
            continue
        cost = _event_cost(event)
        if cost is None or abs(cost) < 0.005:
            continue
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        sku = str(meta.get("gcp_sku") or "").strip()
        service = str(meta.get("gcp_service") or "").strip()
        name = sku or service or str(event.get("feature") or "Cloud")
        project = str(meta.get("gcp_project") or "").strip()
        key = (name, project)
        buckets[key] = buckets.get(key, 0.0) + cost
    rows = [
        {"name": name, "label": name, "project": project, "amount": _num(amount)}
        for (name, project), amount in buckets.items()
    ]
    rows.sort(key=lambda row: -abs(row["amount"] or 0))
    return rows[:12]


def _call_counts(raw: Any) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if not isinstance(raw, dict):
        return counts
    for name, value in raw.items():
        try:
            counts[str(name)] = int(value)
        except (TypeError, ValueError):
            continue
    return counts


def _pull_requests(report: Dict[str, Any]) -> list:
    rows = []
    for row in report.get("top_prs") or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("pr_url") or "").strip()
        amount = _num(row.get("est_cost_usd"))
        if not url or amount is None:
            continue
        rows.append({"url": url, "amount": amount})
    return rows[:8]


def _spend_breakdown(report: Dict[str, Any]) -> Dict[str, Any]:
    """List-price lines that add up to the tile, plus the cloud invoice kept apart.

    Provider totals in the usage report mix list price and invoice. The tile
    shows list price only, so the drawer rebuilds that split from events.
    """
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    events = report.get("events") if isinstance(report.get("events"), list) else []
    calls = _call_counts(totals.get("activity_by_feature"))
    if events:
        grouped = _sum_events(events, invoice=False)
        providers, _ = _rows(grouped["providers"], known=_PROVIDER_LABELS)
        features, more = _rows(grouped["features"], known=_FEATURE_LABELS, calls=calls, limit=_FEATURE_LIMIT)
        apps, _ = _rows(grouped["apps"], known=_APP_LABELS)
        tiers, _ = _rows(grouped["tiers"], known=_TIER_LABELS)
        lines = _invoice_lines(events)
    else:
        providers, _ = _rows(totals.get("by_provider"), known=_PROVIDER_LABELS)
        features, more = _rows(
            totals.get("by_feature"), known=_FEATURE_LABELS, calls=calls, limit=_FEATURE_LIMIT
        )
        apps, _ = _rows(totals.get("by_app"), known=_APP_LABELS)
        tiers, _ = _rows(totals.get("by_model_tier"), known=_TIER_LABELS)
        lines = []
    return {
        "providers": providers,
        "features": features,
        "features_more": more,
        "apps": apps,
        "model_tiers": tiers,
        "invoice": {"amount": _num(totals.get("invoice_cost_usd")), "lines": lines},
        "pull_requests": _pull_requests(report),
    }


def _company(label: str, company_id: str, api_key: str, today: date) -> Dict[str, Any]:
    from bigas.resources.cto.usage.accounted_books import _top_costs, fetch_company_books

    try:
        snap = fetch_company_books(company_id, api_key=api_key, today=today)
        if not isinstance(snap, dict):
            raise TypeError("books snapshot was empty")
        raw_current = snap.get("current")
        raw_previous = snap.get("previous")
        current = raw_current if isinstance(raw_current, dict) else {}
        previous = raw_previous if isinstance(raw_previous, dict) else {}
        raw_year = snap.get("year")
        year = raw_year if isinstance(raw_year, dict) else {}
        raw_sections = current.get("expense_sections")
        sections = raw_sections if isinstance(raw_sections, list) else []
        cost_lines = [
            {"account": number, "name": name, "amount": _num(amount)}
            for number, name, amount in _top_costs(sections)
        ]
        month_start = snap.get("month_start")
        if isinstance(month_start, date):
            month = month_start.strftime("%Y-%m")
        elif isinstance(month_start, str) and len(month_start) >= 7 and month_start[4:5] == "-":
            month = month_start[:7]
        else:
            month = ""
        return {
            "label": label,
            "month": month,
            "revenue": _num(current.get("nettoomsattning")),
            "result": _num(current.get("net_result")),
            "costs": _num(current.get("total_expenses")),
            "prev_revenue": _num(previous.get("nettoomsattning")),
            "prev_costs": _num(previous.get("total_expenses")),
            "prev_result": _num(previous.get("net_result")),
            "cash": _num(snap.get("cash_now")),
            "receivables": _num(snap.get("receivables")),
            "payables": _num(snap.get("payables")),
            "cost_lines": cost_lines,
            "year_revenue": _num(year.get("nettoomsattning")),
            "year_costs": _num(year.get("total_expenses")),
            "year_result": _num(year.get("net_result")),
            "unbooked": snap.get("unbooked"),
            "inbox": snap.get("inbox"),
        }
    except Exception as exc:
        logger.warning("Cockpit books failed for %s", label, exc_info=True)
        return {"label": label, "error": str(exc)[:160]}


def _books(today: date) -> Dict[str, Any]:
    from bigas.resources.cto.usage.accounted_books import parse_cfo_companies

    companies = parse_cfo_companies(os.environ.get("ACCOUNTED_CFO_COMPANIES") or "")
    key = (os.environ.get("ACCOUNTED_API_KEY") or "").strip()
    if not companies:
        return {"configured": False, "companies": []}
    if not key:
        return {"configured": False, "companies": [], "error": "ACCOUNTED_API_KEY is not set"}
    with ThreadPoolExecutor(max_workers=min(4, len(companies))) as pool:
        rows = list(pool.map(lambda c: _company(c[0], c[1], key, today), companies))
    return {"configured": True, "companies": rows}


def _ai_spend() -> Dict[str, Any]:
    from bigas.resources.cto.usage.service import enrich_with_prior_period, fetch_ai_usage

    if not (os.environ.get("BIGAS_USAGE_PROVIDERS") or "").strip():
        return {"configured": False}
    try:
        report = enrich_with_prior_period(fetch_ai_usage(days=7))
        if not isinstance(report, dict):
            raise TypeError("AI spend report was empty")
        raw_totals = report.get("totals")
        totals = raw_totals if isinstance(raw_totals, dict) else {}
        raw_prior_period = report.get("prior_period")
        prior_period = raw_prior_period if isinstance(raw_prior_period, dict) else {}
        raw_prior = prior_period.get("totals")
        prior = raw_prior if isinstance(raw_prior, dict) else {}
        week = _num(totals.get("est_cost_usd"))
        before = _num(prior.get("est_cost_usd"))
        change = None
        if week is not None and before:
            change = round((week - before) / before * 100)
        raw_by_provider = totals.get("by_provider")
        by_provider = raw_by_provider if isinstance(raw_by_provider, dict) else {}
        ranked = sorted(
            by_provider.items(),
            key=lambda kv: -(kv[1] or 0) if isinstance(kv[1], (int, float)) else 0,
        )
        raw_errors = report.get("errors") or []
        if not isinstance(raw_errors, list):
            raw_errors = [raw_errors]
        return {
            "configured": True,
            "currency": "USD",
            "week": week,
            "prior_week": before,
            "change_pct": change,
            "top": [{"name": k, "amount": _num(v)} for k, v in ranked[:3]],
            "breakdown": _spend_breakdown(report),
            "errors": [e.get("provider") if isinstance(e, dict) else str(e) for e in raw_errors],
        }
    except Exception as exc:
        logger.warning("Cockpit AI spend failed", exc_info=True)
        return {"configured": True, "error": str(exc)[:160]}


def money_summary(*, refresh: bool = False, today: Optional[date] = None) -> Dict[str, Any]:
    with _lock:
        cached = _cache["value"]
        if cached is not None and not refresh and time.time() - _cache["at"] < CACHE_SECONDS:
            return cached
    day = today or date.today()
    with ThreadPoolExecutor(max_workers=2) as pool:
        books_f = pool.submit(_books, day)
        ai_f = pool.submit(_ai_spend)
        value = {
            "books": books_f.result(),
            "ai": ai_f.result(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    with _lock:
        _cache.update(at=time.time(), value=value)
    return value


def reset_cache_for_tests() -> None:
    with _lock:
        _cache.update(at=0.0, value=None)
