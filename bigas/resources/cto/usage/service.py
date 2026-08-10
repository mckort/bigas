"""Aggregate AI usage from UsageProvider implementations + Discord helpers."""
from __future__ import annotations

import logging
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
    feature_prefix: Optional[str] = "cto_",
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
    cost_total = 0.0
    cost_known = 0
    for ev in events:
        if ev.est_cost_usd is None:
            continue
        cost_total += float(ev.est_cost_usd)
        cost_known += 1
        by_provider[ev.provider] += float(ev.est_cost_usd)
        by_feature[ev.feature] += float(ev.est_cost_usd)

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
            "est_cost_usd": round(cost_total, 6),
            "events": len(events),
            "events_with_cost": cost_known,
            "by_provider": {k: round(v, 6) for k, v in sorted(by_provider.items())},
            "by_feature": {k: round(v, 6) for k, v in sorted(by_feature.items())},
        },
        "top_prs": [{"pr_url": u, "est_cost_usd": round(c, 6)} for u, c in top_prs],
        "events": [e.as_dict() for e in events],
        "errors": errors,
    }


def format_weekly_cto_ai_report(report: Dict[str, Any]) -> str:
    days = report.get("days") or 7
    totals = report.get("totals") or {}
    est = totals.get("est_cost_usd")
    lines = [
        f"**CTO AI usage (last {days} days)**",
        (
            f"Estimated list-price total: ~${float(est):.4f}"
            if est is not None
            else "Estimated list-price total: n/a"
        ),
        f"Events: {totals.get('events') or 0}",
    ]
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

    # Accomplishments-ish: count autofix agents / review totals from events.
    events = report.get("events") or []
    autofix_n = sum(1 for e in events if e.get("feature") == "cto_autofix")
    review_n = sum(1 for e in events if e.get("feature") == "cto_pr_review")
    if autofix_n or review_n:
        lines.append(
            f"Activity: {autofix_n} Cursor autofix agent(s), {review_n} PR review total(s)"
        )

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

    lines.append("_List-price estimates only; not Cursor/GCP invoices._")
    return "\n".join(lines)
