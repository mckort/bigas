"""Aggregate AI usage from UsageProvider implementations + Discord helpers."""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

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


def format_weekly_cto_ai_report(report: Dict[str, Any]) -> str:
    days = report.get("days") or 7
    totals = report.get("totals") or {}
    est = totals.get("est_cost_usd")
    invoice = totals.get("invoice_cost_usd")
    lines = [
        f"**Bigas AI + cloud usage (last {days} days)**",
        (
            f"List-price (LLM + Cursor + Tavily): ~${float(est):.4f}"
            if est is not None
            else "List-price (LLM + Cursor + Tavily): n/a"
        ),
    ]
    if invoice:
        lines.append(f"GCP invoice (Cloud Billing export): ~${float(invoice):.4f}")
    lines.append(f"Events: {totals.get('events') or 0}")
    by_provider = totals.get("by_provider") or {}
    if by_provider:
        lines.append("By provider:")
        for name, cost in sorted(by_provider.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- {name}: ~${float(cost):.4f}")
    by_feature = totals.get("by_feature") or {}
    if by_feature:
        lines.append("By feature:")
        for name, cost in sorted(by_feature.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- {name}: ~${float(cost):.4f}")

    by_app = totals.get("by_app") or {}
    if by_app:
        lines.append("By app:")
        for name, cost in sorted(by_app.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- {name}: ~${float(cost):.4f}")
    by_model_tier = totals.get("by_model_tier") or {}
    if by_model_tier:
        lines.append("By model tier (judgment=Pro, helper=Flash):")
        for name, cost in sorted(by_model_tier.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- {name}: ~${float(cost):.4f}")
    empty_fb = totals.get("empty_fallback_events") or 0
    empty_rs = totals.get("empty_response_events") or 0
    if empty_fb or empty_rs:
        lines.append(
            f"Empty Pro responses: {empty_rs}; Flash fallbacks after Pro: {empty_fb}"
        )

    activity_by_feature = totals.get("activity_by_feature") or {}
    if activity_by_feature:
        lines.append("LLM calls:")
        for name, n in sorted(activity_by_feature.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- {name}: {n}")

    top_prs = report.get("top_prs") or []
    if top_prs:
        lines.append("Top PRs by est. cost:")
        for i, row in enumerate(top_prs[:5], start=1):
            lines.append(
                f"{i}. {row.get('pr_url')} — ~${float(row.get('est_cost_usd') or 0):.4f}"
            )

    errors = report.get("errors") or []
    if errors:
        lines.append("Provider errors:")
        for err in errors[:5]:
            lines.append(f"- {err.get('provider')}: {err.get('error')}")

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
    "You are the Bigas CFO. Write a concise weekly cost briefing in English.\n"
    "Use only the usage numbers given.\n\n"
    "Do two things:\n"
    "1) Usage analysis — what drove cost (app, feature, Pro vs Flash), empty Pro/"
    "Flash-fallback waste, Tavily search, and GCP invoice line items "
    "(Firestore, Cloud Run, …). Give 2–4 concrete savings. "
    "List-price (llm_logs, Cursor, Tavily) is not the GCP invoice. "
    "gcp.gemini_invoice is billed Gemini; do not add it to llm_logs Gemini.\n"
    "2) Model landscape — for the models we run now, and other leading LLMs "
    "(Gemini 2.5/3.x Pro+Flash, Claude, GPT, Cursor composer), note any recent "
    "releases, price cuts, or quality jumps that could cut cost or improve "
    "performance. You may challenge keeping Gemini Pro on living-analysis "
    "judgment (thesis, moats, replicability, landscape, deal memo) if a cheaper "
    "or newer model would match or beat that analysis quality. Any such "
    "recommendation must state explicitly that living-analysis performance must "
    "not get worse, and why you believe quality would hold or improve. "
    "Do not suggest Flash (or similar) for that judgment work unless you can "
    "argue quality would not drop. Flag uncertainty. Do not invent list prices. "
    "End with stay / watch / switch recommendations.\n\n"
    "Keep it under 400 words. Markdown with short headings. No preamble.\n"
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

        llm, _model = get_llm_client(feature="cfo_ai_usage")
        text = (llm.complete(
            [
                {"role": "system", "content": (
                    "You are a concise CFO for AI/GCP spend. Challenge the stack when "
                    "a better cost/quality trade exists, but never recommend a change "
                    "that would worsen living-analysis performance."
                )},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2_048,
            temperature=0.3,
        ) or "").strip()
        return text or None
    except Exception:
        logger.warning("CFO weekly AI spend analysis failed", exc_info=True)
        return None


def build_weekly_cfo_ai_report(report: Dict[str, Any]) -> str:
    numbers = format_weekly_cto_ai_report(report).replace(
        "**Bigas AI + cloud usage",
        "**CFO: AI + cloud usage",
        1,
    )
    analysis = analyze_weekly_ai_spend(report)
    if not analysis:
        return numbers
    return f"{numbers}\n\n**CFO analysis**\n{analysis}"


def publish_weekly_cfo_ai_report(message: str) -> None:
    """Post the weekly AI cost briefing to the CFO chat thread (optional CFO Discord)."""
    from bigas.discord_webhook import post_long_to_discord

    webhook = (os.environ.get("DISCORD_WEBHOOK_URL_CFO") or "").strip()
    post_long_to_discord(webhook, message, chat_agent_id="cfo")
