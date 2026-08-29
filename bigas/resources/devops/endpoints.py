"""DevOps specialist MCP tool endpoints."""
from __future__ import annotations

import logging
import uuid

from flask import Blueprint, jsonify, request

from bigas.resources.devops.service import (
    DevOpsError,
    check_deployment_risk,
    check_website_health,
    create_github_pr,
    fetch_github_action_logs,
    get_commit_diff,
    get_deployment_status,
    trigger_deployment,
)
from bigas.resources.devops.self_healing import (
    SelfHealingError,
    _run_self_healing_job,
    enqueue_self_healing,
    get_self_healing_job,
    parse_workflow_run_payload,
    self_healing_enabled,
    should_process_workflow_run,
    verify_github_signature,
    webhook_secret,
)
from bigas.resources.marketing.utils import sanitize_error_message, validate_request_data
from bigas.resources.product.jira_automation.service import (
    extract_webhook_secret_from_headers,
    verify_webhook_secret,
)

devops_bp = Blueprint("devops_bp", __name__, url_prefix="/mcp/tools")
logger = logging.getLogger(__name__)


@devops_bp.route("/check_deployment_risk", methods=["POST"])
def check_deployment_risk_endpoint():
    """
    Assess production deployment risk by comparing git refs for migration/config changes.

    Request JSON (all optional except one of repo or project_key when target is not in maps):
      {
        "repo": "owner/repo",
        "project_key": "VFA",
        "base_ref": "v1.2.0",
        "head_ref": "main",
        "github_token": "optional override"
      }
    """
    data = request.json or {}
    try:
        result = check_deployment_risk(
            repo=data.get("repo"),
            project_key=data.get("project_key"),
            base_ref=data.get("base_ref"),
            head_ref=data.get("head_ref"),
            github_token=data.get("github_token"),
        )
        return jsonify(result)
    except DevOpsError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("check_deployment_risk failed")
        return jsonify({"error": sanitize_error_message(str(e))}), 500


@devops_bp.route("/trigger_deployment", methods=["POST"])
def trigger_deployment_endpoint():
    """
    Trigger GitHub Actions deployment workflow(s) via workflow_dispatch.

    Request JSON:
      {
        "project_key": "VFA",
        "repo": "owner/repo",
        "workflows": ["deploy-backend.yml", "deploy-web.yml"],
        "ref": "main",
        "github_token": "optional override"
      }
    """
    data = request.json or {}
    workflows = data.get("workflows")
    if isinstance(workflows, str):
        workflows = [w.strip() for w in workflows.split(",") if w.strip()]
    try:
        result = trigger_deployment(
            repo=data.get("repo"),
            project_key=data.get("project_key"),
            workflows=workflows,
            ref=data.get("ref"),
            github_token=data.get("github_token"),
        )
        return jsonify(result)
    except DevOpsError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("trigger_deployment failed")
        return jsonify({"error": sanitize_error_message(str(e))}), 500


@devops_bp.route("/get_deployment_status", methods=["POST"])
def get_deployment_status_endpoint():
    """
    Get status of a GitHub Actions workflow run.

    Request JSON:
      {"repo": "owner/repo", "run_id": 12345678}
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data, required_fields=["repo", "run_id"])
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    try:
        run_id = int(data["run_id"])
    except (TypeError, ValueError):
        return jsonify({"error": "run_id must be a valid integer"}), 400
    try:
        result = get_deployment_status(
            repo=data["repo"],
            run_id=run_id,
            github_token=data.get("github_token"),
        )
        return jsonify(result)
    except DevOpsError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("get_deployment_status failed")
        return jsonify({"error": sanitize_error_message(str(e))}), 500


@devops_bp.route("/fetch_github_action_logs", methods=["POST"])
def fetch_github_action_logs_endpoint():
    """
    Download and parse logs from failed jobs on a GitHub Actions workflow run.

    Request JSON:
      {"repo": "owner/repo", "run_id": 12345678}
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data, required_fields=["repo", "run_id"])
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    try:
        run_id = int(data["run_id"])
    except (TypeError, ValueError):
        return jsonify({"error": "run_id must be a valid integer"}), 400
    try:
        result = fetch_github_action_logs(
            repo=data["repo"],
            run_id=run_id,
            github_token=data.get("github_token"),
        )
        return jsonify(result)
    except DevOpsError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("fetch_github_action_logs failed")
        return jsonify({"error": sanitize_error_message(str(e))}), 500


