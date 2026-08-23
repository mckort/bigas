import os
import json
import logging
from flask import Flask, jsonify, request
from dotenv import load_dotenv

from bigas.registry import registry

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_MCP_SERVER_URL = "https://mcp-marketing-343105851187.europe-north1.run.app"


def _jsonrpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id, code: int, message: str, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": err}


def _unauthorized(payload):
    """401 with WWW-Authenticate so MCP clients treat this as Bearer auth, not OAuth discovery."""
    response = jsonify(payload)
    response.status_code = 401
    response.headers["WWW-Authenticate"] = 'Bearer realm="bigas-mcp"'
    return response


def _mcp_base_url():
    base = (os.environ.get("SERVER_URL") or "").strip().rstrip("/")
    if base:
        return base
    try:
        return (request.host_url or "").rstrip("/")
    except RuntimeError:
        return DEFAULT_MCP_SERVER_URL


def _mcp_server_card(app: Flask):
    base = _mcp_base_url()
    header_name = app.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
    restricted = app.config.get("BIGAS_ACCESS_MODE") == "restricted"
    return {
        "name": "Bigas Modular AI Agent",
        "description": "Marketing and product analytics tools for GA4, ad platforms, Jira, and Discord.",
        "schemaVersion": "1.0",
        "transport": {
            "type": "http",
            "baseUrl": base,
            "manifestUrl": f"{base}/mcp/manifest",
            "openapiUrl": f"{base}/openapi.json",
        },
        "auth": {
            "type": "api_key",
            "location": "header",
            "header": header_name,
            "optional": not restricted,
        },
        "tags": ["marketing", "product", "analytics"],
    }


def _load_access_control_config(app: Flask) -> None:
    """
    Load simple access control configuration from environment variables and
    attach it to the Flask app config.

    BIGAS_ACCESS_MODE:
        - "open" (default): no access key required.
        - "restricted": require a valid access key on protected routes.

    BIGAS_ACCESS_KEYS:
        - Comma-separated list of allowed access keys.
        - Required when BIGAS_ACCESS_MODE="restricted".

    BIGAS_ACCESS_HEADER:
        - HTTP header name to read the access key from.
        - Defaults to "X-Bigas-Access-Key".
    """
    mode = os.environ.get("BIGAS_ACCESS_MODE", "open").strip().lower() or "open"
    if mode not in ("open", "restricted"):
        raise ValueError("BIGAS_ACCESS_MODE must be either 'open' or 'restricted'.")

    header_name = os.environ.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key").strip() or "X-Bigas-Access-Key"

    raw_keys = os.environ.get("BIGAS_ACCESS_KEYS", "")
    keys = {k.strip() for k in raw_keys.split(",") if k.strip()}

    if mode == "restricted" and not keys:
        raise ValueError(
            "BIGAS_ACCESS_MODE is set to 'restricted' but no BIGAS_ACCESS_KEYS are configured."
        )

    app.config["BIGAS_ACCESS_MODE"] = mode
    app.config["BIGAS_ACCESS_HEADER"] = header_name
    app.config["BIGAS_ACCESS_KEYS"] = keys

    logger.info("Access control mode set to '%s'. Protected routes will require header '%s' when restricted.", mode, header_name)


