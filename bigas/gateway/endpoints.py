"""Cockpit API for the agent gateway: connect agents, change levels, approve requests.

Signed-in owner only (same login as the cockpit). When ``CHAT_ADMIN_EMAILS`` is
set, only those accounts may manage agents and approve.
"""
from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from bigas.chat.auth import chat_admin_emails, is_chat_admin, require_chat_auth
from bigas.gateway import policy
from bigas.gateway import service as gw
from bigas.mcp_urls import mcp_public_base_url

gateway_bp = Blueprint("gateway_bp", __name__, url_prefix="/api/gateway")


def _owner_error():
    user = getattr(g, "chat_user", None) or {}
    if chat_admin_emails() and not is_chat_admin(user):
        return jsonify({"error": "Only an admin can manage outside agents."}), 403
    if not gw.gateway_enabled():
        return jsonify({"error": "The agent gateway is off. Set BIGAS_AGENT_GATEWAY=on to use it."}), 409
    return None


def _who() -> str:
    user = getattr(g, "chat_user", None) or {}
    return str(user.get("email") or user.get("uid") or "")


def _tool_names() -> list:
    """Tool names from the live manifest (empty if it cannot be built)."""
    try:
        with current_app.test_client() as client:
            header = current_app.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
            keys = sorted(current_app.config.get("BIGAS_ACCESS_KEYS") or [])
            resp = client.get("/mcp/manifest", headers={header: keys[0]} if keys else {})
            data = resp.get_json(silent=True) or {}
        return sorted({t.get("name") for t in data.get("tools", []) if isinstance(t, dict) and t.get("name")})
    except Exception:
        return []


def _err(exc: Exception, status: int = 400):
    return jsonify({"error": str(exc)}), status


@gateway_bp.route("", methods=["GET"])
@require_chat_auth
def overview():
    """Everything the cockpit shows: on/off, pause switch, agents, pending approvals, levels."""
    if not gw.gateway_enabled():
        return jsonify({"enabled": False})
    err = _owner_error()
    if err:
        return err
    return jsonify(
        {
            "enabled": True,
            "paused": gw.is_paused(),
            "agents": [gw.agent_view(a) for a in gw.list_agents()],
            "approvals": [gw.approval_view(a) for a in gw.pending_approvals()],
            "levels": [
                {"id": key, "label": policy.LEVELS[key]["label"], "summary": policy.LEVELS[key]["summary"]}
                for key in policy.LEVEL_ORDER
            ],
        }
    )


@gateway_bp.route("/agents", methods=["POST"])
@require_chat_auth
def connect():
    err = _owner_error()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    try:
        agent, token = gw.connect_agent(body.get("name") or "", body.get("level") or "", created_by=_who())
    except gw.GatewayError as exc:
        return _err(exc)
    return jsonify(
        {
            "agent": gw.agent_view(agent),
            "token": token,
            "mcp_url": f"{mcp_public_base_url()}/mcp",
            "instructions": gw.connect_instructions(token, mcp_public_base_url()),
        }
    ), 201


@gateway_bp.route("/agents/<agent_id>", methods=["GET"])
@require_chat_auth
def agent_detail(agent_id: str):
    err = _owner_error()
    if err:
        return err
    agent = gw.get_agent(agent_id)
    if not agent:
        return jsonify({"error": "No such agent."}), 404
    return jsonify(
        {
            "agent": gw.agent_view(agent, _tool_names()),
            "activity": gw.recent_activity(agent_id),
        }
    )


@gateway_bp.route("/agents/<agent_id>", methods=["PATCH"])
@require_chat_auth
def change_agent(agent_id: str):
    err = _owner_error()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    try:
        kwargs = {
            "level": body.get("level"),
            "name": body.get("name"),
            "suspended": body.get("paused"),
            "allow_tools": body.get("allow_tools"),
            "deny_tools": body.get("deny_tools"),
        }
        if "limits" in body:
            kwargs["limits"] = body.get("limits")
        agent = gw.update_agent(agent_id, **kwargs)
    except gw.GatewayError as exc:
        return _err(exc, 404 if "No agent" in str(exc) else 400)
    return jsonify({"agent": gw.agent_view(agent)})


@gateway_bp.route("/agents/<agent_id>", methods=["DELETE"])
@require_chat_auth
def delete_agent(agent_id: str):
    err = _owner_error()
    if err:
        return err
    try:
        gw.remove_agent(agent_id)
    except gw.GatewayError as exc:
        return _err(exc, 404)
    return jsonify({"removed": agent_id})


@gateway_bp.route("/agents/<agent_id>/token", methods=["POST"])
@require_chat_auth
def rotate(agent_id: str):
    err = _owner_error()
    if err:
        return err
    try:
        token = gw.new_token(agent_id)
    except gw.GatewayError as exc:
        return _err(exc, 404)
    return jsonify(
        {
            "token": token,
            "mcp_url": f"{mcp_public_base_url()}/mcp",
            "instructions": gw.connect_instructions(token, mcp_public_base_url()),
        }
    )


@gateway_bp.route("/pause", methods=["POST"])
@require_chat_auth
def pause():
    err = _owner_error()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    settings = gw.set_paused(bool(body.get("paused", True)), by=_who())
    return jsonify({"paused": bool(settings.get("paused"))})


@gateway_bp.route("/approvals/<approval_id>/approve", methods=["POST"])
@require_chat_auth
def approve(approval_id: str):
    err = _owner_error()
    if err:
        return err
    try:
        approval = gw.approve_approval(
            approval_id, by=_who(), dispatch=gw.dispatch_with_owner_key(current_app._get_current_object())
        )
    except gw.GatewayError as exc:
        return _err(exc, 409)
    return jsonify({"approval": gw.approval_view(approval)})


@gateway_bp.route("/approvals/<approval_id>/deny", methods=["POST"])
@require_chat_auth
def deny(approval_id: str):
    err = _owner_error()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    try:
        approval = gw.deny_approval(approval_id, by=_who(), note=body.get("note") or "")
    except gw.GatewayError as exc:
        return _err(exc, 409)
    return jsonify({"approval": gw.approval_view(approval)})
