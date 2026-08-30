"""Aggregate AI usage from UsageProvider implementations + Discord helpers."""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bigas.providers.usage.base import UsageEvent, UsageProvider
from bigas.resources.cto.autofix.cursor_client import (
    CursorCloudAgentClient,
    CursorCloudAgentError,
)
from bigas.resources.cto.usage.pricing import (
    CursorTokenUsage,
    default_autofix_model,
    estimate_cursor_cost_usd,
)

logger = logging.getLogger(__name__)


def _registry_usage_providers() -> List[UsageProvider]:
    try:
        from bigas.registry import registry

        return list(registry.get_all("usage") or [])
    except Exception:
        logger.warning("Could not load usage providers from registry", exc_info=True)
        return []


def format_token_counts(
    *,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    cache_read_tokens: Optional[int] = None,
    cache_write_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> str:
    parts: List[str] = []
    if input_tokens is not None:
        parts.append(f"in {input_tokens:,}")
    if output_tokens is not None:
        parts.append(f"out {output_tokens:,}")
    if cache_write_tokens:
        parts.append(f"cacheWrite {cache_write_tokens:,}")
    if cache_read_tokens:
        parts.append(f"cacheRead {cache_read_tokens:,}")
    detail = " / ".join(parts)
    if total_tokens is None:
        total = (input_tokens or 0) + (output_tokens or 0) + (cache_read_tokens or 0) + (
            cache_write_tokens or 0
        )
        if total == 0 and not parts:
            return ""
        total_tokens = total
    if detail:
        return f"{total_tokens:,} tokens ({detail})"
    return f"{total_tokens:,} tokens"


def format_cursor_usage_discord_lines(
    *,
    usage: CursorTokenUsage,
    model: str,
    est_cost_usd: Optional[float],
) -> List[str]:
    token_line = format_token_counts(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens or None,
        cache_write_tokens=usage.cache_write_tokens or None,
        total_tokens=usage.total_tokens or None,
    )
    lines: List[str] = []
    if token_line:
        lines.append(f"Cursor usage: {token_line}")
    if est_cost_usd is not None:
        lines.append(f"Estimated list-price: ~${est_cost_usd:.4f} ({model})")
    elif token_line:
        lines.append(f"Estimated list-price: n/a (unknown model {model})")
    return lines


def fetch_cursor_run_usage(
    *,
    agent_id: str,
    run_id: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    retries: int = 3,
    retry_delay_seconds: float = 1.5,
) -> Dict[str, Any]:
    """
    Fetch Cursor token usage for one agent/run with short retries for settling.

    Never raises — returns ``{"ok": False, ...}`` on failure.
    """
    aid = (agent_id or "").strip()
    if not aid:
        return {"ok": False, "reason": "agent_id missing"}

    key = (api_key or "").strip()
    import os

    key = key or (os.environ.get("CURSOR_API_KEY") or "").strip()
    if not key:
        return {"ok": False, "reason": "CURSOR_API_KEY not set"}

    model_id = (model or "").strip() or default_autofix_model()
    client = CursorCloudAgentClient(api_key=key)
    last_err = ""
    usage = CursorTokenUsage()

    for attempt in range(max(1, retries)):
        try:
            payload = client.get_usage(aid, run_id=run_id)
        except CursorCloudAgentError as e:
            last_err = str(e)
            if attempt + 1 < retries:
                time.sleep(retry_delay_seconds)
            continue

        total = payload.get("totalUsage") or payload.get("total_usage") or {}
        if isinstance(total, dict) and total:
            usage = CursorTokenUsage.from_mapping(total)
        else:
            runs = payload.get("runs") or []
            if isinstance(runs, list) and runs:
                # Prefer matching run_id when present.
                chosen = None
                rid = (run_id or "").strip()
                for run in runs:
                    if isinstance(run, dict) and rid and run.get("id") == rid:
                        chosen = run
                        break
                if chosen is None:
                    chosen = runs[0] if isinstance(runs[0], dict) else None
                if chosen is not None:
                    usage = CursorTokenUsage.from_mapping(chosen.get("usage") or {})

        if usage.total_tokens > 0:
            break
        if attempt + 1 < retries:
            time.sleep(retry_delay_seconds)

    if usage.total_tokens <= 0 and last_err:
        return {"ok": False, "reason": last_err, "model": model_id}

    est = estimate_cursor_cost_usd(model_id, usage)
    return {
        "ok": True,
        "model": model_id,
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "total_tokens": usage.total_tokens,
        },
        "est_cost_usd": est,
        "cost_estimate": est is not None,
        "discord_lines": format_cursor_usage_discord_lines(
            usage=usage, model=model_id, est_cost_usd=est
        ),
    }


