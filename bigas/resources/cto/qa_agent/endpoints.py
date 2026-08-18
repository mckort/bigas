"""Human-in-the-loop HTTP routes for QA improvement proposals."""
from __future__ import annotations

import logging

from flask import Blueprint, Response, request

from bigas.resources.cto.qa_agent.service import QAAgentError, QAAgentService
from bigas.resources.product.x_posts.html import error_page, success_page
from bigas.resources.product.x_posts.signing import verify_draft_token

logger = logging.getLogger(__name__)

qa_proposals_bp = Blueprint("qa_proposals_bp", __name__, url_prefix="/api/qa-proposals")


def _service() -> QAAgentService:
    return QAAgentService()


def _html(body: str, status: int = 200) -> Response:
    return Response(body, status=status, mimetype="text/html; charset=utf-8")


def _require_token(proposal_id: str) -> str | None:
    token = (request.args.get("token") or request.form.get("token") or "").strip()
    if not verify_draft_token(proposal_id, token):
        return None
    return token


def _preview_page(payload: dict, *, action_base: str, token: str) -> str:
    import html as html_module

    title = html_module.escape(str(payload.get("title") or "QA improvement"))
    tool = html_module.escape(str(payload.get("tool_name") or "?"))
    summary = html_module.escape(str(payload.get("summary") or ""))
    proposal = html_module.escape(str(payload.get("proposal") or ""))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QA proposal</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f1419; color: #e7e9ea; }}
    .wrap {{ max-width: 32rem; margin: 0 auto; padding: 1.5rem 1.25rem 2.5rem; }}
    h1 {{ font-size: 1.35rem; margin: 0 0 0.75rem; }}
    .card {{ background: #15202b; border: 1px solid #38444d; border-radius: 12px; padding: 1rem 1.1rem; margin: 1rem 0; white-space: pre-wrap; }}
    .muted {{ color: #8b98a5; font-size: 0.95rem; }}
    .actions {{ display: flex; flex-direction: column; gap: 0.75rem; margin-top: 1.25rem; }}
    button {{ appearance: none; border: 0; border-radius: 999px; padding: 0.9rem 1rem; font-size: 1.05rem; font-weight: 650; }}
    .approve {{ background: #00ba7c; color: #fff; }}
    .decline {{ background: #38444d; color: #e7e9ea; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>QA improvement proposal</h1>
    <p class="muted">Tool: {tool}</p>
    <p><strong>{title}</strong></p>
    {f'<p class="muted">{summary}</p>' if summary else ''}
    <div class="card">{proposal or '(no proposal text)'}</div>
    <form class="actions" method="post" action="{html_module.escape(action_base)}/approve">
      <input type="hidden" name="token" value="{html_module.escape(token)}">
      <button type="submit" class="approve">Approve — create Jira issue</button>
    </form>
    <form class="actions" method="post" action="{html_module.escape(action_base)}/decline">
      <input type="hidden" name="token" value="{html_module.escape(token)}">
      <button type="submit" class="decline">Decline</button>
    </form>
  </div>
</body>
</html>"""


@qa_proposals_bp.route("/<proposal_id>", methods=["GET"])
def review_qa_proposal(proposal_id: str):
    token = _require_token(proposal_id)
    if token is None:
        return _html(error_page(title="Invalid link", message="This approval link is invalid."), 403)
    try:
        payload = _service().load_proposal(proposal_id)
    except QAAgentError as e:
        status = 404 if "not found" in str(e).lower() or "expired" in str(e).lower() else 400
        return _html(error_page(title="Proposal unavailable", message=str(e)), status)
    except Exception:
        logger.error("Failed to load QA proposal %s", proposal_id, exc_info=True)
        return _html(error_page(title="Error", message="Could not load this proposal."), 500)
    action_base = f"/api/qa-proposals/{proposal_id}"
    return _html(_preview_page(payload, action_base=action_base, token=token))


@qa_proposals_bp.route("/<proposal_id>/approve", methods=["POST"])
def approve_qa_proposal(proposal_id: str):
    if _require_token(proposal_id) is None:
        return _html(error_page(title="Invalid link", message="This approval link is invalid."), 403)
    try:
        result = _service().approve_proposal(proposal_id)
    except QAAgentError as e:
        status = 404 if "not found" in str(e).lower() or "expired" in str(e).lower() else 400
        return _html(error_page(title="Could not approve", message=str(e)), status)
    except Exception:
        logger.error("Failed to approve QA proposal %s", proposal_id, exc_info=True)
        return _html(error_page(title="Error", message="Approval failed."), 500)

    issue_key = result.get("issue_key") or ""
    issue_url = result.get("issue_url") or ""
    extra = f"Jira issue: {issue_key}\n{issue_url}".strip()
    return _html(
        success_page(
            title="Approved",
            message="A Jira issue was created with the QA improvement proposal.",
            extra=extra,
        )
    )


@qa_proposals_bp.route("/<proposal_id>/decline", methods=["POST"])
def decline_qa_proposal(proposal_id: str):
    if _require_token(proposal_id) is None:
        return _html(error_page(title="Invalid link", message="This approval link is invalid."), 403)
    try:
        _service().decline_proposal(proposal_id)
    except QAAgentError as e:
        status = 404 if "not found" in str(e).lower() else 400
        return _html(error_page(title="Could not decline", message=str(e)), status)
    except Exception:
        logger.error("Failed to decline QA proposal %s", proposal_id, exc_info=True)
        return _html(error_page(title="Error", message="Could not decline this proposal."), 500)
    return _html(
        success_page(
            title="Declined",
            message="The stored proposal was deleted. No Jira issue was created.",
        )
    )
