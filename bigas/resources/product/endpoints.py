from flask import Blueprint, jsonify, request
import logging
import os
import threading
import uuid

from bigas.access import require_bigas_access_key
from bigas.discord_webhook import post_long_to_discord, post_to_discord as post_discord_message
from bigas.resources.marketing.utils import (
    request_flag,
    sanitize_error_message,
    validate_request_data,
)
from bigas.resources.product.create_jira_issue.service import (
    CreateJiraIssueError,
    CreateJiraIssueService,
)
from bigas.resources.product.create_jira_issue.lookup import (
    LookupJiraError,
    LookupJiraService,
)
from bigas.resources.product.create_release_notes.jira_client import normalize_project_keys
from bigas.resources.product.create_release_notes.service import CreateReleaseNotesService, ReleaseNotesError
from bigas.resources.product.jira_automation.service import (
    JiraAutomationError,
    JiraAutomationService,
    extract_webhook_secret_from_headers,
    parse_automation_payload,
    verify_webhook_secret,
)
from bigas.resources.product.progress_updates.service import ProgressUpdatesService, ProgressUpdatesError
from bigas.chat.activity import post_to_agent_thread
from bigas.resources.product.x_posts.service import (
    XPostsError,
    XPostsService,
    format_discord_message,
)


def _project_keys_from_request(data: dict):
    """Accept project_keys (list/str) or project_key (str) from request body."""
    if data.get("project_keys") is not None:
        return normalize_project_keys(data.get("project_keys"))
    if data.get("project_key") is not None:
        return normalize_project_keys(data.get("project_key"))
    return None

product_bp = Blueprint(
    'product_bp', __name__,
    url_prefix='/mcp/tools'
)

logger = logging.getLogger(__name__)

_JIRA_AI_JOBS: dict = {}
_JIRA_AI_JOBS_LOCK = threading.Lock()


def _jira_webhook_disabled_response():
    """404 the Jira Automation webhook unless Jira credentials are configured."""
    from bigas.tickets.config import jira_configured

    if jira_configured():
        return None
    return jsonify(
        {
            "error": "Jira webhook disabled",
            "reason": "Jira is not configured",
        }
    ), 404


def _post_to_discord(webhook_url: str, message: str) -> None:
    post_discord_message(webhook_url, message, chat_agent_id="product")


def _post_to_discord_in_chunks(webhook_url: str, message: str, *, chunk_size: int = 1900) -> None:
    """
    Post long content to Discord by splitting into multiple messages.
    Discord hard limit is 2000 chars; we keep a margin.
    """
    if not message:
        return
    post_long_to_discord(webhook_url, message, chunk_size=chunk_size, chat_agent_id="product")


@product_bp.route('/product_resource_placeholder', methods=['POST'])
def product_placeholder():
    """
    This is a placeholder for a future Product Management AI Resource.
    """
    return jsonify({
        "status": "placeholder",
        "message": "This endpoint is reserved for a future Product AI Resource.",
        "details": "Potential integrations: Jira, Asana, Figma, etc."
    })