def _resolve_providers(
    *,
    provider: str = "all",
    providers: Optional[Sequence[UsageProvider]] = None,
) -> List[UsageProvider]:
    active = list(providers) if providers is not None else _registry_usage_providers()
    want = (provider or "all").strip().lower()
    if want in {"", "all", "*"}:
        return active
    return [p for p in active if p.name == want]


def fetch_ai_usage(
    *,
    days: int = 7,
    provider: str = "all",
    feature_prefix: Optional[str] = None,
    providers: Optional[Sequence[UsageProvider]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    days_n = max(1, min(int(days or 7), 90))
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(days=days_n)

    selected = _resolve_providers(provider=provider, providers=providers)
    events: List[UsageEvent] = []
    errors: List[Dict[str, str]] = []

    for p in selected:
        try:
            events.extend(
                p.fetch_usage(
                    start=start,
                    end=end,
                    feature_prefix=feature_prefix,
                )
            )
        except Exception as e:
            logger.warning("Usage provider %s failed: %s", p.name, e, exc_info=True)
            errors.append({"provider": p.name, "error": str(e)})

    by_provider: Dict[str, float] = defaultdict(float)
    by_feature: Dict[str, float] = defaultdict(float)
    by_app: Dict[str, float] = defaultdict(float)
    by_model_tier: Dict[str, float] = defaultdict(float)
    activity_by_feature: Dict[str, int] = defaultdict(int)
    list_price_total = 0.0
    invoice_total = 0.0
    cost_known = 0
    empty_fallback_events = 0
    empty_response_events = 0
    for ev in events:
        activity_by_feature[ev.feature or "unknown"] += 1
        meta = ev.meta if isinstance(ev.meta, dict) else {}
        if meta.get("empty_fallback") in (True, "true", 1):
            empty_fallback_events += 1
        if meta.get("empty_response") in (True, "true", 1):
            empty_response_events += 1
        if ev.est_cost_usd is None:
            continue
        usd = float(ev.est_cost_usd)
        cost_known += 1
        by_provider[ev.provider] += usd
        by_feature[ev.feature] += usd
        app = str(meta.get("app") or "").strip() or (
            "vcfieldassistant" if str(meta.get("gcp_project") or "") == "vcfieldassistant" else "bigas"
        )
        by_app[app] += usd
        kind = str(meta.get("cost_kind") or "").strip()
        is_invoice = kind == "invoice" or ev.cost_estimate is False
        if is_invoice:
            invoice_total += usd
        else:
            list_price_total += usd
        tier = str(meta.get("model_tier") or "").strip()
        if tier:
            by_model_tier[tier] += usd

    # Top PRs by estimated cost (cursor meta.pr_url).
    pr_costs: Dict[str, float] = defaultdict(float)
    for ev in events:
        pr_url = ""
        if isinstance(ev.meta, dict):
            pr_url = (ev.meta.get("pr_url") or "").strip()
        if pr_url and ev.est_cost_usd is not None:
            pr_costs[pr_url] += float(ev.est_cost_usd)
    top_prs = sorted(pr_costs.items(), key=lambda kv: kv[1], reverse=True)[:10]

    return {
        "success": True,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days_n,
        "providers": [p.name for p in selected],
        "totals": {
            "est_cost_usd": round(list_price_total, 6),
            "list_price_usd": round(list_price_total, 6),
            "invoice_cost_usd": round(invoice_total, 6),
            "events": len(events),
            "events_with_cost": cost_known,
            "by_provider": {k: round(v, 6) for k, v in sorted(by_provider.items())},
            "by_feature": {k: round(v, 6) for k, v in sorted(by_feature.items())},
            "by_app": {k: round(v, 6) for k, v in sorted(by_app.items())},
            "by_model_tier": {k: round(v, 6) for k, v in sorted(by_model_tier.items())},
            "activity_by_feature": dict(sorted(activity_by_feature.items())),
            "empty_fallback_events": empty_fallback_events,
            "empty_response_events": empty_response_events,
        },
        "top_prs": [{"pr_url": u, "est_cost_usd": round(c, 6)} for u, c in top_prs],
        "events": [e.as_dict() for e in events],
        "errors": errors,
    }


# Feature → cost bucket for the executive rollup (unlisted → Ops / other).
_FEATURE_BUCKET_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("Engineering (PR + autofix)", ("cto_pr_review", "cto_autofix")),
    ("VCFA analysis", ("llm.living_analysis", "llm.analysis")),
)
_TAVILY_BUCKET = "Search (Tavily)"
_OTHER_BUCKET = "Ops / chat / other"
_TOP_FEATURES = 5


def _pct(part: float, whole: float) -> str:
    if whole <= 0:
        return "n/a"
    return f"{100.0 * part / whole:.0f}%"


def _money(value: Optional[float], *, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):.{digits}f}"


