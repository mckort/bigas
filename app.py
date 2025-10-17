import os
import logging
from flask import Flask, jsonify

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app():
    """Create and configure an instance of the Flask application."""
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

    with app.app_context():
        # Import and register blueprints from each resource
        from bigas.resources.marketing.endpoints import marketing_bp, get_manifest as get_marketing_manifest
        from bigas.resources.product.endpoints import product_bp, get_manifest as get_product_manifest

        app.register_blueprint(marketing_bp)
        app.register_blueprint(product_bp)

        logger.info("Registered marketing blueprint.")
        logger.info("Registered product blueprint.")

    @app.route('/', methods=['GET'])
    def health_check():
        """Health check endpoint for Cloud Run startup probes."""
        return jsonify({"status": "healthy", "service": "bigas-core"})
    
    @app.route('/mcp/manifest', methods=['GET'])
    def combined_manifest():
        """
        Dynamically generates a combined manifest from all registered resources.
        """
        marketing_manifest = get_marketing_manifest()
        product_manifest = get_product_manifest()

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

    return app

if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=False, host='0.0.0.0', port=port) 