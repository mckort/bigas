"""Month-to-date bookkeeping for the weekly CFO report, from Accounted.

``ACCOUNTED_CFO_COMPANIES`` is plain text, one company per line::

    Scaleup Advisor:<company uuid>
    Green Promo Wear:<company uuid>

The label is the report heading. The uuid is the Accounted company id.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_DEFAULT_API_BASE = "https://app.gnubok.se/api/v1"
_MAX_COMPANY_WORKERS = 4


def _api_base() -> str:
    """REST base for weekly books. CFO chat MCP uses ACCOUNTED_MCP_URL."""
    raw = (os.environ.get("ACCOUNTED_API_BASE") or _DEFAULT_API_BASE).strip()
    return raw.rstrip("/")


_MONTHS_SV = (
    "januari",
    "februari",
    "mars",
    "april",
    "maj",
    "juni",
    "juli",
    "augusti",
    "september",
    "oktober",
    "november",
    "december",
)


def parse_cfo_companies(raw: str) -> List[Tuple[str, str]]:
    """Return (label, company_id) pairs. Blank lines and comments are skipped."""
    companies: List[Tuple[str, str]] = []
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if ":" not in text:
            continue
        label, company_id = text.split(":", 1)
        label = label.strip()
        company_id = company_id.strip()
        if label and company_id:
            companies.append((label, company_id))
    return companies


def format_accounted_books_section(
    *,
    today: Optional[date] = None,
    api_key: Optional[str] = None,
    companies_raw: Optional[str] = None,
) -> Optional[str]:
    """SEK bookkeeping block, or None when no companies are configured."""
    raw = companies_raw if companies_raw is not None else os.environ.get("ACCOUNTED_CFO_COMPANIES") or ""
    companies = parse_cfo_companies(raw)
    if not companies:
        return None
    key = (api_key if api_key is not None else os.environ.get("ACCOUNTED_API_KEY") or "").strip()
    day = today or date.today()
    if not key:
        return _heading(day) + "\nACCOUNTED_API_KEY saknas."
    blocks = [_heading(day)]
    blocks.extend(_company_blocks(companies, api_key=key, today=day))
    return "\n\n".join(blocks)


def _company_blocks(
    companies: List[Tuple[str, str]],
    *,
    api_key: str,
    today: date,
) -> List[str]:
    """Fetch companies together so several books do not stack request latency."""
    if len(companies) == 1:
        label, company_id = companies[0]
        return [_company_block(label, company_id, api_key=api_key, today=today)]

    def one(item: Tuple[str, str]) -> str:
        return _company_block(item[0], item[1], api_key=api_key, today=today)

    workers = min(_MAX_COMPANY_WORKERS, len(companies))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, companies))


def _company_block(label: str, company_id: str, *, api_key: str, today: date) -> str:
    try:
        snap = fetch_company_books(company_id, api_key=api_key, today=today)
        return format_company_books(label, snap)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        logger.warning("Accounted books failed for %s (%s)", label, status)
        if status in (403, 404):
            return f"**{label}**\nAPI-nyckeln når inte det här bolaget."
        return f"**{label}**\nKunde inte läsa bokföringen."
    except Exception:
        logger.warning("Accounted books failed for %s", label, exc_info=True)
        return f"**{label}**\nKunde inte läsa bokföringen."


def fetch_company_books(company_id: str, *, api_key: str, today: date) -> Dict[str, Any]:
    """Read one company's month-to-date books. Raises on transport failure."""
    month_start, month_end, prev_start, prev_end = _windows(today)
    client = _Client(api_key)
    periods_data = client.get(f"/companies/{company_id}/fiscal-periods")
    periods = periods_data.get("fiscal_periods") or periods_data.get("items") or []
    current_bounds = _period_bounds(periods, month_end)
    current_period = current_bounds[0] if current_bounds else None
    year_start = current_bounds[1] if current_bounds else None
    previous_period = _period_id(periods, prev_start)

    current_is = _income(client, company_id, current_period, month_start, month_end)
    previous_is = _income(client, company_id, previous_period, prev_start, prev_end)
    year_is = (
        _income(client, company_id, current_period, year_start, month_end)
        if year_start is not None
        else None
    )
    kpi = _optional(
        client,
        f"/companies/{company_id}/reports/kpi",
        {"period_id": current_period or ""},
    )
    cash_now = _bank_cash(client, company_id, current_period, month_end)
    cash_then = _bank_cash(client, company_id, previous_period, prev_end)
    payables = _optional(
        client,
        f"/companies/{company_id}/reports/supplier-ledger",
        {"as_of_date": month_end.isoformat()},
    )
    unbooked = _count_list(
        client,
        f"/companies/{company_id}/transactions",
        {"status": "unbooked"},
        list_key=None,
    )
    inbox = _count_list(
        client,
        f"/companies/{company_id}/inbox-items",
        {"unprocessed_only": "true"},
        list_key="inbox_items",
    )
    return {
        "month_start": month_start,
        "month_end": month_end,
        "prev_end": prev_end,
        "year_start": year_start,
        "current": current_is,
        "previous": previous_is,
        "year": year_is,
        "receivables": _num((kpi or {}).get("outstandingReceivables")),
        "cash_now": cash_now if cash_now is not None else _num((kpi or {}).get("cashPosition")),
        "cash_then": cash_then,
        "payables": _payables(payables),
        "unbooked": unbooked,
        "inbox": inbox,
    }


