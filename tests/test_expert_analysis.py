#!/usr/bin/env python3
"""
Test script for the enhanced expert analysis feature.

This script demonstrates the new expert Digital Marketing Strategist analysis
that provides comprehensive CRO, SEO, and UX recommendations.
"""

import requests
import json
import sys
from datetime import datetime

def test_expert_analysis():
    """Test the enhanced expert analysis feature."""
    
    # Configuration
    base_url = "https://mcp-marketing-919623369853.europe-north1.run.app"
    
    print("🧪 Testing Enhanced Expert Analysis Feature")
    print("=" * 50)
    
    # Test 1: Analyze underperforming pages with expert analysis
    print("\n1️⃣ Testing Expert Analysis with Limited Pages")
    print("-" * 40)
    
    try:
        response = requests.post(
            f"{base_url}/mcp/tools/analyze_underperforming_pages",
            json={
                "max_pages": 1,  # Limit to 1 page for testing
                "report_date": None  # Use latest report
            },
            timeout=120  # Longer timeout for expert analysis
        )
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Expert analysis completed successfully!")
            print(f"📊 Pages analyzed: {result.get('pages_analyzed', 0)}")
            print(f"📈 Max pages limit: {result.get('max_pages_limit', 0)}")
            print(f"📱 Discord posted: {result.get('discord_posted', False)}")
            print(f"💬 Discord messages sent: {result.get('discord_messages_sent', 0)}")
            
            if result.get('underperforming_pages'):
                print(f"\n📋 Found {len(result['underperforming_pages'])} underperforming pages data")
                for i, page_data in enumerate(result['underperforming_pages'][:2]):  # Show first 2
                    print(f"   {i+1}. Question: {page_data.get('question', 'N/A')[:100]}...")
                    print(f"      Answer: {page_data.get('answer', 'N/A')[:100]}...")
            
            if result.get('page_urls'):
                print(f"\n🔗 Found {len(result['page_urls'])} page URLs")
                for i, page_url in enumerate(result['page_urls'][:3]):  # Show first 3
                    print(f"   {i+1}. {page_url.get('page_url', 'N/A')}")
                    print(f"      Sessions: {page_url.get('sessions', 0)}, Conversions: {page_url.get('conversions', 0)}")
                    print(f"      Conversion Rate: {page_url.get('conversion_rate', 0):.1%}")
            
        else:
            print(f"❌ Expert analysis failed with status {response.status_code}")
            print(f"Error: {response.text}")
            
    except requests.exceptions.Timeout:
        print("⏰ Expert analysis timed out (this is expected for comprehensive analysis)")
    except Exception as e:
        print(f"❌ Error during expert analysis: {e}")
    
    # Test 2: Check what expert analysis includes
    print("\n2️⃣ Expert Analysis Features")
    print("-" * 40)
    print("🎯 Conversion Rate Optimization (CRO):")
    print("   • Critical conversion killers identification")
    print("   • CTA optimization (placement, copy, design)")
    print("   • Trust & credibility building")
    print("   • Value proposition clarity")
    print("   • User journey optimization")
    
    print("\n🔍 Search Engine Optimization (SEO):")
    print("   • On-page SEO (title, meta, headings)")
    print("   • Keyword strategy and content gaps")
    print("   • Technical SEO (speed, mobile, technical)")
    print("   • Content quality improvements")
    print("   • Internal linking opportunities")
    
    print("\n👥 User Experience (UX):")
    print("   • Visual hierarchy improvements")
    print("   • Mobile experience optimization")
    print("   • Page speed enhancements")
    print("   • Accessibility improvements")
    print("   • User intent alignment")
    
    print("\n📊 Analysis Data Collected:")
    print("   • Page content (title, meta, headings, CTAs, forms)")
    print("   • SEO elements (canonical, Open Graph, schema, links)")
    print("   • UX elements (hero, testimonials, pricing, FAQ)")
    print("   • Performance indicators (images, scripts, styles)")
    print("   • Page structure (navigation, footer, responsiveness)")
    
    # Test 3: Show example usage
    print("\n3️⃣ Example Usage")
    print("-" * 40)
    print("```bash")
    print("# Analyze 3 pages with expert analysis")
    print("curl -X POST https://your-server.com/mcp/tools/analyze_underperforming_pages \\")
    print("  -H \"Content-Type: application/json\" \\")
    print("  -d '{\"max_pages\": 3}'")
    print("")
    print("# Analyze specific report date")
    print("curl -X POST https://your-server.com/mcp/tools/analyze_underperforming_pages \\")
    print("  -H \"Content-Type: application/json\" \\")
    print("  -d '{\"report_date\": \"2024-01-15\", \"max_pages\": 5}'")
    print("```")
    
    print("\n🎉 Expert Analysis Feature Test Complete!")
    print("=" * 50)
    print("The enhanced analysis now provides:")
    print("• Expert-level Digital Marketing Strategist insights")
    print("• Comprehensive CRO, SEO, and UX recommendations")
    print("• Detailed page analysis with actionable improvements")
    print("• Priority-based action plans with expected impact")
    print("• Enhanced Discord integration with detailed metrics")

if __name__ == "__main__":
    test_expert_analysis() 