def create_app():
    """Create and configure an instance of the Flask application."""
    # Local development convenience: load `.env` if present.
    # In Cloud Run / production, environment variables are typically injected by the platform.
    load_dotenv(override=False)

    # Standalone + SECRET_MANAGER=true: overlay env from Google Secret Manager (one secret, JSON key-value map).
    try:
        from bigas.secrets import load_secrets_from_secret_manager
        load_secrets_from_secret_manager()
    except Exception as e:
        logger.warning("Secrets loader failed (continuing with existing env): %s", e)

    app = Flask(__name__)

    # Check deployment mode
    deployment_mode = os.environ.get("DEPLOYMENT_MODE", "standalone")

    # Ensure environment variables are set based on deployment mode
    if deployment_mode == "saas":
        # In SaaS mode, GA4_PROPERTY_ID comes from the SaaS layer per-company
        logger.info("Running in SaaS mode - GA4_PROPERTY_ID will be provided per-request")
    else:
        # In standalone/CLI mode, GA4_PROPERTY_ID must be set
        if not os.environ.get("GA4_PROPERTY_ID"):
            raise ValueError("GA4_PROPERTY_ID environment variable not set.")

    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY environment variable not set. LLM features will not work.")

    # Load simple access control configuration (open vs restricted)
    _load_access_control_config(app)

    with app.app_context():
        # Import and register blueprints from each resource
        from bigas.resources.marketing.endpoints import marketing_bp, get_manifest as get_marketing_manifest
        from bigas.resources.product.endpoints import product_bp, get_manifest as get_product_manifest
        from bigas.resources.product.x_posts.endpoints import x_posts_bp
        from bigas.resources.cto.endpoints import cto_bp, get_manifest as get_cto_manifest
        from bigas.resources.cto.qa_agent.endpoints import qa_proposals_bp
        from bigas.resources.devops.endpoints import devops_bp, get_manifest as get_devops_manifest

        app.register_blueprint(marketing_bp)
        app.register_blueprint(product_bp)
        app.register_blueprint(x_posts_bp)
        app.register_blueprint(qa_proposals_bp)
        app.register_blueprint(cto_bp)
        app.register_blueprint(devops_bp)

        get_chat_manifest = None
        if os.environ.get("CHAT_ENABLED", "true").strip().lower() in ("1", "true", "yes"):
            from bigas.resources.chat.endpoints import chat_bp, get_manifest as get_chat_manifest

            app.register_blueprint(chat_bp)
            logger.info("Registered chat blueprint.")

            from bigas.resources.tickets.endpoints import tickets_bp

            app.register_blueprint(tickets_bp)
            logger.info("Registered tickets blueprint.")

        from bigas.resources.email.endpoints import email_bp

        app.register_blueprint(email_bp)
        logger.info("Registered email ingest blueprint.")

        logger.info("Registered marketing blueprint.")
        logger.info("Registered product blueprint.")
        logger.info("Registered X-post approval blueprint.")
        logger.info("Registered QA proposal approval blueprint.")
        logger.info("Registered CTO blueprint.")
        logger.info("Registered DevOps blueprint.")

    # Discover configured providers once at startup
    try:
        registry.discover()
    except Exception as e:
        logger.warning("Provider registry discovery failed (continuing without providers): %s", e)

    # Paths that should always remain public, even in restricted mode.
    # POST /mcp still checks the access key inside the handler.
    from bigas.resources.chat.endpoints import BRAND_ICON_FILES

    public_paths = {
        "/health",
        "/mcp",
        "/mcp/manifest",
        "/mcp/providers",
        "/openapi.json",
        "/api/auth/config",
    }

    def _is_public_path(path: str) -> bool:
        # Scheduler webhook: same X-Bigas-Access-Key as other cron jobs.
        if path.rstrip("/") == "/api/agents/evaluate-goals":
            return False
        return (
            path in public_paths
            or path == "/"
            or path == "/board"
            or path.startswith("/board/")
            or path.startswith("/api/x-posts")
            or path.startswith("/api/qa-proposals")
            or path.startswith("/api/chat/")
            or path.startswith("/api/v1/chat/")
            or path == "/api/boards"
            or path.startswith("/api/boards/")
            or path == "/api/tickets"
            or path.startswith("/api/tickets/")
            or path.startswith("/api/agents")
            or path.startswith("/api/feed")
            or path.startswith("/api/auth/")
            or path.startswith("/assets/")
            or path.startswith("/.well-known/")
            or path.lstrip("/") in BRAND_ICON_FILES
        )

    @app.before_request
    def _enforce_access_key():
        """
        Enforce a simple shared access key when BIGAS_ACCESS_MODE is "restricted".
        The key is expected in the configured HTTP header. If missing or invalid,
        the request is rejected before handlers run.
        """
        if request.path.rstrip("/") == "/api/agents/evaluate-goals":
            from bigas.access import verify_evaluate_goals_webhook_auth

            err = verify_evaluate_goals_webhook_auth()
            if err is not None:
                return err
            return

        mode = app.config.get("BIGAS_ACCESS_MODE", "open")
        if mode != "restricted":
            return

        # Allow health checks, manifest, and MCP discovery without a key
        if _is_public_path(request.path):
            return

        header_name = app.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
        expected_keys = app.config.get("BIGAS_ACCESS_KEYS") or set()

        provided_key = (
            request.headers.get(header_name)
            or request.args.get("access_key")
            or (request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip() or None)
        )
        if not provided_key or provided_key not in expected_keys:
            logger.warning(
                "Rejected request to %s due to invalid or missing access key (header: %s).",
                request.path,
                header_name,
            )
            return _unauthorized({"detail": "Invalid or missing access key"})

    @app.route('/health', methods=['GET'])
    def health_check():
        """Health check endpoint for Cloud Run startup probes."""
        return jsonify({"status": "healthy", "service": "bigas-core"})

    @app.route("/mcp/providers", methods=["GET"])
    def providers_status():
        """Return provider discovery status for all domains."""
        return jsonify(registry.status())

    @app.route('/mcp/manifest', methods=['GET'])
    def combined_manifest():
        """
        Dynamically generates a combined manifest from all registered resources.
        """
        marketing_manifest = {}
        product_manifest = {}
        cto_manifest = {}
        devops_manifest = {}
        chat_manifest = {}

        try:
            marketing_manifest = get_marketing_manifest() or {}
        except Exception:
            logger.exception("Failed to build marketing manifest")

        try:
            product_manifest = get_product_manifest() or {}
        except Exception:
            logger.exception("Failed to build product manifest")

        try:
            cto_manifest = get_cto_manifest() or {}
        except Exception:
            logger.exception("Failed to build CTO manifest")

        try:
            devops_manifest = get_devops_manifest() or {}
        except Exception:
            logger.exception("Failed to build DevOps manifest")

        if get_chat_manifest is not None:
            try:
                chat_manifest = get_chat_manifest() or {}
            except Exception:
                logger.exception("Failed to build chat manifest")

        # Combine the tools from all manifests
        all_tools = (
            marketing_manifest.get('tools', [])
            + product_manifest.get('tools', [])
            + cto_manifest.get('tools', [])
            + devops_manifest.get('tools', [])
            + chat_manifest.get('tools', [])
        )

        # Create the combined manifest
        manifest = {
            "name": "Bigas Modular AI Agent",
            "version": "1.1",
            "description": "A multi-resource AI agent for marketing, product, CTO, and DevOps operations.",
            "tools": all_tools
        }
        return jsonify(manifest)

    register_mcp_jsonrpc_routes(app, lambda: combined_manifest().get_json() or {})
    return app


