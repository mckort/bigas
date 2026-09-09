"""HTTP endpoints for scheduled model evaluation runs."""
from __future__ import annotations

import logging

from flask import Blueprint, Response, jsonify, request

from bigas.eval.base import EvalFixture, reject_customer_identifiers
from bigas.eval.html import build_html_report
from bigas.eval.reporter import (
    report_html_blob_path,
    report_json_blob_path,
    summarize_for_response,
)
from bigas.eval.runner import EvalRunner
from bigas.eval.signing import verify_report_token
from bigas.eval.storage import EvalStorage

# Register use-case adapters on import.
import bigas.eval.use_cases.vc_field_assistant  # noqa: F401

logger = logging.getLogger(__name__)

eval_bp = Blueprint("eval_bp", __name__)


@eval_bp.route("/tasks/eval/<use_case>", methods=["POST"])
def run_eval_task(use_case: str):
    """
    Run a modular model evaluation for the given use case.

    Body (optional):
      {
        "company": "VC Field Assistant",
        "url": "https://vcfieldassistant.com",
        "extra_urls": [],
        "models": ["gpt-6-astra", "gemini-3.1-pro-preview"],
        "dry_run": false,
        "skip_judge": false,
        "post_discord": true,
        "post_to_chat": true,
        "include_baseline": true
      }

    Omit company/url to run every pack fixture.
    """
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    try:
        reject_customer_identifiers(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    extra_urls = data.get("extra_urls")
    if extra_urls is not None and not isinstance(extra_urls, list):
        return jsonify({"error": "extra_urls must be a list of strings"}), 400

    models = data.get("models")
    if models is not None and not isinstance(models, list):
        return jsonify({"error": "models must be a list of strings"}), 400

    fixture = None
    company = (data.get("company") or data.get("company_name") or "").strip()
    url = (data.get("url") or data.get("website_url") or "").strip()
    if company or url:
        fixture = EvalFixture.from_dict(
            {
                "company_name": company,
                "website_url": url,
                "extra_urls": extra_urls or [],
            }
        )

    dry_run = bool(data.get("dry_run"))
    skip_judge = bool(data.get("skip_judge"))
    post_discord = data.get("post_discord", True) is not False
    post_chat = data.get("post_to_chat", True) is not False
    include_baseline = data.get("include_baseline", True) is not False

    try:
        runner = EvalRunner()
        result = runner.run(
            use_case,
            fixture=fixture,
            models=models,
            dry_run=dry_run,
            skip_judge=skip_judge,
            post_discord=post_discord,
            post_chat=post_chat,
            include_baseline=include_baseline,
        )
        return jsonify(summarize_for_response(result))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Eval task failed for %s", use_case)
        return jsonify({"error": str(exc)}), 500


@eval_bp.route("/eval/reports/<use_case>/<run_id>", methods=["GET"])
def view_eval_report(use_case: str, run_id: str):
    """Signed, clickable HTML report — not the raw ranking.json."""
    token = (request.args.get("token") or "").strip()
    if not verify_report_token(use_case, run_id, token):
        return Response("Invalid or missing report link.", status=403, mimetype="text/plain")

    from bigas.eval.base import EvalRunResult

    storage = EvalStorage()
    stub = EvalRunResult(use_case=use_case, run_id=run_id, fixture=EvalFixture("", ""))
    html_blob = report_html_blob_path(stub)
    page = storage.get_text(html_blob)
    if page:
        return Response(page, status=200, mimetype="text/html; charset=utf-8")

    ranking = storage.get_json(report_json_blob_path(stub))
    if not ranking:
        return Response("Report not found.", status=404, mimetype="text/plain")
    run = EvalRunResult.from_dict(ranking)
    run.use_case = run.use_case or use_case
    run.run_id = run.run_id or run_id
    return Response(build_html_report(run), status=200, mimetype="text/html; charset=utf-8")