def format_company_books(label: str, snap: Dict[str, Any]) -> str:
    current = snap.get("current") or {}
    previous = snap.get("previous") or {}
    lines = [f"**{label}**", _month_heading(snap)]
    lines.extend(
        _pnl_lines(
            current,
            prior_costs=_num(previous.get("total_expenses")),
            prior_label=_month_name(snap["prev_end"]),
            empty_costs="- Inga kostnadskonton den här månaden",
        )
    )
    if snap.get("year_start"):
        lines.append(_year_heading(snap))
        year = snap.get("year")
        if year:
            lines.extend(
                _pnl_lines(
                    year,
                    prior_costs=None,
                    prior_label="",
                    empty_costs="- Inga kostnadskonton hittills i år",
                )
            )
        else:
            lines.append("Årssiffrorna saknas.")
    cash_now = snap.get("cash_now")
    cash_line = "Kassa på banken " + _sek(cash_now)
    cash_line += _change_suffix(cash_now, snap.get("cash_then"), _day_label(snap["prev_end"]))
    lines.append(cash_line)
    lines.append(
        f"Kundfordringar {_sek(snap.get('receivables'))} · "
        f"Leverantörsskulder {_sek(snap.get('payables'))}"
    )
    lines.append(
        f"Obokat: {_count_phrase(snap.get('unbooked'), 'bankrad', 'bankrader')}, "
        f"{_count_phrase(snap.get('inbox'), 'underlag', 'underlag')} i inkorgen"
    )
    return "\n".join(lines)


