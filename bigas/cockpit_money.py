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
from typing import Any, Dict, List, Optional

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


def _company(label: str, company_id: str, api_key: str, today: date) -> Dict[str, Any]:
    from bigas.resources.cto.usage.accounted_books import fetch_company_books

    try:
        snap = fetch_company_books(company_id, api_key=api_key, today=today)
    except Exception as exc:
        logger.warning("Cockpit books failed for %s", label, exc_info=True)
        return {"label": label, "error": str(exc)[:160]}
    current = snap.get("current") or {}
    previous = snap.get("previous") or {}
    return {
        "label": label,
        "month": snap["month_start"].strftime("%Y-%m") if snap.get("month_start") else "",
        "revenue": _num(current.get("nettoomsattning")),
        "result": _num(current.get("net_result")),
        "costs": _num(current.get("total_expenses")),
        "prev_costs": _num(previous.get("total_expenses")),
        "prev_result": _num(previous.get("net_result")),
        "cash": _num(snap.get("cash_now")),
        "unbooked": snap.get("unbooked"),
        "inbox": snap.get("inbox"),
    }


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
    except Exception as exc:
        logger.warning("Cockpit AI spend failed", exc_info=True)
        return {"configured": True, "error": str(exc)[:160]}
    totals = report.get("totals") or {}
    prior = ((report.get("prior_period") or {}).get("totals") or {})
    week = _num(totals.get("est_cost_usd"))
    before = _num(prior.get("est_cost_usd"))
    change = None
    if week is not None and before:
        change = round((week - before) / before * 100)
    by_provider = sorted((totals.get("by_provider") or {}).items(), key=lambda kv: -kv[1])
    return {
        "configured": True,
        "currency": "USD",
        "week": week,
        "prior_week": before,
        "change_pct": change,
        "top": [{"name": k, "amount": _num(v)} for k, v in by_provider[:3]],
        "errors": [e.get("provider") for e in report.get("errors") or []],
    }


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
