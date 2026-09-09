"""HTTP endpoints for scheduled model evaluation runs."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, jsonify, request

EVAL_TZ = ZoneInfo("Europe/Stockholm")
# Sunday anchor for fortnightly eval cadence (weeks since this date mod N).
CADENCE_EPOCH = date(2025, 12, 28)


def should_run_cadence(every_n_weeks: int, *, now: Optional[datetime] = None) -> bool:
    """True when this Stockholm week should fire on the configured cadence.

    ``every_n_weeks=1`` always returns True. For larger intervals, whole weeks
    elapsed since ``CADENCE_EPOCH`` are used so bi-weekly pacing continues across
    ISO year boundaries (unlike ISO week number modulo).

    ``now`` defaults to the current time in ``EVAL_TZ``. Timezone-aware values
    are converted to Stockholm. Naive values are treated as UTC before conversion;
    pass an aware ``EVAL_TZ`` timestamp in tests when possible.
    """
    interval = int(every_n_weeks or 1)
    if interval <= 1:
        return True
    stamp = now or datetime.now(EVAL_TZ)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc).astimezone(EVAL_TZ)
    else:
        stamp = stamp.astimezone(EVAL_TZ)
    weeks_elapsed = (stamp.date() - CADENCE_EPOCH).days // 7
    return weeks_elapsed % interval == 0

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
import bigas.eval.use_cases.okr_goal_loop  # noqa: F401
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
        "include_baseline": true,
        "every_n_weeks": 2
      }

    Omit company/url to run every pack fixture.
    every_n_weeks=2 runs every other week (Europe/Stockholm, from CADENCE_EPOCH)
    so a Sunday 16:00 cron can share the CTO-report wake-up but only eval
    fortnightly.
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

    try:
        every_n_weeks = int(data.get("every_n_weeks") or 1)
    except (TypeError, ValueError):
        return jsonify({"error": "every_n_weeks must be an integer"}), 400
    if every_n_weeks < 1:
        return jsonify({"error": "every_n_weeks must be >= 1"}), 400
    if not should_run_cadence(every_n_weeks):
        return jsonify(
            {
                "status": "skipped",
                "reason": "biweekly_cadence",
                "every_n_weeks": every_n_weeks,
                "use_case": use_case,
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
