#!/usr/bin/env python3
"""
Test script for the new comprehensive analytics endpoint.
This tests the optimization that reduces GA API calls from 8+ to just 1.

Tests both SaaS mode (with credentials) and standalone mode (environment variables).
"""

import requests
import json
import os
from datetime import datetime

def test_comprehensive_analytics_saas_mode():
    """Test the comprehensive analytics endpoint in SaaS mode (with credentials)."""
    
    # Configuration
    base_url = "http://localhost:5000"  # Adjust if your bigas-core runs on different port
    endpoint = "/comprehensive-analytics"
    
    # Test data
    test_payload = {
        "credentials": {
            "ga4_property_id": os.environ.get("GA4_PROPERTY_ID", "test_property"),
            "openai_api_key": os.environ.get("OPENAI_API_KEY", "test_key")
        },
        "date_range": {
            "start_date": "30daysAgo",
            "end_date": "today"
        }
    }
    
    print(f"🧪 Testing comprehensive analytics endpoint (SaaS mode)...")
    print(f"📡 URL: {base_url}{endpoint}")
    print(f"📊 Test payload: {json.dumps(test_payload, indent=2)}")
    
    try:
        # Make the request
        response = requests.post(
            f"{base_url}{endpoint}",
            json=test_payload,
            timeout=30
        )
        
        print(f"\n📥 Response Status: {response.status_code}")
        print(f"📥 Response Headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ SUCCESS! Comprehensive analytics endpoint working (SaaS mode)!")
            print(f"📊 Response structure: {list(data.keys())}")
            
            if "data" in data:
                response_data = data["data"]
                print(f"📋 Data summary: {response_data.get('summary', {})}")
                print(f"🔢 Total rows: {response_data.get('summary', {}).get('total_rows', 0)}")
                print(f"🚦 Traffic sources: {list(response_data.get('extracted_data', {}).get('traffic_sources', {}).keys())}")
                print(f"📅 Daily traffic days: {len(response_data.get('extracted_data', {}).get('daily_traffic', []))}")
                
                # Show sample of raw data
                raw_data = response_data.get('raw_data', {})
                if 'rows' in raw_data and raw_data['rows']:
                    print(f"\n📊 Sample raw data (first row):")
                    first_row = raw_data['rows'][0]
                    print(f"  Dimensions: {first_row.get('dimensionValues', [])}")
                    print(f"  Metrics: {first_row.get('metricValues', [])}")
                    
        else:
            print(f"\n❌ ERROR: {response.status_code}")
            try:
                error_data = response.json()
                print(f"Error details: {json.dumps(error_data, indent=2)}")
            except:
                print(f"Error text: {response.text}")
                
    except requests.exceptions.ConnectionError:
        print(f"\n❌ CONNECTION ERROR: Could not connect to {base_url}")
        print("Make sure bigas-core is running and accessible")
    except requests.exceptions.Timeout:
        print(f"\n❌ TIMEOUT: Request took too long (>30 seconds)")
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {type(e).__name__}: {e}")

def test_comprehensive_analytics_standalone_mode():
    """Test the comprehensive analytics endpoint in standalone mode (environment variables)."""
    
    # Configuration
    base_url = "http://localhost:5000"  # Adjust if your bigas-core runs on different port
    endpoint = "/comprehensive-analytics/standalone"
    
    print(f"\n🧪 Testing comprehensive analytics endpoint (Standalone mode)...")
    print(f"📡 URL: {base_url}{endpoint}")
    print(f"🔑 Using environment variables: GA4_PROPERTY_ID={os.environ.get('GA4_PROPERTY_ID', 'NOT SET')}")
    
    try:
        # Make the request (GET method, no payload needed)
        response = requests.get(
            f"{base_url}{endpoint}",
            timeout=30
        )
        
        print(f"\n📥 Response Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ SUCCESS! Comprehensive analytics endpoint working (Standalone mode)!")
            print(f"📊 Response structure: {list(data.keys())}")
            print(f"🔧 Mode: {data.get('mode', 'unknown')}")
            
            if "data" in data:
                response_data = data["data"]
                print(f"📋 Data summary: {response_data.get('summary', {})}")
                print(f"🔢 Total rows: {response_data.get('summary', {}).get('total_rows', 0)}")
                print(f"🚦 Traffic sources: {list(response_data.get('extracted_data', {}).get('traffic_sources', {}).keys())}")
                print(f"📅 Daily traffic days: {len(response_data.get('extracted_data', {}).get('daily_traffic', []))}")
                
                # Show sample of raw data
                raw_data = response_data.get('raw_data', {})
                if 'rows' in raw_data and raw_data['rows']:
                    print(f"\n📊 Sample raw data (first row):")
                    first_row = raw_data['rows'][0]
                    print(f"  Dimensions: {first_row.get('dimensionValues', [])}")
                    print(f"  Metrics: {first_row.get('metricValues', [])}")
                    
        else:
            print(f"\n❌ ERROR: {response.status_code}")
            try:
                error_data = response.json()
                print(f"Error details: {json.dumps(error_data, indent=2)}")
            except:
                print(f"Error text: {response.text}")
                
    except requests.exceptions.ConnectionError:
        print(f"\n❌ CONNECTION ERROR: Could not connect to {base_url}")
        print("Make sure bigas-core is running and accessible")
    except requests.exceptions.Timeout:
        print(f"\n❌ TIMEOUT: Request took too long (>30 seconds)")
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {type(e).__name__}: {e}")

