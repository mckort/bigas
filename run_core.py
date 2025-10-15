#!/usr/bin/env python3
"""
Startup script for Bigas Core Flask app.
Run this to start the HTTP API that exposes core functionality.
"""

import os
from app import create_app

if __name__ == "__main__":
    print("🚀 Starting Bigas Core Flask server...")
    print("📡 API will be available at: http://localhost:8080")
    print("🔍 Weekly report endpoint: http://localhost:8080/mcp/tools/weekly_analytics_report")
    print("📚 OpenAPI spec at: http://localhost:8080/openapi.json")
    print("\nPress Ctrl+C to stop the server")
    
    app = create_app()
    app.run(debug=False, host='0.0.0.0', port=8080)
