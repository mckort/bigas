"""Markdown ranking reports and delivery to PM chat / Discord."""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from bigas.chat.activity import post_to_agent_thread
from bigas.discord_webhook import post_long_to_discord
from bigas.eval.base import EvalModelResult, EvalRunResult
from bigas.eval.storage import EvalStorage, eval_bucket_name

logger = logging.getLogger(__name__)


def report_json_blob_path(run: EvalRunResult) -> str:
    return f"{run.use_case}/reports/{run.run_id}/ranking.json"


def build_markdown_report(run: EvalRunResult) -> str:
    ranked = run.ranked_results()
    lines = [
        "# AI Model Evaluation Report",
        "",
        f"**Use case:** {run.use_case}",
        f"**Run ID:** {run.run_id}",
        f"**Fixture:** {run.fixture.company_name} — {run.fixture.website_url}",
        "",
    ]

    if not ranked:
        lines.append("_No successful model results to rank._")
        errors = [r for r in run.results if r.error]
        for item in errors:
            lines.append(f"- {item.model_id}: {item.error}")
        return "\n".join(lines)

    champion = ranked[0]
    lines.extend(
        [
            f"**Champion:** {champion.model_id} (score {champion.score:.1f}/100)",
            "",
            "## Ranking",
            "",
            "| Rank | Model | Score | Latency | Est. cost |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )

    for idx, result in enumerate(ranked, start=1):
        latency = f"{result.usage.latency_ms:.0f} ms"
        cost = (
            f"${result.usage.cost_usd:.4f}"
            if result.usage.cost_usd is not None
            else "n/a"
        )
        lines.append(
            f"| {idx} | {result.model_id} | {result.score:.1f} | {latency} | {cost} |"
        )

    lines.extend(["", "## Motivation", ""])
    for idx, result in enumerate(ranked, start=1):
        lines.append(f"### {idx}. {result.model_id}")
        lines.append(result.score_rationale or "_No rationale provided._")
        if result.output_blob:
            lines.append(f"- Output: `gs://{eval_bucket_name()}/{result.output_blob}`")
        lines.append("")

    if run.report_blob:
        lines.append(f"Full report JSON: `gs://{eval_bucket_name()}/{run.report_blob}`")

    return "\n".join(lines).strip()


def publish_report(
    run: EvalRunResult,
    *,
    markdown: Optional[str] = None,
    post_discord: bool = True,
    post_chat: bool = True,
) -> dict:
    """Persist report and post to PM Discord + product chat thread."""
    if not run.report_blob:
        run.report_blob = report_json_blob_path(run)

    body = (markdown or run.report_markdown or build_markdown_report(run)).strip()
    run.report_markdown = body

    storage = EvalStorage()
    storage.store_json(run.report_blob, run.to_dict())

    posted_discord = False
    posted_chat = False

    webhook = (
        (os.environ.get("MODEL_EVAL_DISCORD_WEBHOOK_URL") or "").strip()
        or (os.environ.get("DISCORD_WEBHOOK_URL_PRODUCT") or "").strip()
    )
    if post_discord and webhook:
        post_long_to_discord(
            webhook,
            body,
            chat_agent_id="product",
            chat_metadata={"source": "model_eval", "run_id": run.run_id},
        )
        posted_discord = True

    if post_chat:
        chat_result = post_to_agent_thread(
            "product",
            body,
            metadata={"source": "model_eval", "run_id": run.run_id, "use_case": run.use_case},
        )
        posted_chat = bool(chat_result)

    return {
        "report_blob": run.report_blob,
        "report_gcs_uri": storage.gcs_uri(run.report_blob),
        "posted_discord": posted_discord,
        "posted_chat": posted_chat,
    }


def summarize_for_response(run: EvalRunResult) -> dict:
    ranked = run.ranked_results()
    champion = ranked[0].model_id if ranked else ""
    return {
        "status": "ok",
        "use_case": run.use_case,
        "run_id": run.run_id,
        "champion": champion,
        "fixture": run.fixture.to_dict(),
        "report_url": EvalStorage().gcs_uri(run.report_blob) if run.report_blob else "",
        "models_tested": len(run.results),
        "models": [f"{r.provider}:{r.model_id}" for r in run.results],
        "dry_run": run.dry_run,
    }