def test_weekly_report_optimization():
    """Test that the weekly report now uses comprehensive data."""
    
    base_url = "http://localhost:5000"
    endpoint = "/mcp/tools/weekly_analytics_report"
    
    test_payload = {
        "credentials": {
            "ga4_property_id": os.environ.get("GA4_PROPERTY_ID", "test_property"),
            "openai_api_key": os.environ.get("OPENAI_API_KEY", "test_key"),
            "discord_webhook_url": os.environ.get("DISCORD_WEBHOOK_URL", "test_webhook")
        },
        "return_full_report": True,
        "request_id": f"test_{datetime.now().isoformat()}",
        "timestamp": datetime.now().isoformat()
    }
    
    print(f"\n🧪 Testing weekly report optimization...")
    print(f"📡 URL: {base_url}{endpoint}")
    print(f"🎯 This should now use comprehensive data instead of 8+ API calls")
    
    try:
        response = requests.post(
            f"{base_url}{endpoint}",
            json=test_payload,
            timeout=60  # Weekly report takes longer
        )
        
        print(f"\n📥 Response Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ SUCCESS! Weekly report optimization working!")
            
            if "report" in data:
                report = data["report"]
                print(f"📊 Report contains comprehensive data: {'comprehensive_data' in report}")
                print(f"📊 Report contains extracted data: {'extracted_data' in report}")
                
                if "comprehensive_data" in report:
                    comp_data = report["comprehensive_data"]
                    print(f"🔢 Comprehensive data rows: {len(comp_data.get('rows', []))}")
                    
                if "extracted_data" in report:
                    extracted = report["extracted_data"]
                    print(f"🚦 Traffic sources extracted: {list(extracted.get('traffic_sources', {}).keys())}")
                    print(f"📅 Daily traffic extracted: {len(extracted.get('daily_traffic', []))}")
        else:
            print(f"❌ ERROR: {response.status_code}")
            try:
                error_data = response.json()
                print(f"Error details: {json.dumps(error_data, indent=2)}")
            except:
                print(f"Error text: {response.text}")
                
    except Exception as e:
        print(f"\n❌ ERROR: {type(e).__name__}: {e}")

def test_curl_examples():
    """Show curl examples for testing."""
    
    print(f"\n🔧 CURL Examples for Testing")
    print("=" * 50)
    
    print(f"\n1. SaaS Mode (with credentials):")
    print(f"curl -X POST http://localhost:5000/comprehensive-analytics \\")
    print(f"  -H 'Content-Type: application/json' \\")
    print(f"  -d '{{")
    print(f"    \"credentials\": {{")
    print(f"      \"ga4_property_id\": \"your_property_id\"")
    print(f"    }}")
    print(f"  }}'")
    
    print(f"\n2. Standalone Mode (environment variables):")
    print(f"curl -X GET http://localhost:5000/comprehensive-analytics/standalone")
    
    print(f"\n3. Weekly Report (SaaS mode):")
    print(f"curl -X POST http://localhost:5000/mcp/tools/weekly_analytics_report \\")
    print(f"  -H 'Content-Type: application/json' \\")
    print(f"  -d '{{")
    print(f"    \"credentials\": {{")
    print(f"      \"ga4_property_id\": \"your_property_id\",")
    print(f"      \"openai_api_key\": \"your_openai_key\",")
    print(f"      \"discord_webhook_url\": \"your_webhook_url\"")
    print(f"    }}, ")
    print(f"    \"return_full_report\": true")
    print(f"  }}'")

if __name__ == "__main__":
    print("🚀 Testing Bigas-Core Comprehensive Analytics Optimization")
    print("=" * 70)
    
    # Test 1: Comprehensive analytics endpoint (SaaS mode)
    test_comprehensive_analytics_saas_mode()
    
    print("\n" + "=" * 70)
    
    # Test 2: Comprehensive analytics endpoint (Standalone mode)
    test_comprehensive_analytics_standalone_mode()
    
    print("\n" + "=" * 70)
    
    # Test 3: Weekly report optimization
    test_weekly_report_optimization()
    
    print("\n" + "=" * 70)
    
    # Show curl examples
    test_curl_examples()
    
    print("\n" + "=" * 70)
    print("🏁 Testing complete!")
    print("\n💡 Expected results:")
    print("• Comprehensive analytics endpoint should work in both SaaS and standalone modes")
    print("• Weekly report should use comprehensive data instead of 8+ API calls")
    print("• Cost reduction: ~92% fewer GA API calls")
    print("• Proper fallback: credentials -> environment variables")
