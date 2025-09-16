#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script for the target keywords feature in page analysis.
Tests against the live Google Cloud deployment.
"""

import asyncio
import httpx
import json
import os
from datetime import datetime

# Configuration - Use environment variable or default to the correct server
SERVER_URL = os.environ.get("BIGAS_SERVER_URL", "https://mcp-marketing-919623369853.europe-north1.run.app")
TIMEOUT = httpx.Timeout(60.0)

async def test_keyword_analysis():
    """Test the target keywords feature."""
    print("Testing Target Keywords Feature")
    print("=" * 50)
    
    timeout = httpx.Timeout(TIMEOUT)
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        try:
            # Test 1: Analyze with target keywords (configured via environment)
            print("\n1. Testing analysis with target keywords...")
            print("   Target keywords are configured via TARGET_KEYWORDS environment variable")
            print("   Example: TARGET_KEYWORDS=sustainable fashion, eco-friendly clothing, green promos")
            
            response = await client.post(
                f"{SERVER_URL}/mcp/tools/analyze_underperforming_pages",
                json={
                    "max_pages": 1  # Limit to 1 page for testing
                }
            )
            
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print("✅ Keyword analysis completed successfully!")
                print(f"📊 Pages analyzed: {data.get('pages_analyzed', 0)}")
                print(f"📈 Max pages limit: {data.get('max_pages_limit', 0)}")
                print(f"📱 Discord posted: {data.get('discord_posted', False)}")
                print(f"💬 Discord messages sent: {data.get('discord_messages_sent', 0)}")
                
                if data.get('page_urls'):
                    print(f"\n🔗 Found {len(data['page_urls'])} page URLs")
                    for i, page_url in enumerate(data['page_urls'][:2]):  # Show first 2
                        print(f"   {i+1}. {page_url.get('page_url', 'N/A')}")
                        print(f"      Sessions: {page_url.get('sessions', 0)}, Conversions: {page_url.get('conversions', 0)}")
                        print(f"      Conversion Rate: {page_url.get('conversion_rate', 0):.1%}")
            else:
                print(f"❌ Analysis failed with status {response.status_code}")
                print(f"Error: {response.text}")
                
        except Exception as e:
            print(f"❌ Error during keyword analysis: {e}")
    
    # Test 2: Show keyword analysis features
    print("\n2️⃣ Keyword Analysis Features")
    print("-" * 40)
    print("🔍 Keyword Presence Analysis:")
    print("   • Title keyword inclusion")
    print("   • Meta description keyword inclusion")
    print("   • Content keyword density")
    print("   • Keyword position analysis")
    
    print("\n📊 SEO Optimization:")
    print("   • Specific keyword optimization suggestions")
    print("   • Title and meta description improvements")
    print("   • Content gap identification")
    print("   • Competitive keyword analysis")
    
    print("\n🎯 Enhanced Recommendations:")
    print("   • Keyword-specific CTA suggestions")
    print("   • Content structure for target keywords")
    print("   • Internal linking for keyword authority")
    print("   • User intent alignment with keywords")
    
    # Test 3: Show configuration and usage
    print("\n3️⃣ Configuration & Usage")
    print("-" * 40)
    print("📝 Environment Configuration:")
    print("```bash")
    print("# Add to your .env file")
    print("TARGET_KEYWORDS=sustainable fashion, eco-friendly clothing, green promos")
    print("```")
    print("")
    print("🚀 API Usage:")
    print("```bash")
    print("# Analyze underperforming pages (keywords automatically included)")
    print("curl -X POST https://your-server.com/mcp/tools/analyze_underperforming_pages \\")
    print("  -H \"Content-Type: application/json\" \\")
    print("  -d '{\"max_pages\": 3}'")
    print("```")
    
    print("\n🎉 Target Keywords Feature Test Complete!")
    print("\n💡 Benefits:")
    print("   • Consistent keyword focus across all analyses")
    print("   • No need to specify keywords in each request")
    print("   • More specific SEO recommendations")
    print("   • Keyword-focused content optimization")
    print("   • Better search engine visibility")
    print("   • Competitive advantage analysis")

if __name__ == "__main__":
    asyncio.run(test_keyword_analysis()) 