class _Client:
    def __init__(self, api_key: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    def get(self, path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        response = requests.get(
            _api_base() + path,
            params={k: v for k, v in (params or {}).items() if v},
            headers=self._headers,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
            if meta.get("next_cursor") and "next_cursor" not in data:
                data = {**data, "next_cursor": meta.get("next_cursor")}
            return data
        if isinstance(data, list):
            meta = body.get("meta") if isinstance(body, dict) and isinstance(body.get("meta"), dict) else {}
            return {"items": data, "next_cursor": meta.get("next_cursor")}
        return {}


def _optional(client: _Client, path: str, params: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    if params is not None and not any(params.values()):
        return None
    try:
        return client.get(path, params)
    except Exception:
        logger.warning("Accounted %s failed", path, exc_info=True)
        return None


def _income(
    client: _Client,
    company_id: str,
    period_id: Optional[str],
    start: date,
    end: date,
) -> Optional[Dict[str, Any]]:
    if not period_id:
        return None
    return _optional(
        client,
        f"/companies/{company_id}/reports/income-statement",
        {
            "period_id": period_id,
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
        },
    )


def _bank_cash(
    client: _Client,
    company_id: str,
    period_id: Optional[str],
    as_of: date,
) -> Optional[float]:
    if not period_id:
        return None
    sheet = _optional(
        client,
        f"/companies/{company_id}/reports/balance-sheet",
        {"period_id": period_id, "as_of": as_of.isoformat()},
    )
    if not sheet:
        return None
    total = 0.0
    found = False
    for section in sheet.get("asset_sections") or []:
        for row in section.get("rows") or []:
            number = str(row.get("account_number") or "")
            if number.startswith("19"):
                found = True
                total += _num(row.get("amount")) or 0.0
    return round(total, 2) if found else None


def _count_list(
    client: _Client,
    path: str,
    params: Dict[str, str],
    *,
    list_key: Optional[str],
) -> Optional[int]:
    total = 0
    cursor: Optional[str] = None
    try:
        for _ in range(5):
            query = dict(params)
            query["limit"] = "100"
            if cursor:
                query["cursor"] = cursor
            page = client.get(path, query)
            if list_key:
                rows = page.get(list_key) or page.get("items") or []
            else:
                rows = page.get("items") or []
            total += len(rows)
            cursor = page.get("next_cursor") or None
            if not cursor or len(rows) < 100:
                return total
        return total
    except Exception:
        logger.warning("Accounted count %s failed", path, exc_info=True)
        return None


def _period_id(periods: List[Dict[str, Any]], day: date) -> Optional[str]:
    bounds = _period_bounds(periods, day)
    return bounds[0] if bounds else None


def _period_bounds(periods: List[Dict[str, Any]], day: date) -> Optional[Tuple[str, date, date]]:
    """Return (id, period_start, period_end) for the fiscal period that contains day."""
    stamp = day.isoformat()
    for period in periods:
        start_s = str(period.get("period_start") or "")[:10]
        end_s = str(period.get("period_end") or "")[:10]
        if not (start_s and end_s and start_s <= stamp <= end_s):
            continue
        period_id = str(period.get("id") or "") or None
        if not period_id:
            continue
        try:
            start = date.fromisoformat(start_s)
            end = date.fromisoformat(end_s)
        except ValueError:
            continue
        return period_id, start, end
    return None


def _reporting_day(today: date) -> date:
    """On the 1st, report the completed prior month.

    A one-day month-to-date window would be compared with the full previous month.
    """
    if today.day == 1:
        return today - timedelta(days=1)
    return today


def _windows(today: date) -> Tuple[date, date, date, date]:
    reported = _reporting_day(today)
    month_start = reported.replace(day=1)
    prev_end = month_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    return month_start, reported, prev_start, prev_end


def _payables(payload: Optional[Dict[str, Any]]) -> Optional[float]:
    if not payload:
        return None
    direct = _num(payload.get("total_outstanding"))
    if direct is not None:
        return direct
    return _num((payload.get("totals") or {}).get("total_outstanding"))


def _top_costs(sections: List[Dict[str, Any]]) -> List[Tuple[str, str, float]]:
    rows: List[Tuple[str, str, float]] = []
    for section in sections:
        subtotal = _num(section.get("subtotal")) or 0.0
        # A section that nets to zero is a contra pair (import VAT base and
        # its offset account), not money spent.
        if abs(subtotal) < 0.5:
            continue
        raw_rows = section.get("rows") or []
        has_contra = any((_num(row.get("amount")) or 0) < -0.5 for row in raw_rows)
        if has_contra:
            # Gross lines such as 4535 are reversed inside the section. The
            # subtotal is the net cost.
            if subtotal > 0.5:
                title = str(section.get("title") or "").strip() or "Kostnader"
                rows.append(("", title, subtotal))
            continue
        for row in raw_rows:
            amount = _num(row.get("amount"))
            if amount is None or amount <= 0:
                continue
            rows.append(
                (
                    str(row.get("account_number") or "").strip(),
                    str(row.get("account_name") or "").strip() or "Konto",
                    amount,
                )
            )
    rows.sort(key=lambda item: item[2], reverse=True)
    return rows[:5]


def _pnl_lines(
    statement: Dict[str, Any],
    *,
    prior_costs: Optional[float],
    prior_label: str,
    empty_costs: str,
) -> List[str]:
    revenue = _num(statement.get("nettoomsattning"))
    result = _num(statement.get("net_result"))
    costs = _num(statement.get("total_expenses"))
    lines = [
        f"Intäkter {_sek(revenue)} · Resultat {_sek(result)}",
        "Kostnader " + _sek(costs) + _change_suffix(costs, prior_costs, prior_label),
    ]
    top = _top_costs(statement.get("expense_sections") or [])
    if top:
        for number, name, amount in top:
            prefix = f"{number} {name}" if number else name
            lines.append(f"- {prefix}: {_sek(amount)}")
    else:
        lines.append(empty_costs)
    return lines


def _month_heading(snap: Dict[str, Any]) -> str:
    start = snap["month_start"]
    end = snap["month_end"]
    span = str(end.day) if start.day == end.day else f"{start.day}–{end.day}"
    return f"**{_month_title(end)} ({span})**"


def _year_heading(snap: Dict[str, Any]) -> str:
    return f"**Hittills i år ({_day_label(snap['year_start'])}–{_day_label(snap['month_end'])})**"


def _heading(today: date) -> str:
    reported = _reporting_day(today)
    start = reported.replace(day=1)
    return (
        f"**Bokföring (Accounted), {start.day}–{reported.day} "
        f"{_month_name(reported)} {reported.year}**"
    )


def _month_name(day: date) -> str:
    return _MONTHS_SV[day.month - 1]


def _month_title(day: date) -> str:
    name = _month_name(day)
    return name[:1].upper() + name[1:]


def _day_label(day: date) -> str:
    return f"{day.day} {_month_name(day)}"


def _change_suffix(current: Optional[float], prior: Optional[float], prior_label: str) -> str:
    if current is None or prior is None:
        return ""
    diff = current - prior
    if abs(diff) < 0.5:
        return f" (oförändrat mot {prior_label})"
    direction = "upp" if diff > 0 else "ner"
    return f" ({_sek(abs(diff))} {direction} mot {prior_label})"


def _sek(amount: Optional[float]) -> str:
    if amount is None:
        return "saknas"
    whole = int(round(abs(amount)))
    if whole == 0:
        return "0 kr"
    body = f"{whole:,}".replace(",", " ")
    return ("−" if amount < 0 else "") + body + " kr"


def _count_phrase(count: Optional[int], one: str, many: str) -> str:
    if count is None:
        return f"okänt antal {many}"
    word = one if count == 1 else many
    return f"{count} {word}"


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