@product_bp.route('/create_release_notes', methods=['POST'])
def create_release_notes():
    """
    Create release notes by querying Jira issues for a Fix Version.

    Request JSON:
      {
        "fix_version": "1.1.0",
        "jql_extra": "AND statusCategory = Done",
        "project_key": "VFA",
        "project_keys": ["VFA", "WAYW"]
      }
      (jql_extra / project_key(s) optional; default uses JIRA_PROJECT_KEY from env, supports "VFA,WAYW")
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data, required_fields=["fix_version"])
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    fix_version = data.get("fix_version")
    jql_extra = (data.get("jql_extra") or "").strip()
    project_keys = _project_keys_from_request(data)
    try:
        service = CreateReleaseNotesService()
        result = service.create(
            fix_version=fix_version,
            jql_extra=jql_extra,
            project_keys=project_keys,
        )

        # Optional: post the release notes to the product Discord channel
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_PRODUCT")
        if webhook_url:
            title = result.get("release_title") or f"Release {result.get('release_version', fix_version)}"
            sections = result.get("sections") or {}
            new_features = sections.get("features") or []
            improvements = sections.get("improvements") or []
            bug_fixes = sections.get("bug_fixes") or []

            lines = [f"# 🚀 {title}", ""]
            lines.append("**New features:**")
            lines.extend([f"- {x}" for x in new_features] or ["- (None)"])
            lines.append("")
            lines.append("**Improvements:**")
            lines.extend([f"- {x}" for x in improvements] or ["- (None)"])
            lines.append("")
            lines.append("**Bug Fixes:**")
            lines.extend([f"- {x}" for x in bug_fixes] or ["- (None)"])

            _post_to_discord_in_chunks(webhook_url, "\n".join(lines))

            # Add comms pack: social drafts + blog draft (as additional messages)
            social = result.get("social") or {}
            social_msg = "\n".join(
                [
                    "## 📣 Social drafts",
                    "",
                    f"**X:** {social.get('x','')}".strip(),
                    "",
                    f"**LinkedIn:** {social.get('linkedin','')}".strip(),
                    "",
                    f"**Facebook:** {social.get('facebook','')}".strip(),
                    "",
                    f"**Instagram:** {social.get('instagram','')}".strip(),
                ]
            ).strip()
            _post_to_discord_in_chunks(webhook_url, social_msg)

            blog = result.get("blog_markdown") or ""
            if blog:
                _post_to_discord_in_chunks(webhook_url, "## 📝 Proposed blog post\n\n" + blog)

        return jsonify(result)
    except ReleaseNotesError as e:
        # If it's our validation, treat as 400; otherwise 500.
        msg = str(e)
        status = 400 if any(
            s in msg.lower()
            for s in ["fix_version", "invalid", "missing required", "required"]
        ) else 500
        return jsonify({"error": sanitize_error_message(msg)}), status
    except Exception as e:
        logger.error("Error in create_release_notes", exc_info=True)
        return jsonify({"error": sanitize_error_message(str(e))}), 500


@product_bp.route('/progress_updates', methods=['POST'])
def progress_updates():
    """
    Generate a team progress update from issues moved to Done in the last N days
    (Jira or the internal board).
    Request JSON (optional):
      { "days": 7, "post_to_discord": true, "post_to_chat": true, "jql_extra": "...",
        "project_keys": ["VFA","WAYW"], "group_by": "label", "ignore_labels": ["customer-request"] }
    Default jql_extra is "AND statusCategory = Done". Specify the period with `days` (default 7).
    group_by=label groups by remaining ticket labels (Unlabeled bucket; customer-request ignored by default).
    When chat is enabled, the same message is posted to the Product Manager thread.
    """
    data = request.json or {}
    days = int(data.get("days", 7))
    if days < 1 or days > 365:
        return jsonify({"error": "days must be between 1 and 365"}), 400
    post_to_discord = bool(data.get("post_to_discord", True))
    post_to_chat = True if data.get("post_to_chat") is None else bool(data.get("post_to_chat"))
    # Default jql_extra for progress report: narrow to statusCategory = Done (can override via request).
    jql_extra = (data.get("jql_extra") or "AND statusCategory = Done").strip()
    project_keys = _project_keys_from_request(data)
    group_by = (data.get("group_by") or "project")
    ignore_labels = data.get("ignore_labels")
    if str(group_by).strip().lower() not in ("project", "label"):
        return jsonify({"error": "group_by must be 'project' or 'label'"}), 400

    try:
        service = ProgressUpdatesService()
        result = service.run(
            days=days,
            jql_extra=jql_extra,
            project_keys=project_keys,
            group_by=group_by,
            ignore_labels=ignore_labels,
        )

        message = result.get("message", "")
        if post_to_discord and message:
            webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_PRODUCT")
            if webhook_url:
                _post_to_discord_in_chunks(webhook_url, message)
                result["posted_to_discord"] = True
            else:
                result["posted_to_discord"] = False
        else:
            result["posted_to_discord"] = False
        if post_to_chat and message:
            chat_msg = post_to_agent_thread(
                "product",
                message,
                metadata={"source": "progress_updates"},
            )
            result["posted_to_chat"] = bool(chat_msg)
            if chat_msg:
                result["chat_thread_id"] = chat_msg.get("thread_id")
        else:
            result["posted_to_chat"] = False

        return jsonify(result)
    except ProgressUpdatesError as e:
        logger.warning("Progress updates error: %s", e)
        return jsonify({"error": sanitize_error_message(str(e))}), 500
    except Exception as e:
        logger.error("Error in progress_updates", exc_info=True)
        return jsonify({"error": sanitize_error_message(str(e))}), 500


@product_bp.route('/generate_weekly_x_post', methods=['POST'])
def generate_weekly_x_post():
    """
    Draft a weekly X post from recent git activity, store it, and send an
    approval link to marketing Discord and the Product Manager chat thread.
    Publishing happens only after a human approves.

    Request JSON (all optional):
      {
        "days": 7,
        "accounts": ["bigasmyaiteam"],
        "post_to_discord": true,
        "post_to_chat": true,
        "dry_run": false,
        "project_keys": ["BIG", "VFA"]
      }
    """
    data = request.json or {}
    try:
        days = int(data.get("days", 7))
    except (TypeError, ValueError):
        return jsonify({"error": "days must be an integer between 1 and 365"}), 400
    if days < 1 or days > 365:
        return jsonify({"error": "days must be between 1 and 365"}), 400
    post_to_discord = bool(data.get("post_to_discord", True))
    post_to_chat = True if data.get("post_to_chat") is None else bool(data.get("post_to_chat"))
    dry_run = bool(data.get("dry_run", False))
    accounts = data.get("accounts")
    if accounts is not None and not isinstance(accounts, list):
        return jsonify({"error": "accounts must be a list of X account names"}), 400
    tweets = data.get("tweets")
    if tweets is not None and not isinstance(tweets, list):
        return jsonify({"error": "tweets must be a list of strings"}), 400
    project_keys = _project_keys_from_request(data)

    try:
        service = XPostsService()
        result = service.generate(
            days=days,
            accounts=accounts,
            project_keys=project_keys,
            public_url=request.host_url,
            dry_run=dry_run,
            tweets=tweets,
        )
        message = format_discord_message(result)
        if post_to_discord:
            webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_MARKETING")
            if webhook_url:
                _post_to_discord_in_chunks(webhook_url, message)
                result["posted_to_discord"] = True
            else:
                result["posted_to_discord"] = False
        else:
            result["posted_to_discord"] = False
        if post_to_chat:
            chat_msg = post_to_agent_thread(
                "product",
                message,
                metadata={"source": "generate_weekly_x_post"},
            )
            result["posted_to_chat"] = bool(chat_msg)
            if chat_msg:
                result["chat_thread_id"] = chat_msg.get("thread_id")
        else:
            result["posted_to_chat"] = False
        return jsonify(result)
    except XPostsError as e:
        msg = str(e)
        status = 400 if any(
            s in msg.lower()
            for s in ["days must", "not configured", "no x credentials", "signing"]
        ) else 500
        return jsonify({"error": sanitize_error_message(msg)}), status
    except Exception as e:
        logger.error("Error in generate_weekly_x_post", exc_info=True)
        return jsonify({"error": sanitize_error_message(str(e))}), 500


@product_bp.route('/create_jira_issue', methods=['POST'])
@require_bigas_access_key
def create_jira_issue():
    """
    Create a Jira issue in the specified project.

    Request JSON:
      {
        "project_key": "BIG",
        "summary": "Ticket title",
        "description": "Task description (markdown supported)",
        "issue_type": "Task",
        "marketing": false,
        "parent_epic_key": "BIG-10"
      }

    Returns { "ok": true, "key": "BIG-42", "url": "https://..." } on success.
    Set marketing=true for marketing-related tickets (adds the Jira label "marketing").
    Optional parent_epic_key links the new Task/Bug to a goal Epic (never creates Epics).
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(
        data,
        required_fields=["project_key", "summary", "description"],
    )
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    issue_type = str(data.get("issue_type") or "Task").strip().title() or "Task"
    marketing = request_flag(data, "marketing", False)
    parent_epic_key = str(data.get("parent_epic_key") or "").strip() or None
    user_id = str(data.get("user_id") or "").strip() or None

    try:
        service = CreateJiraIssueService()
        result = service.create(
            project_key=data.get("project_key"),
            summary=data.get("summary"),
            description=data.get("description"),
            issue_type=issue_type,
            marketing=marketing,
            parent_epic_key=parent_epic_key,
            user_id=user_id,
        )
        return jsonify(result)
    except CreateJiraIssueError as e:
        msg = str(e)
        status = 400 if any(
            s in msg.lower()
            for s in [
                "required",
                "must be one of",
                "not found",
                "not accessible",
            ]
        ) else 500
        return jsonify({"ok": False, "error": sanitize_error_message(msg)}), status
    except Exception as e:
        logger.error("Error in create_jira_issue", exc_info=True)
        return jsonify({"ok": False, "error": sanitize_error_message(str(e))}), 500


