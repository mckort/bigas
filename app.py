import os
import json
import logging
from flask import Flask, jsonify, request
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
    public_paths = {"/", "/mcp/manifest", "/.well-known/mcp.json"}

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