@devops_bp.route("/create_github_pr", methods=["POST"])
def create_github_pr_endpoint():
    """
    Create a hotfix branch with file changes and open a pull request.

    Request JSON:
      {
        "repo": "owner/repo",
        "base_branch": "main",
        "new_branch_name": "bigas-hotfix/run-123",
        "title": "fix(ci): ...",
        "body": "Root cause ...",
        "files_to_change": {"path/to/file.py": "full new contents"}
      }
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(
        data,
        required_fields=["repo", "new_branch_name", "title", "files_to_change"],
    )
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    files = data.get("files_to_change")
    if not isinstance(files, dict) or not files:
        return jsonify({"error": "files_to_change must be a non-empty object"}), 400
    try:
        result = create_github_pr(
            repo=data["repo"],
            base_branch=data.get("base_branch") or "main",
            new_branch_name=data["new_branch_name"],
            title=data["title"],
            body=data.get("body") or "",
            files_to_change={str(k): str(v) for k, v in files.items()},
            github_token=data.get("github_token"),
        )
        return jsonify(result)
    except DevOpsError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("create_github_pr failed")
        return jsonify({"error": sanitize_error_message(str(e))}), 500


@devops_bp.route("/github_workflow_run", methods=["POST"])
def github_workflow_run_webhook():
    """
    GitHub webhook for workflow_run events (self-healing CI/CD).

    Configure the repo webhook with content type application/json and
    subscribe to Workflow runs. Verifies X-Hub-Signature-256 when
    GITHUB_WEBHOOK_SECRET is set, or X-Bigas-Webhook-Secret otherwise.

    Only processes completed runs with conclusion failure. Ignores branches
    starting with bigas-hotfix/ to prevent infinite fix loops.
    """
    payload_bytes = request.get_data() or b""
    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}

    secret = webhook_secret()
    if not secret:
        logger.error("github_workflow_run webhook rejected: GITHUB_WEBHOOK_SECRET not configured")
        return jsonify({"error": "webhook secret not configured"}), 503

    sig_ok = verify_github_signature(
        payload_bytes,
        request.headers.get("X-Hub-Signature-256"),
        secret,
    )
    header_secret = extract_webhook_secret_from_headers(request.headers)
    header_ok = verify_webhook_secret(header_secret, secret)
    if not sig_ok and not header_ok:
        return jsonify({"error": "unauthorized"}), 401

    event = (request.headers.get("X-GitHub-Event") or "").strip().lower()
    if event and event != "workflow_run":
        return jsonify({"ok": True, "ignored": True, "reason": f"event:{event}"}), 200

    should_run, reason = should_process_workflow_run(payload if isinstance(payload, dict) else {})
    if not should_run:
        return jsonify({"ok": True, "ignored": True, "reason": reason}), 200

    if not self_healing_enabled():
        return jsonify({"ok": True, "ignored": True, "reason": "self_healing_disabled"}), 200

    try:
        context = parse_workflow_run_payload(payload)
    except (TypeError, ValueError) as e:
        return jsonify({"error": sanitize_error_message(str(e))}), 400

    sync = bool((payload or {}).get("sync", False))
    if sync:
        try:
            from bigas.resources.devops.self_healing import run_self_healing_fix

            result = run_self_healing_fix(
                repo=context["repo"],
                run_id=int(context["run_id"]),
                head_sha=context.get("head_sha") or "",
                head_branch=context.get("head_branch") or "main",
                workflow_name=context.get("workflow_name") or "workflow",
                html_url=context.get("html_url") or "",
                github_token=(payload or {}).get("github_token"),
            )
            return jsonify({"ok": True, **result}), 200
        except (DevOpsError, SelfHealingError) as e:
            return jsonify({"error": sanitize_error_message(str(e))}), 400
        except Exception as e:
            logger.exception("github_workflow_run sync failed")
            return jsonify({"error": sanitize_error_message(str(e))}), 500

    job_id = str(uuid.uuid4())
    enqueue_self_healing(context, job_id=job_id)
    return jsonify({
        "ok": True,
        "accepted": True,
        "job_id": job_id,
        **context,
        "warning": (
            "Async self-healing runs in a separate HTTP worker request (Cloud Run-safe). "
            "Poll self_healing_ci_job or set sync=true for inline processing."
        ),
    }), 202


@devops_bp.route("/self_healing_ci_worker", methods=["POST"])
def self_healing_ci_worker_endpoint():
    """
    Internal worker endpoint for async self-healing CI jobs.

    Dispatched via HTTP from enqueue_self_healing so Cloud Run allocates CPU
    for the full LLM + GitHub workflow instead of relying on background threads.
    """
    secret = webhook_secret()
    header_secret = extract_webhook_secret_from_headers(request.headers)
    if not secret or not verify_webhook_secret(header_secret, secret):
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    job_id = (data.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    context = {
        key: data[key]
        for key in ("repo", "run_id", "head_sha", "head_branch", "workflow_name", "html_url", "github_token")
        if key in data
    }
    if not context.get("repo") or not context.get("run_id"):
        return jsonify({"error": "repo and run_id are required"}), 400

    _run_self_healing_job(job_id, context)
    job = get_self_healing_job(job_id) or {}
    status = job.get("status") or "unknown"
    if status == "error":
        return jsonify({"ok": False, "job_id": job_id, **job}), 500
    return jsonify({"ok": True, "job_id": job_id, **job}), 200


@devops_bp.route("/self_healing_ci_job", methods=["POST"])
def self_healing_ci_job_endpoint():
    """Poll status of an async self-healing CI job."""
    data = request.json or {}
    job_id = (data.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id is required"}), 400
    job = get_self_healing_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


@devops_bp.route("/check_website_health", methods=["POST"])
def check_website_health_endpoint():
    """
    Perform a post-deployment HTTP health check on a URL.

    Request JSON:
      {"url": "https://vcfieldassistant.com"}
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data, required_fields=["url"])
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    try:
        result = check_website_health(data["url"])
        return jsonify(result)
    except DevOpsError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("check_website_health failed")
        return jsonify({"error": sanitize_error_message(str(e))}), 500