@product_bp.route('/lookup_jira', methods=['POST'])
@require_bigas_access_key
def lookup_jira():
    """
    Look up one or more Jira issues (including parent Epic) and/or open Epics.

    Request JSON:
      {
        "issue_key": "BIG-15 to BIG-18",
        "issue_keys": ["GPWW-3", "GPWW-4"],
        "project_key": "GPWW"
      }

    At least one of issue_key / issue_keys or project_key is required.
    issue_key accepts a browse URL, comma-separated keys, or a range.
    """
    data = request.json or {}
    issue_key = (
        str(data.get("issue_key") or data.get("issue") or data.get("key") or "").strip()
        or None
    )
    issue_keys = data.get("issue_keys")
    project_key = str(data.get("project_key") or "").strip() or None
    try:
        result = LookupJiraService().lookup(
            issue_key=issue_key,
            issue_keys=issue_keys,
            project_key=project_key,
        )
        return jsonify(result)
    except LookupJiraError as e:
        msg = str(e)
        status = 400 if any(
            s in msg.lower()
            for s in [
                "required",
                "not found",
                "not accessible",
            ]
        ) else 500
        return jsonify({"ok": False, "error": sanitize_error_message(msg)}), status
    except Exception as e:
        logger.error("Error in lookup_jira", exc_info=True)
        return jsonify({"ok": False, "error": sanitize_error_message(str(e))}), 500