def _feature_bucket(feature: str) -> str:
    name = (feature or "").strip()
    for label, members in _FEATURE_BUCKET_RULES:
        if name in members:
            return label
    if name.startswith("tavily"):
        return _TAVILY_BUCKET
    return _OTHER_BUCKET


def _bucket_costs(by_feature: Dict[str, float]) -> List[Tuple[str, float]]:
    buckets: Dict[str, float] = defaultdict(float)
    for feature, cost in (by_feature or {}).items():
        buckets[_feature_bucket(feature)] += float(cost or 0)
    order = [
        "Engineering (PR + autofix)",
        "VCFA analysis",
        _TAVILY_BUCKET,
        _OTHER_BUCKET,
    ]
    rows = [(label, buckets[label]) for label in order if buckets.get(label)]
    # Any unexpected labels last.
    for label, cost in sorted(buckets.items(), key=lambda kv: kv[1], reverse=True):
        if label not in order and cost:
            rows.append((label, cost))
    return rows


def _gcp_invoice_status(report: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Return (status_line, short_blocker) for the GCP invoice row."""
    totals = report.get("totals") or {}
    invoice = totals.get("invoice_cost_usd")
    errors = report.get("errors") or []
    gcp_errs = [
        str(err.get("error") or "").strip()
        for err in errors
        if isinstance(err, dict) and err.get("provider") == "gcp_billing"
    ]
    gcp_errs = [e for e in gcp_errs if e]
    if gcp_errs:
        short = gcp_errs[0]
        if len(short) > 160:
            short = short[:157].rstrip() + "…"
        return (
            f"GCP invoice: unavailable — {short}",
            short,
        )
    if invoice is None:
        return ("GCP invoice: n/a", None)
    if float(invoice) > 0:
        return (f"GCP invoice (Cloud Billing export): ~{_money(invoice, digits=4)}", None)
    return ("GCP invoice: $0.00 (no invoice rows in window)", None)


def _wow_line(report: Dict[str, Any]) -> Optional[str]:
    prior = report.get("prior_period") or {}
    prior_totals = prior.get("totals") if isinstance(prior, dict) else None
    if not isinstance(prior_totals, dict):
        return None
    cur = (report.get("totals") or {}).get("est_cost_usd")
    prev = prior_totals.get("est_cost_usd")
    if cur is None or prev is None:
        return None
    cur_f = float(cur)
    prev_f = float(prev)
    days = int(report.get("days") or prior.get("days") or 7)
    if prev_f <= 0:
        return f"vs prior {days}d: {_money(prev_f)} → {_money(cur_f)} (n/a)"
    delta_pct = 100.0 * (cur_f - prev_f) / prev_f
    sign = "+" if delta_pct >= 0 else ""
    return (
        f"vs prior {days}d: {_money(prev_f)} → {_money(cur_f)} "
        f"({sign}{delta_pct:.0f}%)"
    )


def enrich_with_prior_period(
    report: Dict[str, Any],
    *,
    providers: Optional[Sequence[UsageProvider]] = None,
) -> Dict[str, Any]:
    """Attach the previous equal-length window for week-over-week context."""
    if report.get("prior_period"):
        return report
    start_raw = report.get("start")
    if not start_raw:
        return report
    try:
        days = int(report.get("days") or 7)
        start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        prior = fetch_ai_usage(
            days=days,
            provider="all",
            feature_prefix=None,
            providers=providers,
            now=start,
        )
        return {
            **report,
            "prior_period": {
                "start": prior.get("start"),
                "end": prior.get("end"),
                "days": days,
                "totals": prior.get("totals") or {},
            },
        }
    except Exception:
        logger.warning("Could not load prior-period usage for CFO report", exc_info=True)
        return report


def format_weekly_cto_ai_report(report: Dict[str, Any]) -> str:
    """Human-readable weekly usage brief: exec summary first, details below."""
    days = report.get("days") or 7
    totals = report.get("totals") or {}
    est = totals.get("est_cost_usd")
    est_f = float(est) if est is not None else 0.0
    by_feature = {
        str(k): float(v)
        for k, v in (totals.get("by_feature") or {}).items()
    }
    activity = {
        str(k): int(v)
        for k, v in (totals.get("activity_by_feature") or {}).items()
    }
    by_provider = totals.get("by_provider") or {}
    by_app = totals.get("by_app") or {}
    by_model_tier = totals.get("by_model_tier") or {}

    gcp_line, _gcp_blocker = _gcp_invoice_status(report)
    top_sorted = sorted(by_feature.items(), key=lambda kv: kv[1], reverse=True)
    top3 = top_sorted[:3]
    drivers = (
        " · ".join(
            f"{name} {_pct(cost, est_f)} (~{_money(cost)})" for name, cost in top3
        )
        if top3
        else "n/a"
    )

    lines: List[str] = [
        f"**Bigas AI + cloud usage (last {days} days)**",
        (
            f"List-price (LLM + Cursor + Tavily): ~{_money(est_f)} · "
            f"Events: {totals.get('events') or 0}"
            if est is not None
            else f"List-price (LLM + Cursor + Tavily): n/a · Events: {totals.get('events') or 0}"
        ),
        gcp_line,
    ]
    wow = _wow_line(report)
    if wow:
        lines.append(wow)
    lines.append(f"Top drivers: {drivers}")

    if by_app:
        apps = " · ".join(
            f"{name} {_money(cost)}"
            for name, cost in sorted(by_app.items(), key=lambda kv: kv[1], reverse=True)
        )
        lines.append(f"Apps: {apps}")

    buckets = _bucket_costs(by_feature)
    if buckets:
        lines.append("")
        lines.append("By area:")
        for label, cost in buckets:
            lines.append(f"- {label}: ~{_money(cost)} ({_pct(cost, est_f)})")

    if by_provider:
        lines.append("")
        lines.append("By provider:")
        for name, cost in sorted(by_provider.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(
                f"- {name}: ~{_money(float(cost))} ({_pct(float(cost), est_f)})"
            )

    if top_sorted:
        lines.append("")
        lines.append("Top features (share · calls · $/call):")
        shown = top_sorted[:_TOP_FEATURES]
        for name, cost in shown:
            n = activity.get(name) or 0
            per = (cost / n) if n else None
            per_txt = _money(per, digits=3) if per is not None else "n/a"
            lines.append(
                f"- {name}: ~{_money(cost)} "
                f"({_pct(cost, est_f)} · {n} · ~{per_txt}/call)"
            )
        rest = top_sorted[_TOP_FEATURES:]
        if rest:
            rest_cost = sum(c for _, c in rest)
            lines.append(
                f"- (+ {len(rest)} more features totaling ~{_money(rest_cost)})"
            )

    if by_model_tier:
        lines.append("")
        lines.append("Model tier (judgment=Pro, helper=Flash):")
        for name, cost in sorted(
            by_model_tier.items(), key=lambda kv: kv[1], reverse=True
        ):
            lines.append(
                f"- {name}: ~{_money(float(cost))} ({_pct(float(cost), est_f)})"
            )

    empty_fb = totals.get("empty_fallback_events") or 0
    empty_rs = totals.get("empty_response_events") or 0
    if empty_fb or empty_rs:
        lines.append(
            f"Empty Pro responses: {empty_rs}; Flash fallbacks after Pro: {empty_fb}"
        )

    top_prs = report.get("top_prs") or []
    if top_prs:
        lines.append("")
        lines.append("Top PRs by est. cost:")
        for i, row in enumerate(top_prs[:5], start=1):
            lines.append(
                f"{i}. {row.get('pr_url')} — ~"
                f"{_money(float(row.get('est_cost_usd') or 0), digits=4)}"
            )

    other_errors = [
        err
        for err in (report.get("errors") or [])
        if isinstance(err, dict) and err.get("provider") != "gcp_billing"
    ]
    # GCP already surfaced in the summary line; still list other provider failures.
    if other_errors:
        lines.append("")
        lines.append("Other provider errors:")
        for err in other_errors[:5]:
            lines.append(f"- {err.get('provider')}: {err.get('error')}")

    lines.append("")
    lines.append(
        "_List-price: Cursor, llm_logs, Tavily. "
        "gcp_billing is the Cloud Billing invoice (~1 day lag). "
        "Do not add gcp.gemini_invoice to llm_logs Gemini._"
    )
    return "\n".join(lines)


def _models_from_report(report: Dict[str, Any]) -> List[str]:
    seen: List[str] = []
    found = set()
    for ev in report.get("events") or []:
        if not isinstance(ev, dict):
            continue
        model = str(ev.get("model") or "").strip()
        if model and model not in found:
            found.add(model)
            seen.append(model)
    return seen


def _configured_stack_blurb() -> str:
    chat = (os.environ.get("BIGAS_CHAT_MODEL") or os.environ.get("LLM_MODEL") or "").strip()
    review = (os.environ.get("BIGAS_CTO_PR_REVIEW_MODEL") or "").strip()
    autofix = (os.environ.get("BIGAS_CTO_AUTOFIX_MODEL") or "").strip()
    marketing = (os.environ.get("BIGAS_MARKETING_LLM_MODEL") or "").strip()
    lines = [
        f"- Bigas default LLM: {chat or 'gemini-3.1-pro-preview'}",
        f"- Bigas PR review: {review or '(same as default)'}",
        f"- Cursor autofix: {autofix or 'composer-2.5'}",
        f"- Bigas marketing: {marketing or '(same as default)'}",
        "- VCFA living-analysis judgment: gemini-2.5-pro (thinking on)",
        "- VCFA living-analysis helper + Bigas Flash paths: gemini-2.5-flash",
    ]
    return "\n".join(lines)


CFO_WEEKLY_ANALYSIS_INSTRUCTIONS = (
    "You are the Bigas CFO. Write a short weekly cost briefing in English.\n"
    "Use only the usage numbers given. No preamble.\n\n"
    "Use exactly these headings:\n"
    "### Drivers\n"
    "3 bullets: what drove list-price (app / feature / Pro vs Flash). "
    "Mention GCP only if invoice rows exist or the report says invoice is unavailable.\n"
    "### Savings\n"
    "2–4 concrete actions with rough $ impact from the numbers "
    "(e.g. cut PR-review volume, cadence, caching). "
    "List-price (llm_logs, Cursor, Tavily) is not the GCP invoice. "
    "Do not add gcp.gemini_invoice to llm_logs Gemini.\n"
    "### Model\n"
    "1–3 bullets: stay / watch / switch for the stack we run "
    "(Gemini 2.5/3.x Pro+Flash, Claude, GPT, Cursor composer). "
    "You may challenge keeping Gemini Pro on living-analysis judgment "
    "(thesis, moats, replicability, landscape, deal memo) if a cheaper or newer "
    "model would match or beat that quality — state explicitly that living-analysis "
    "performance must not get worse and why quality would hold. "
    "Do not suggest Flash for judgment unless you can argue quality would not drop. "
    "Flag uncertainty. Do not invent list prices.\n\n"
    "Hard limit: 180 words total. Finish every section — never truncate mid-sentence.\n"
)


def analyze_weekly_ai_spend(report: Dict[str, Any]) -> Optional[str]:
    """LLM read of the week's usage plus a model-landscape check. Best-effort."""
    numbers = format_weekly_cto_ai_report(report)
    seen = _models_from_report(report)
    seen_txt = ", ".join(seen) if seen else "(none in this window's events)"
    prompt = (
        f"{CFO_WEEKLY_ANALYSIS_INSTRUCTIONS}\n"
        f"Configured stack:\n{_configured_stack_blurb()}\n\n"
        f"Models seen in this window: {seen_txt}\n\n"
        f"Usage report:\n{numbers}"
    )
    try:
        from bigas.llm.factory import get_llm_client

        llm, model = get_llm_client(feature="cfo_ai_usage")
        messages = [
            {"role": "system", "content": (
                "You are a concise CFO for AI/GCP spend. Challenge the stack when "
                "a better cost/quality trade exists, but never recommend a change "
                "that would worsen living-analysis performance. "
                "Always complete all three sections within the word limit."
            )},
            {"role": "user", "content": prompt},
        ]
        call_kwargs: Dict[str, Any] = {
            "max_tokens": 4_096,
            "temperature": 0.3,
        }
        # Gemini shares max_output_tokens between thinking and visible text.
        if str(model or "").lower().startswith("gemini"):
            call_kwargs["thinking_budget"] = 1_024

        text = (llm.complete(messages, **call_kwargs) or "").strip()
        if text and not _cfo_analysis_looks_complete(text):
            # One retry with more output room and no thinking budget.
            retry_kwargs = {"max_tokens": 4_096, "temperature": 0.2}
            text2 = (llm.complete(messages, **retry_kwargs) or "").strip()
            if text2 and (
                _cfo_analysis_looks_complete(text2)
                or len(text2) > len(text)
            ):
                text = text2
        return text or None
    except Exception:
        logger.warning("CFO weekly AI spend analysis failed", exc_info=True)
        return None


def _cfo_analysis_looks_complete(text: str) -> bool:
    low = (text or "").lower()
    if "### drivers" not in low or "### savings" not in low or "### model" not in low:
        return False
    last = (text or "").rstrip().splitlines()[-1].strip() if text else ""
    if not last:
        return False
    if last.count("`") % 2 == 1 or last.count("**") % 2 == 1:
        return False
    if last[-1] not in ".!?:;`\"')]":
        # Heading-only last line is ok; mid-bullet cut is not.
        if last.startswith("#"):
            return False
        if last.startswith(("-", "*", "•")) or (
            len(last) > 2 and last[0].isdigit() and last[1] in ".)"
        ):
            return False
    return True


def build_weekly_cfo_ai_report(
    report: Dict[str, Any],
    *,
    include_prior_period: bool = True,
    providers: Optional[Sequence[UsageProvider]] = None,
) -> str:
    enriched = (
        enrich_with_prior_period(report, providers=providers)
        if include_prior_period
        else report
    )
    numbers = format_weekly_cto_ai_report(enriched).replace(
        "**Bigas AI + cloud usage",
        "**CFO: AI + cloud usage",
        1,
    )
    analysis = analyze_weekly_ai_spend(enriched)
    if not analysis:
        return numbers
    return f"{numbers}\n\n**CFO analysis**\n{analysis}"


def publish_weekly_cfo_ai_report(message: str) -> None:
    """Post the full weekly briefing to CFO chat (and Discord, chunked if needed).

    Uses ``post_long_to_discord`` so chat gets the entire message once while
    Discord receives newline-safe chunks under the 2k limit.
    """
    from bigas.discord_webhook import post_long_to_discord

    webhook = (os.environ.get("DISCORD_WEBHOOK_URL_CFO") or "").strip()
    post_long_to_discord(webhook, message, chat_agent_id="cfo")
