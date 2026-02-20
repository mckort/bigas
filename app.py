import os
import json
import logging
from flask import Flask, jsonify, request
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _jsonrpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id, code: int, message: str, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": err}


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

        app.register_blueprint(marketing_bp)
        app.register_blueprint(product_bp)

        logger.info("Registered marketing blueprint.")
        logger.info("Registered product blueprint.")

    # Paths that should always remain public, even in restricted mode
    public_paths = {"/", "/mcp", "/mcp/manifest", "/.well-known/mcp.json", "/openapi.json"}

    @app.before_request
    def _enforce_access_key():
        """
        Enforce a simple shared access key when BIGAS_ACCESS_MODE is "restricted".
        The key is expected in the configured HTTP header. If missing or invalid,
        the request is rejected before handlers run.
        """
        mode = app.config.get("BIGAS_ACCESS_MODE", "open")
        if mode != "restricted":
            return

        # Allow health checks and manifest without a key
        if request.path in public_paths:
            return

        header_name = app.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
        expected_keys = app.config.get("BIGAS_ACCESS_KEYS") or set()

        provided_key = request.headers.get(header_name)
        if not provided_key or provided_key not in expected_keys:
            logger.warning(
                "Rejected request to %s due to invalid or missing access key (header: %s).",
                request.path,
                header_name,
            )
            return jsonify({"detail": "Invalid or missing access key"}), 401

    @app.route('/', methods=['GET'])
    def health_check():
        """Health check endpoint for Cloud Run startup probes."""
        return jsonify({"status": "healthy", "service": "bigas-core"})

    @app.route('/mcp/manifest', methods=['GET'])
    def combined_manifest():
        """
        Dynamically generates a combined manifest from all registered resources.
        """
        marketing_manifest = {}
        product_manifest = {}

        try:
            marketing_manifest = get_marketing_manifest() or {}
        except Exception:
            logger.exception("Failed to build marketing manifest")

        try:
            product_manifest = get_product_manifest() or {}
        except Exception:
            logger.exception("Failed to build product manifest")

        # Combine the tools from all manifests
        all_tools = marketing_manifest.get('tools', []) + product_manifest.get('tools', [])

        # Create the combined manifest
        manifest = {
            "name": "Bigas Modular AI Agent",
            "version": "1.1",
            "description": "A multi-resource AI agent for marketing and product analytics.",
            "tools": all_tools
        }
        return jsonify(manifest)

    @app.route('/mcp', methods=['GET', 'POST'])
    def mcp_endpoint():
        """
        Minimal MCP Streamable-HTTP compatible JSON-RPC endpoint.
        Supports initialize, tools/list, and tools/call.
        """
        if request.method == "GET":
            return jsonify(
                {
                    "service": "bigas-mcp",
                    "status": "ok",
                    "endpoint": "/mcp",
                    "note": "Send JSON-RPC requests as HTTP POST to use MCP tools.",
                }
            )

        # Local auth for /mcp itself. We keep /mcp in public_paths to support clients
        # that cannot set custom headers during discovery and bootstrap.
        mode = app.config.get("BIGAS_ACCESS_MODE", "open")
        header_name = app.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
        expected_keys = app.config.get("BIGAS_ACCESS_KEYS") or set()

        provided_key = (
            request.headers.get(header_name)
            or request.args.get("access_key")
            or (request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip() or None)
        )
        if mode == "restricted" and (not provided_key or provided_key not in expected_keys):
            return jsonify({"error": "Invalid or missing access key for /mcp"}), 401

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

        if method == "tools/list":
            manifest = combined_manifest().get_json() or {}
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

            manifest = combined_manifest().get_json() or {}
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
                response_text = json.dumps(response_json, ensure_ascii=False)

            result = {
                "content": [{"type": "text", "text": response_text}],
                "isError": tool_resp.status_code >= 400,
            }
            if response_json is not None:
                result["structuredContent"] = response_json

            return jsonify(_jsonrpc_result(request_id, result))

        return jsonify(_jsonrpc_error(request_id, -32601, f"Method not found: {method}")), 404

    @app.route('/.well-known/mcp.json', methods=['GET'])
    def well_known_mcp():
        """
        Expose the MCP server card at the standard well-known location.
        """
        try:
            base_dir = os.path.dirname(__file__)
            card_path = os.path.join(base_dir, "mcp.json")
            with open(card_path, "r", encoding="utf-8") as f:
                card = json.load(f)
            return jsonify(card)
        except Exception as exc:
            logger.error("Failed to load MCP server card: %s", exc)
            return jsonify({"error": "MCP server card not available"}), 500

    return app

if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=False, host='0.0.0.0', port=port) 