def _run_jira_automation_job(job_id: str, parsed: dict) -> None:
    with _JIRA_AI_JOBS_LOCK:
        _JIRA_AI_JOBS[job_id] = {"status": "running", **parsed}
    try:
        service = JiraAutomationService()
        result = service.handle_event(
            issue_key=parsed["issue_key"],
            to_status=parsed["to_status"],
            from_status=parsed.get("from_status") or "",
            project_key=parsed.get("project_key") or "",
            idempotency_key=parsed.get("idempotency_key") or "",
            sync=False,
        )
        with _JIRA_AI_JOBS_LOCK:
            _JIRA_AI_JOBS[job_id] = {"status": "done", "result": result}
    except Exception as e:
        logger.error("jira_status_automation job %s failed", job_id, exc_info=True)
        with _JIRA_AI_JOBS_LOCK:
            _JIRA_AI_JOBS[job_id] = {
                "status": "error",
                "error": sanitize_error_message(str(e)),
            }


@product_bp.route('/jira_status_automation', methods=['POST'])
def jira_status_automation():
    """
    Jira Automation webhook: when an issue enters an AI column, run the matching handler.

    Auth: header X-Bigas-Webhook-Secret (or Authorization: Bearer <secret>)
          must match JIRA_AUTOMATION_WEBHOOK_SECRET.
          Body webhook_secret is only accepted when
          BIGAS_JIRA_ALLOW_BODY_WEBHOOK_SECRET=1 (local curl).

    Body (flexible):
      {
        "issue_key": "VFA-1",
        "to_status": "Research and describe (AI)",
        "from_status": "To Do",
        "idempotency_key": "optional-unique-id",
        "sync": true
      }
    Or Jira-shaped: { "issue": { "key": "VFA-1", "fields": { "status": { "name": "..." } } } }

    Prefer "sync": true on Cloud Run (background threads are best-effort).
    Without sync, response is 202 + job_id; poll jira_status_automation_job.

    Disabled (404) when Jira is not configured — internal board drags use
    the authenticated loopback worker instead.
    """
    disabled = _jira_webhook_disabled_response()
    if disabled:
        return disabled

    data = request.json or {}
    try:
        service_cfg = JiraAutomationService().config
    except Exception as e:
        logger.error("jira_status_automation config error", exc_info=True)
        return jsonify({"error": sanitize_error_message(str(e))}), 500

    provided = extract_webhook_secret_from_headers(request.headers)
    allow_body_secret = (
        os.environ.get("BIGAS_JIRA_ALLOW_BODY_WEBHOOK_SECRET", "").strip().lower()
        in ("1", "true", "yes")
    )
    if not provided and allow_body_secret:
        provided = str(data.get("webhook_secret") or "").strip()

    if not verify_webhook_secret(provided, service_cfg.webhook_secret):
        return jsonify({"error": "unauthorized"}), 401

    parsed = parse_automation_payload(data)
    if not parsed.get("issue_key") or not parsed.get("to_status"):
        return jsonify({
            "error": "issue_key and to_status are required",
            "hint": "Send issue_key + to_status, or issue.key + issue.fields.status.name",
        }), 400

    # Default sync=true for Cloud Run reliability; Automation can set sync=false
    # if it needs a fast 202 ack (background work requires CPU-always / min instances).
    sync = bool(data.get("sync", True))
    if sync:
        try:
            result = JiraAutomationService().handle_event(
                issue_key=parsed["issue_key"],
                to_status=parsed["to_status"],
                from_status=parsed.get("from_status") or "",
                project_key=parsed.get("project_key") or "",
                idempotency_key=parsed.get("idempotency_key") or "",
                sync=True,
            )
            status = 200 if result.get("ok", True) else 500
            return jsonify(result), status
        except JiraAutomationError as e:
            return jsonify({"error": sanitize_error_message(str(e))}), 400
        except Exception as e:
            logger.error("jira_status_automation sync failed", exc_info=True)
            return jsonify({"error": sanitize_error_message(str(e))}), 500

    job_id = str(uuid.uuid4())
    with _JIRA_AI_JOBS_LOCK:
        _JIRA_AI_JOBS[job_id] = {"status": "queued", **parsed}
    t = threading.Thread(
        target=_run_jira_automation_job,
        args=(job_id, parsed),
        daemon=True,
        name=f"jira-ai-{job_id[:8]}",
    )
    t.start()
    return jsonify({
        "ok": True,
        "accepted": True,
        "job_id": job_id,
        "issue_key": parsed["issue_key"],
        "to_status": parsed["to_status"],
        "warning": (
            "async jobs are process-local and best-effort on Cloud Run; "
            "prefer sync=true unless CPU-always-allocated + min instances are set"
        ),
    }), 202


