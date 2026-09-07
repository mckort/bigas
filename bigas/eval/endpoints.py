"""HTTP endpoints for scheduled model evaluation runs."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from bigas.eval.base import EvalFixture, reject_customer_identifiers
from bigas.eval.reporter import summarize_for_response
from bigas.eval.runner import EvalRunner

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
        "models": ["gpt-4o", "gemini-2.5-pro"],
        "dry_run": false,
        "skip_judge": false,
        "post_discord": true,
        "post_to_chat": true
      }
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
        )
        return jsonify(summarize_for_response(result))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Eval task failed for %s", use_case)
        return jsonify({"error": str(exc)}), 500