def get_manifest():
    """Return the DevOps tools manifest for the combined MCP manifest."""
    return {
        "name": "DevOps Tools",
        "description": (
            "Pre-flight deployment risk checks, GitHub Actions triggers, post-deploy health checks, "
            "CI log fetching, and self-healing hotfix PR creation."
        ),
        "tools": [
            {
                "name": "check_deployment_risk",
                "description": (
                    "Assess deployment risk before production: compares git refs and flags "
                    "database migrations, dependency changes, and deploy/infrastructure config."
                ),
                "path": "/mcp/tools/check_deployment_risk",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository owner/repo (optional if project_key is mapped)",
                        },
                        "project_key": {
                            "type": "string",
                            "description": "Jira project key e.g. VFA (resolves repo from BIGAS_JIRA_PROJECT_REPO_MAP)",
                        },
                        "base_ref": {
                            "type": "string",
                            "description": "Base ref/tag to compare from (default: latest deploy-backend/web tags)",
                        },
                        "head_ref": {
                            "type": "string",
                            "description": "Head ref to deploy (default: default branch)",
                        },
                    },
                },
            },
            {
                "name": "trigger_deployment",
                "description": (
                    "Trigger GitHub Actions deployment workflow(s) via workflow_dispatch. "
                    "VFA runs separate backend and web workflows. BIG dispatches deploy.yml "
                    "on mckort/bigas (Cloud Run). GPWW/FYDA/REM/MYL dispatch deploy.yml on "
                    "the VM infra repo when BIGAS_DEPLOY_REPO_MAP is set."
                ),
                "path": "/mcp/tools/trigger_deployment",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_key": {
                            "type": "string",
                            "description": "Jira project key e.g. VFA",
                        },
                        "repo": {
                            "type": "string",
                            "description": "Repository owner/repo (optional if project_key is mapped)",
                        },
                        "workflows": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Workflow filenames to trigger (default from BIGAS_DEPLOY_WORKFLOW_MAP)",
                        },
                        "ref": {
                            "type": "string",
                            "description": "Git ref/branch to deploy (default: default branch)",
                        },
                    },
                },
            },
            {
                "name": "get_deployment_status",
                "description": "Check status of a GitHub Actions workflow run by run ID.",
                "path": "/mcp/tools/get_deployment_status",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository owner/repo (required)",
                        },
                        "run_id": {
                            "type": "integer",
                            "description": "GitHub Actions run ID (required)",
                        },
                    },
                    "required": ["repo", "run_id"],
                },
            },
            {
                "name": "check_website_health",
                "description": (
                    "Post-deployment HTTP health check: GET the URL and report status code and response time."
                ),
                "path": "/mcp/tools/check_website_health",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Site URL to check e.g. https://vcfieldassistant.com (required)",
                        },
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "fetch_github_action_logs",
                "description": (
                    "Download and parse error logs from failed jobs on a GitHub Actions workflow run. "
                    "Uses the run logs zip when small enough, otherwise fetches per-job logs."
                ),
                "path": "/mcp/tools/fetch_github_action_logs",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository owner/repo (required)",
                        },
                        "run_id": {
                            "type": "integer",
                            "description": "GitHub Actions run ID (required)",
                        },
                    },
                    "required": ["repo", "run_id"],
                },
            },
            {
                "name": "create_github_pr",
                "description": (
                    "Create a hotfix branch with updated file contents and open a pull request. "
                    "Branch name must start with bigas-hotfix/."
                ),
                "path": "/mcp/tools/create_github_pr",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "Repository owner/repo (required)"},
                        "base_branch": {
                            "type": "string",
                            "description": "Base branch for the PR (default: repo default branch)",
                        },
                        "new_branch_name": {
                            "type": "string",
                            "description": "New branch name; must start with bigas-hotfix/ (required)",
                        },
                        "title": {"type": "string", "description": "PR title (required)"},
                        "body": {"type": "string", "description": "PR description"},
                        "files_to_change": {
                            "type": "object",
                            "description": "Map of file path → full new file contents (required)",
                        },
                    },
                    "required": ["repo", "new_branch_name", "title", "files_to_change"],
                },
            },
            {
                "name": "github_workflow_run",
                "description": (
                    "GitHub webhook endpoint for workflow_run failures. Autonomously opens hotfix PRs "
                    "via the DevOps agent. Configure in GitHub repo Settings → Webhooks."
                ),
                "path": "/mcp/tools/github_workflow_run",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sync": {
                            "type": "boolean",
                            "description": "Run synchronously (default false — returns 202 + job_id)",
                        },
                    },
                },
            },
        ],
    }
