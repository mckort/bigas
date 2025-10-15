#!/usr/bin/env python3
"""
Test script to check if GA4 service is working and can access data.
"""

import os
import sys
from datetime import datetime

# Add the bigas package to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bigas'))

def test_ga4_access():
    """Test if GA4 service can access data."""
    
    print("🧪 TESTING GA4 ACCESS")
    print("=" * 50)
    
    try:
        # Check environment variables
        ga4_property_id = os.environ.get("GA4_PROPERTY_ID")
        openai_api_key = os.environ.get("OPENAI_API_KEY")
        
        print(f"GA4 Property ID: {ga4_property_id}")
        print(f"OpenAI API Key: {'✅ Set' if openai_api_key else '❌ Not set'}")
        
        if not ga4_property_id:
            print("❌ GA4_PROPERTY_ID not set")
            return
        
        if not openai_api_key:
            print("❌ OPENAI_API_KEY not set")
            return
        
        # Import the service
        from bigas.resources.marketing.service import MarketingAnalyticsService
        
        print("✅ MarketingAnalyticsService imported successfully")
        
        # Create service instance
        service = MarketingAnalyticsService(openai_api_key)
        print("✅ Service instance created successfully")
        
        # Test a simple template query
        print("\n🔄 Testing template query: session_quality")
        try:
            result = service.template_service.run_template_query("session_quality", ga4_property_id)
            print(f"✅ Template query successful")
            print(f"Result keys: {list(result.keys()) if result else 'None'}")
            if result and "rows" in result:
                print(f"Rows count: {len(result['rows'])}")
                if result['rows']:
                    print(f"First row: {result['rows'][0]}")
            else:
                print("❌ No rows in result")
        except Exception as e:
            print(f"❌ Template query failed: {e}")
            import traceback
            traceback.print_exc()
        
        # Test trend analysis
        print("\n🔄 Testing trend analysis")
        try:
            result = service.trend_analysis_service.get_weekly_trend_analysis(ga4_property_id)
            print(f"✅ Trend analysis successful")
            print(f"Result keys: {list(result.keys()) if result else 'None'}")
            if result and "ai_insights" in result:
                print(f"AI insights: {result['ai_insights'][:100]}...")
            else:
                print("❌ No AI insights in result")
        except Exception as e:
            print(f"❌ Trend analysis failed: {e}")
            import traceback
            traceback.print_exc()
        
        # Test traffic sources
        print("\n🔄 Testing traffic sources")
        try:
            result = service.answer_traffic_sources(ga4_property_id)
            print(f"✅ Traffic sources successful")
            print(f"Result: {result[:100]}...")
        except Exception as e:
            print(f"❌ Traffic sources failed: {e}")
            import traceback
            traceback.print_exc()
            
    except Exception as e:
        print(f"❌ Error during test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_ga4_access()