def register_mcp_jsonrpc_routes(app: Flask, get_manifest_json):
    """
    Streamable HTTP MCP: POST handles JSON-RPC. GET returns 405 so clients do not
    open a long-lived SSE stream that would block gunicorn's worker.
    """

    @app.route("/mcp", methods=["GET", "POST"])
    def mcp_endpoint():
        if request.method == "GET":
            response = jsonify({"error": "Method Not Allowed. Use POST /mcp for JSON-RPC."})
            response.status_code = 405
            response.headers["Allow"] = "POST"
            return response

        mode = app.config.get("BIGAS_ACCESS_MODE", "open")
        header_name = app.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
        expected_keys = app.config.get("BIGAS_ACCESS_KEYS") or set()

        provided_key = (
            request.headers.get(header_name)
            or request.args.get("access_key")
            or (request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip() or None)
        )
        if mode == "restricted" and (not provided_key or provided_key not in expected_keys):
            return _unauthorized({"error": "Invalid or missing access key for /mcp"})

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(_jsonrpc_error(None, -32600, "Invalid Request: expected JSON object")), 400

        request_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}

        if method == "initialize":
            protocol_version = params.get("protocolVersion") or "2025-03-26"
            return jsonify(
                _jsonrpc_result(
                    request_id,
                    {
                        "protocolVersion": protocol_version,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "bigas-mcp", "version": "1.1"},
                    },
                )
            )

        if method == "notifications/initialized":
            if request_id is not None:
                return jsonify(_jsonrpc_result(request_id, {}))
            return "", 204

        if method == "tools/list":
            manifest = get_manifest_json() or {}
            tools = []
            for tool in manifest.get("tools", []):
                if not isinstance(tool, dict):
                    continue
                tools.append(
                    {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "inputSchema": tool.get("parameters")
                        or {"type": "object", "properties": {}},
                    }
                )
            return jsonify(_jsonrpc_result(request_id, {"tools": tools}))

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}
            if not tool_name:
                return jsonify(_jsonrpc_error(request_id, -32602, "Missing tool name in tools/call"))

            manifest = get_manifest_json() or {}
            manifest_tools = manifest.get("tools", [])
            selected = next((t for t in manifest_tools if isinstance(t, dict) and t.get("name") == tool_name), None)
            if not selected:
                return jsonify(_jsonrpc_error(request_id, -32601, f"Tool not found: {tool_name}"))

            tool_path = selected.get("path")
            tool_method = (selected.get("method") or "POST").upper()
            if not tool_path:
                return jsonify(_jsonrpc_error(request_id, -32603, f"Tool path missing for: {tool_name}"))

            headers = {}
            if mode == "restricted" and provided_key:
                headers[header_name] = provided_key

            with app.test_client() as client:
                if tool_method == "GET":
                    tool_resp = client.open(tool_path, method="GET", headers=headers, query_string=arguments)
                else:
                    tool_resp = client.open(tool_path, method=tool_method, headers=headers, json=arguments)

            response_text = tool_resp.get_data(as_text=True)
            response_json = None
            if tool_resp.is_json:
                response_json = tool_resp.get_json()
                summary = None
                if isinstance(response_json, dict):
                    summary = response_json.get("summary")
                if isinstance(summary, str) and summary.strip():
                    response_text = summary.strip()
                else:
                    response_text = json.dumps(response_json, ensure_ascii=False)

            result = {
                "content": [{"type": "text", "text": response_text}],
                "isError": tool_resp.status_code >= 400,
            }
            if response_json is not None:
                result["structuredContent"] = response_json

            return jsonify(_jsonrpc_result(request_id, result))

        return jsonify(_jsonrpc_error(request_id, -32601, f"Method not found: {method}")), 404

    @app.route("/.well-known/mcp.json", methods=["GET"])
    def well_known_mcp():
        """Expose the MCP server card at the standard well-known location."""
        return jsonify(_mcp_server_card(app))

    @app.route("/.well-known/oauth-authorization-server", methods=["GET"])
    @app.route("/.well-known/oauth-protected-resource", methods=["GET"])
    def oauth_not_configured():
        """Bigas uses a static access key, not OAuth. Return 404 instead of 401."""
        return jsonify({"error": "oauth_not_supported"}), 404

if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=False, host='0.0.0.0', port=port) 