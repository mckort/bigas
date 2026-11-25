"""Reconcile GCP Gemini invoice rows with ``llm_logs`` list-price estimates."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

_GEMINI_INVOICE_FEATURE = "gcp.gemini_invoice"
_RECONCILE_SAVING_ID = "reconcile-gemini-gap"
_TOKEN_SKU_HINTS = ("token", "input", "output", "character")
_NON_TOKEN_SKU_HINTS = (
    "cache",
    "caching",
    "storage",
    "grounding",
    "embedding",
    "search",
    "image",
    "video",
    "audio",
)


def _money(value: float, *, digits: int = 2) -> str:
    return f"${value:.{digits}f}"


def _approx_money(value: float, *, digits: int = 2) -> str:
    return f"≈{_money(value, digits=digits)}"


def _event_cost(ev: Dict[str, Any]) -> float:
    try:
        return float(ev.get("est_cost_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def _sku_kind(sku: str) -> str:
    low = (sku or "").strip().lower()
    if not low:
        return "unknown"
    tokenish = any(hint in low for hint in _TOKEN_SKU_HINTS)
    # Context-cache input/output token SKUs include "cache" but are still
    # token charges. Only storage-style cache SKUs (no token keywords) are
    # non-token.
    if ("cache" in low or "caching" in low) and tokenish:
        return "token"
    if any(hint in low for hint in _NON_TOKEN_SKU_HINTS):
        if "cache" in low or "caching" in low or "storage" in low:
            return "context_cache"
        if "grounding" in low or "search" in low:
            return "grounding"
        if "embedding" in low:
            return "embedding"
        return "non_token"
    if tokenish:
        return "token"
    return "other"


def summarize_gemini_gap(report: Dict[str, Any]) -> Dict[str, Any]:
    """Return structured Gemini invoice vs llm_logs reconciliation for one report."""
    invoice_usd = 0.0
    list_price_usd = 0.0
    in_tok = 0
    out_tok = 0
    cached_tok = 0
    invoice_skus: Dict[str, float] = defaultdict(float)
    sku_kinds: Dict[str, float] = defaultdict(float)
    llm_by_feature: Dict[str, float] = defaultdict(float)
    llm_by_project: Dict[str, float] = defaultdict(float)
    llm_by_app: Dict[str, float] = defaultdict(float)

    for ev in report.get("events") or []:
        if not isinstance(ev, dict):
            continue
        feature = str(ev.get("feature") or "")
        model = str(ev.get("model") or "").lower()
        cost = _event_cost(ev)
        if feature == _GEMINI_INVOICE_FEATURE:
            invoice_usd += cost
            meta = ev.get("meta") if isinstance(ev.get("meta"), dict) else {}
            sku = str(meta.get("gcp_sku") or "").strip() or "(unknown sku)"
            invoice_skus[sku] += cost
            sku_kinds[_sku_kind(sku)] += cost
        if ev.get("provider") == "llm_logs" and "gemini" in model:
            list_price_usd += cost
            llm_by_feature[str(ev.get("feature") or "llm")] += cost
            meta = ev.get("meta") if isinstance(ev.get("meta"), dict) else {}
            proj = str(meta.get("gcp_project") or "").strip() or "unknown"
            app = str(meta.get("app") or "").strip() or "bigas"
            llm_by_project[proj] += cost
            llm_by_app[app] += cost
            try:
                in_tok += int(ev.get("input_tokens") or 0)
                out_tok += int(ev.get("output_tokens") or 0)
            except (TypeError, ValueError):
                pass
            try:
                cached_tok += int(ev.get("cached_content_tokens") or 0)
            except (TypeError, ValueError):
                pass

    gap_usd = round(invoice_usd - list_price_usd, 6)
    gap_pct: Optional[float] = None
    if invoice_usd > 0:
        gap_pct = round(100.0 * abs(gap_usd) / invoice_usd, 1)

    non_token_invoice = round(
        sum(v for k, v in sku_kinds.items() if k != "token"), 6
    )

    top_skus = sorted(invoice_skus.items(), key=lambda kv: kv[1], reverse=True)[:6]
    top_features = sorted(llm_by_feature.items(), key=lambda kv: kv[1], reverse=True)[:6]

    hypotheses: List[str] = []
    if non_token_invoice >= 5.0:
        hypotheses.append(
            f"Invoice includes {_approx_money(non_token_invoice)} in non-token Gemini SKUs "
            "(context cache storage, grounding, embeddings) that llm_logs does not estimate."
        )
    if list_price_usd > invoice_usd * 1.05:
        hypotheses.append(
            "List-price exceeds invoice — check the price table (intro rates, model id drift) "
            "or double-counted log lines."
        )
    elif gap_usd >= 10.0:
        hypotheses.append(
            "Untracked or partially logged Gemini calls (direct SDK, multimodal bypass, "
            "streaming without usage_metadata, or workloads outside Bigas logging)."
        )
    vcfa_logs = llm_by_app.get("vcfieldassistant", 0.0)
    bigas_logs = llm_by_app.get("bigas", 0.0)
    if vcfa_logs > 0 and bigas_logs > 0:
        hypotheses.append(
            f"Mixed apps on the invoice: llm_logs bigas {_approx_money(bigas_logs)} · "
            f"vcfieldassistant {_approx_money(vcfa_logs)}."
        )

    return {
        "invoice_usd": round(invoice_usd, 6),
        "list_price_usd": round(list_price_usd, 6),
        "gap_usd": gap_usd,
        "gap_pct": gap_pct,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cached_content_tokens": cached_tok,
        "non_token_invoice_usd": non_token_invoice,
        "invoice_skus": [{"sku": s, "cost_usd": round(c, 6)} for s, c in top_skus],
        "llm_logs_by_feature": [
            {"feature": f, "cost_usd": round(c, 6)} for f, c in top_features
        ],
        "llm_logs_by_project": dict(llm_by_project),
        "hypotheses": hypotheses,
        "has_data": invoice_usd > 0 or list_price_usd > 0,
    }


def format_gemini_reconciliation_block(report: Dict[str, Any]) -> str:
    """Multi-line CFO report section for Gemini invoice vs llm_logs."""
    summary = summarize_gemini_gap(report)
    if not summary.get("has_data"):
        return ""

    invoice = float(summary["invoice_usd"])
    list_price = float(summary["list_price_usd"])
    gap = float(summary["gap_usd"])
    lines = [
        "Gemini invoice vs list-price (do not add them): "
        f"invoice {_approx_money(invoice)} · llm_logs {_approx_money(list_price)} · "
        f"gap {_approx_money(abs(gap))}"
        + (f" ({summary['gap_pct']}% of invoice)" if summary.get("gap_pct") is not None else "")
        + ".",
    ]
    in_tok = int(summary.get("input_tokens") or 0)
    out_tok = int(summary.get("output_tokens") or 0)
    cached = int(summary.get("cached_content_tokens") or 0)
    if in_tok or out_tok:
        token_line = f"llm_logs tokens: in {in_tok:,} / out {out_tok:,}"
        if cached:
            token_line += f" / cached {cached:,}"
        lines.append(token_line + ".")

    non_token = float(summary.get("non_token_invoice_usd") or 0)
    if non_token >= 0.5:
        lines.append(
            f"Non-token invoice SKUs (cache, grounding, …): {_approx_money(non_token)}."
        )

    for row in summary.get("invoice_skus") or []:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku") or "")
        cost = float(row.get("cost_usd") or 0)
        if cost >= 0.5 and sku:
            lines.append(f"- Invoice SKU {sku}: {_approx_money(cost)}")

    for row in summary.get("llm_logs_by_feature") or []:
        if not isinstance(row, dict):
            continue
        feat = str(row.get("feature") or "")
        cost = float(row.get("cost_usd") or 0)
        if cost >= 0.5 and feat:
            lines.append(f"- llm_logs {feat}: {_approx_money(cost)}")

    for hint in summary.get("hypotheses") or []:
        if hint:
            lines.append(f"- Likely driver: {hint}")

    return "\n".join(lines)


def reconcile_gemini_saving_action(report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a CFO savings action when the weekly Gemini gap is material."""
    summary = summarize_gemini_gap(report)
    gap = float(summary.get("gap_usd") or 0)
    invoice = float(summary.get("invoice_usd") or 0)
    if invoice <= 0 or abs(gap) < 10.0:
        return None

    detail_bits = [
        f"GCP Gemini invoice {_approx_money(invoice)} vs llm_logs list-price "
        f"{_approx_money(float(summary.get('list_price_usd') or 0))} "
        f"(gap {_approx_money(abs(gap))}).",
    ]
    non_token = float(summary.get("non_token_invoice_usd") or 0)
    if non_token >= 5.0:
        detail_bits.append(
            f"Non-token SKUs account for {_approx_money(non_token)} on the invoice."
        )
    for hint in (summary.get("hypotheses") or [])[:2]:
        detail_bits.append(hint)

    return {
        "id": _RECONCILE_SAVING_ID,
        "title": "Reconcile Gemini invoice gap",
        "detail": " ".join(detail_bits),
        "impact_usd": round(abs(gap), 2),
    }
