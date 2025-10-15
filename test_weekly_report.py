#!/usr/bin/env python3
"""
Test script to debug weekly report generation and see what's actually being returned.
"""

import json
import os
import sys
from datetime import datetime

# Add the bigas package to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bigas'))

def test_weekly_report_generation():
    """Test the weekly report generation process."""
    
    print("🧪 TESTING WEEKLY REPORT GENERATION")
    print("=" * 50)
    
    try:
        # Import the necessary modules
        from bigas.resources.marketing.endpoints import weekly_analytics_report
        from flask import Flask, request, jsonify
        
        # Create a test Flask app
        app = Flask(__name__)
        
        # Test data
        test_data = {
            "return_full_report": True,
            "post_to_discord": False,
            "credentials": {
                "ga4_property_id": "test_property_id",
                "discord_webhook_url": "",
                "target_keywords": "test:keywords"
            }
        }
        
        print(f"Test data: {json.dumps(test_data, indent=2)}")
        
        # Mock the request
        with app.test_request_context(json=test_data):
            # Call the function
            response = weekly_analytics_report()
            
            print(f"Response type: {type(response)}")
            print(f"Response: {response}")
            
            if hasattr(response, 'json'):
                response_data = response.json
                print(f"Response data: {json.dumps(response_data, indent=2)}")
                
                # Check if we got the full report
                if "report" in response_data:
                    report = response_data["report"]
                    print(f"✅ Full report received with keys: {list(report.keys())}")
                    
                    # Check each field
                    for key in ["summary", "questions", "structured_recommendations", "metrics_summary"]:
                        if key in report:
                            print(f"✅ {key}: Present")
                            if key == "summary":
                                print(f"   Summary: {report[key][:100]}...")
                            elif key == "questions":
                                print(f"   Questions count: {len(report[key])}")
                            elif key == "structured_recommendations":
                                print(f"   Recommendations count: {len(report[key])}")
                            elif key == "metrics_summary":
                                print(f"   Metrics: {report[key]}")
                        else:
                            print(f"❌ {key}: Missing")
                else:
                    print("❌ No 'report' field in response")
                    print(f"Available fields: {list(response_data.keys())}")
            else:
                print(f"Response is not JSON: {response}")
                
    except Exception as e:
        print(f"❌ Error during test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_weekly_report_generation()