@product_bp.route('/jira_status_automation_job', methods=['POST'])
def jira_status_automation_job():
    """
    Poll a background jira_status_automation job: { "job_id": "..." }.
    Requires the same X-Bigas-Webhook-Secret as the webhook endpoint.
    Disabled (404) when Jira is not configured.
    """
    disabled = _jira_webhook_disabled_response()
    if disabled:
        return disabled

    data = request.json or {}
    try:
        service_cfg = JiraAutomationService().config
    except Exception as e:
        logger.error("jira_status_automation_job config error", exc_info=True)
        return jsonify({"error": sanitize_error_message(str(e))}), 500

    provided = extract_webhook_secret_from_headers(request.headers)
    allow_body_secret = (
        os.environ.get("BIGAS_JIRA_ALLOW_BODY_WEBHOOK_SECRET", "").strip().lower()
        in ("1", "true", "yes")
    )
    if not provided and allow_body_secret:
        provided = str(data.get("webhook_secret") or "").strip()
    if not verify_webhook_secret(provided, service_cfg.webhook_secret):
        return jsonify({"error": "unauthorized"}), 401

    job_id = str(data.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id is required"}), 400
    with _JIRA_AI_JOBS_LOCK:
        job = _JIRA_AI_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify({"job_id": job_id, **job})


def get_manifest():
    """Returns the manifest for the product tools."""
    manifest = {
        "name": "Product Tools",
        "description": "Tools for product management.",
        "tools": [
            {
                "name": "product_resource_placeholder",
                "description": "Placeholder for a future Product Management AI Resource.",
                "path": "/mcp/tools/product_resource_placeholder",
                "method": "POST"
            },
            {
                "name": "create_release_notes",
                "description": "Query Jira by Fix Version and generate multi-channel release notes.",
                "path": "/mcp/tools/create_release_notes",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fix_version": {"type": "string", "description": "Jira Fix Version, e.g. 1.1.0"},
                        "jql_extra": {"type": "string", "description": "Optional JQL fragment to narrow results (e.g. AND statusCategory = Done)"},
                        "project_key": {"type": "string", "description": "Optional single Jira project key override (e.g. VFA)"},
                        "project_keys": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional Jira project keys override (e.g. [\"VFA\",\"WAYW\"]). Defaults to JIRA_PROJECT_KEY env (supports comma-separated)."
                        }
                    },
                    "required": ["fix_version"]
                }
            },
            {
                "name": "progress_updates",
                "description": "Generate a team progress update from issues moved to Done in the last N days (Jira or the internal board). Optional Discord and Product Manager chat post. Set group_by=label to group by ticket labels (ignores customer-request by default, Unlabeled bucket). Do not invent product-area headings. Specify days (default 7). Uses jql_extra default AND statusCategory = Done on Jira. Supports project_keys or JIRA_PROJECT_KEY=VFA,WAYW.",
                "path": "/mcp/tools/progress_updates",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {"type": "integer", "description": "Number of days to look back (default 7)", "default": 7},
                        "post_to_discord": {"type": "boolean", "description": "Post the message to product Discord webhook", "default": True},
                        "post_to_chat": {"type": "boolean", "description": "Post the message to the Product Manager thread in bigas-chat", "default": True},
                        "jql_extra": {"type": "string", "description": "JQL fragment to narrow results; default AND statusCategory = Done", "default": "AND statusCategory = Done"},
                        "project_key": {"type": "string", "description": "Optional single Jira project key override (e.g. WAYW)"},
                        "project_keys": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional Jira project keys override. Defaults to all keys in JIRA_PROJECT_KEY env."
                        },
                        "group_by": {
                            "type": "string",
                            "enum": ["project", "label"],
                            "description": "How to group Done items. Use label when the user asks to group by Jira/board labels, ignore Customer request, or put unlabeled items in Unlabeled. Default project.",
                            "default": "project"
                        },
                        "ignore_labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Labels to skip as group headings when group_by=label. Default [\"customer-request\"]. Tickets still appear under remaining labels or Unlabeled."
                        }
                    }
                }
            },
            {
                "name": "generate_weekly_x_post",
                "description": "Draft a weekly X post from recent git merges, filter out minor fixes, and send an approval link to marketing Discord and the Product Manager chat. Publishing happens only after a human approves or declines.",
                "path": "/mcp/tools/generate_weekly_x_post",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {"type": "integer", "description": "Lookback window in days (default 7)", "default": 7},
                        "accounts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "X account names to publish to. Defaults to X_ACCOUNTS."
                        },
                        "post_to_discord": {"type": "boolean", "description": "Post the draft (or skip notice) to marketing Discord", "default": True},
                        "post_to_chat": {"type": "boolean", "description": "Post the draft (or skip notice) to the Product Manager thread in bigas-chat", "default": True},
                        "dry_run": {"type": "boolean", "description": "Return the draft without storing it or creating an approval link", "default": False},
                        "tweets": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional tweet thread to post as-is (skips git/LLM filtering). Each string max 280 chars."
                        },
                        "project_key": {"type": "string", "description": "Optional single Jira project key override"},
                        "project_keys": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional Jira project keys whose mapped GitHub repos are included."
                        }
                    }
                }
            },
            {
                "name": "create_jira_issue",
                "description": (
                    "Create a new Jira issue in the specified project. "
                    "Available to every chat agent and MCP client. "
                    "Returns the issue key (e.g. BIG-42) and browse URL. "
                    "For marketing-related tickets (website, SEO, content, ads), set marketing=true "
                    "to add the Jira label \"marketing\" (no other labels are needed)."
                ),
                "path": "/mcp/tools/create_jira_issue",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_key": {
                            "type": "string",
                            "description": "Jira project key, e.g. BIG, VFA, WAYW",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Ticket title",
                        },
                        "description": {
                            "type": "string",
                            "description": "Task description (markdown supported)",
                        },
                        "issue_type": {
                            "type": "string",
                            "description": "Issue type name (Task or Bug). Default Task.",
                            "default": "Task",
                            "enum": ["Task", "Bug"],
                        },
                        "marketing": {
                            "type": "boolean",
                            "description": (
                                "When true, adds the Jira label \"marketing\" for marketing-related work "
                                "(website, SEO, content, ads). Default false."
                            ),
                            "default": False,
                        },
                        "parent_epic_key": {
                            "type": "string",
                            "description": (
                                "Optional existing Epic key only (e.g. GPWW-2). "
                                "Omit this field to create a standalone Task/Bug — that is the default. "
                                "Do not pass a Task/Bug key or guess a parent."
                            ),
                        },
                    },
                    "required": ["project_key", "summary", "description"],
                },
            },
            {
                "name": "lookup_jira",
                "description": (
                    "Look up Jira issues (summary, type, status, parent Epic) and/or list "
                    "open Epics in a project. Accepts one key, several keys, or a range "
                    "(e.g. BIG-15 to BIG-18). Use this to answer status questions, then write "
                    "the answer yourself — do not paste this dump as the reply. "
                    "Also use before create_jira_issue when you need Epic context. "
                    "A parent on a referenced ticket is not automatically the parent "
                    "for a new ticket — only link parent_epic_key if the new work belongs under "
                    "that Epic; otherwise create a standalone Task/Bug."
                ),
                "path": "/mcp/tools/lookup_jira",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {
                            "type": "string",
                            "description": (
                                "Issue key, browse URL, comma-separated keys, or a range "
                                "like BIG-15 to BIG-18."
                            ),
                        },
                        "issue_keys": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional extra issue keys to look up together.",
                        },
                        "project_key": {
                            "type": "string",
                            "description": "Project whose open Epics should be listed, e.g. GPWW.",
                        },
                    },
                },
            },
        ]
    }
    from bigas.tickets.config import jira_configured

    if jira_configured():
        manifest["tools"].extend(
            [
                {
                    "name": "jira_status_automation",
                    "description": "Jira Automation webhook for AI columns. Research and describe → Description approval + Discord PM; Design and plan → Design approval + Discord CTO. Auth via X-Bigas-Webhook-Secret.",
                    "path": "/mcp/tools/jira_status_automation",
                    "method": "POST",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "issue_key": {"type": "string", "description": "Jira issue key, e.g. VFA-1"},
                            "to_status": {"type": "string", "description": "Status the issue was moved into"},
                            "from_status": {"type": "string"},
                            "idempotency_key": {"type": "string"},
                            "sync": {
                                "type": "boolean",
                                "description": "If true, run inline and return result (recommended on Cloud Run). Default true. Set false for 202 + job_id (best-effort).",
                                "default": True,
                            },
                        },
                        "required": ["issue_key", "to_status"],
                    },
                },
                {
                    "name": "jira_status_automation_job",
                    "description": "Poll status/result for a jira_status_automation background job.",
                    "path": "/mcp/tools/jira_status_automation_job",
                    "method": "POST",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "job_id": {"type": "string"},
                        },
                        "required": ["job_id"],
                    },
                },
            ]
        )
    return manifest
