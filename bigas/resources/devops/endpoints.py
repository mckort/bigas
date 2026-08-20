"""DevOps specialist MCP tool endpoints."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from bigas.resources.devops.service import (
    DevOpsError,
    check_deployment_risk,
    check_website_health,
    get_deployment_status,
    trigger_deployment,
)
from bigas.resources.marketing.utils import sanitize_error_message, validate_request_data

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
        "description": "Pre-flight deployment risk checks, GitHub Actions triggers, and post-deploy health checks.",
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
                            "description": "Base ref/tag to compare from (default: latest release tag or default branch)",
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
                    "VFA runs separate backend and web workflows when configured in BIGAS_DEPLOY_WORKFLOW_MAP."
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
        ],
    }
