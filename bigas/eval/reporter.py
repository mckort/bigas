"""Markdown/HTML ranking reports and delivery to PM chat / Discord."""
from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import urlencode

from bigas.chat.activity import post_to_agent_thread
from bigas.discord_webhook import post_long_to_discord
from bigas.eval.base import EvalRunResult
from bigas.eval.html import build_html_report
from bigas.eval.readable import build_full_markdown, build_summary_markdown
from bigas.eval.signing import sign_report, signing_secret
from bigas.eval.storage import EvalStorage, eval_bucket_name

logger = logging.getLogger(__name__)

DEFAULT_PUBLIC_BASE = "https://mcp-marketing-343105851187.europe-north1.run.app"


def report_json_blob_path(run: EvalRunResult) -> str:
    return f"{run.use_case}/reports/{run.run_id}/ranking.json"


def report_html_blob_path(run: EvalRunResult) -> str:
    return f"{run.use_case}/reports/{run.run_id}/report.html"


def report_markdown_blob_path(run: EvalRunResult) -> str:
    return f"{run.use_case}/reports/{run.run_id}/report.md"


def public_eval_base_url() -> str:
    return (
        (os.environ.get("BIGAS_PUBLIC_URL") or "").strip()
        or (os.environ.get("SERVER_URL") or "").strip()
        or DEFAULT_PUBLIC_BASE
    ).rstrip("/")


def readable_report_url(run: EvalRunResult) -> str:
    if not signing_secret():
        return ""
    query = urlencode({"token": sign_report(run.use_case, run.run_id)})
    return f"{public_eval_base_url()}/eval/reports/{run.use_case}/{run.run_id}?{query}"


def attach_report_paths(run: EvalRunResult) -> None:
    run.report_blob = run.report_blob or report_json_blob_path(run)
    run.report_html_blob = run.report_html_blob or report_html_blob_path(run)
    run.report_markdown_blob = run.report_markdown_blob or report_markdown_blob_path(run)
    run.report_url = run.report_url or readable_report_url(run)


def build_markdown_report(run: EvalRunResult) -> str:
    attach_report_paths(run)
    return build_summary_markdown(run)


def publish_report(
    run: EvalRunResult,
    *,
    markdown: Optional[str] = None,
    post_discord: bool = True,
    post_chat: bool = True,
) -> dict:
    """Persist JSON + readable reports and post to PM Discord + product chat."""
    attach_report_paths(run)
    body = (markdown or run.report_markdown or build_markdown_report(run)).strip()
    run.report_markdown = body
    full_markdown = build_full_markdown(run)
    html_page = build_html_report(run)

    storage = EvalStorage()
    storage.store_json(run.report_blob, run.to_dict())
    storage.store_text(run.report_markdown_blob, full_markdown, content_type="text/markdown; charset=utf-8")
    storage.store_text(run.report_html_blob, html_page, content_type="text/html; charset=utf-8")

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
        "report_html_blob": run.report_html_blob,
        "report_markdown_blob": run.report_markdown_blob,
        "report_gcs_uri": storage.gcs_uri(run.report_blob),
        "report_url": run.report_url,
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
        "baseline_model": run.baseline_model,
        "fixture": run.fixture.to_dict(),
        "report_url": run.report_url,
        "ranking_json_url": (
            f"gs://{eval_bucket_name()}/{run.report_blob}" if run.report_blob else ""
        ),
        "models_tested": len(run.results),
        "models": [f"{r.provider}:{r.model_id}" for r in run.results],
        "dry_run": run.dry_run,
    }
