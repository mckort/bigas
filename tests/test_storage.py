#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script for the new storage functionality.

This script tests the storage service and related endpoints to ensure
weekly reports are properly stored and can be retrieved for analysis.
Tests against the live Google Cloud deployment.
"""

import asyncio
import httpx
import json
import os
from datetime import datetime

# Configuration
SERVER_URL = os.environ.get("SERVER_URL", "https://mcp-marketing-919623369853.europe-north1.run.app")
TIMEOUT = 30.0

async def test_storage_functionality():
    """Test the storage functionality end-to-end."""
    print("🧪 Testing Storage Functionality")
    print("=" * 50)
    
    timeout = httpx.Timeout(TIMEOUT)
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        try:
            # Test 1: Get list of stored reports
            print("\n1. Testing get_stored_reports endpoint...")
            response = await client.get(f"{SERVER_URL}/mcp/tools/get_stored_reports")
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Found {data.get('total_reports', 0)} stored reports")
                if data.get('reports'):
                    print("Recent reports:")
                    for report in data['reports'][:3]:  # Show first 3
                        print(f"  - {report['date']} ({report['size']} bytes)")
            else:
                print(f"❌ Error: {response.text}")
            
            # Test 2: Get latest report
            print("\n2. Testing get_latest_report endpoint...")
            response = await client.get(f"{SERVER_URL}/mcp/tools/get_latest_report")
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                summary = data.get('summary', {})
                print(f"✅ Latest report: {summary.get('report_date', 'Unknown')}")
                print(f"   Questions: {summary.get('total_questions', 0)}")
                print(f"   Has underperforming pages: {summary.get('has_underperforming_pages', False)}")
            elif response.status_code == 404:
                print("ℹ️  No reports found yet - this is normal if no weekly reports have been generated")
            else:
                print(f"❌ Error: {response.text}")
            
            # Test 3: Analyze underperforming pages (if reports exist)
            print("\n3. Testing analyze_underperforming_pages endpoint...")
            response = await client.post(f"{SERVER_URL}/mcp/tools/analyze_underperforming_pages")
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Analysis completed for report: {data.get('report_date', 'Unknown')}")
                print(f"   Underperforming pages found: {len(data.get('underperforming_pages', []))}")
                print(f"   Page URLs extracted: {len(data.get('page_urls', []))}")
                print(f"   Discord posted: {data.get('discord_posted', False)}")
                
                # Show page URLs if available
                page_urls = data.get('page_urls', [])
                if page_urls:
                    print("\n   Page URLs found:")
                    for page in page_urls[:3]:  # Show first 3
                        print(f"   • {page.get('page_url', 'N/A')} ({page.get('sessions', 0)} sessions, {page.get('conversions', 0)} conversions)")
                
                if data.get('improvement_suggestions'):
                    print("\n   AI Suggestions preview:")
                    suggestions = data['improvement_suggestions'][:200] + "..." if len(data['improvement_suggestions']) > 200 else data['improvement_suggestions']
                    print(f"   {suggestions}")
            elif response.status_code == 404:
                print("ℹ️  No reports found for analysis")
            else:
                print(f"❌ Error: {response.text}")
            
            # Test 4: Cleanup old reports (dry run - keep all)
            print("\n4. Testing cleanup_old_reports endpoint...")
            response = await client.post(
                f"{SERVER_URL}/mcp/tools/cleanup_old_reports",
                json={"keep_days": 365}  # Keep all reports for testing
            )
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Cleanup completed: {data.get('deleted_reports', 0)} reports deleted")
                print(f"   Keeping reports from last {data.get('keep_days', 0)} days")
            else:
                print(f"❌ Error: {response.text}")
                
        except Exception as e:
            print(f"❌ Test failed with error: {e}")
            import traceback
            traceback.print_exc()

async def test_weekly_report_storage():
    """Test that weekly reports are properly stored."""
    print("\n🧪 Testing Weekly Report Storage")
    print("=" * 50)
    
    timeout = httpx.Timeout(TIMEOUT)
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        try:
            # Trigger a weekly report (this will also test storage)
            print("\n1. Triggering weekly analytics report...")
            response = await client.post(f"{SERVER_URL}/mcp/tools/weekly_analytics_report")
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Weekly report completed")
                print(f"   Stored: {data.get('stored', False)}")
                print(f"   Storage path: {data.get('storage_path', 'N/A')}")
                
                # Wait a moment for storage to complete
                await asyncio.sleep(2)
                
                # Verify the report was stored
                print("\n2. Verifying report was stored...")
                response = await client.get(f"{SERVER_URL}/mcp/tools/get_latest_report")
                if response.status_code == 200:
                    data = response.json()
                    summary = data.get('summary', {})
                    print(f"✅ Report verified in storage")
                    print(f"   Date: {summary.get('report_date', 'Unknown')}")
                    print(f"   Questions: {summary.get('total_questions', 0)}")
                else:
                    print(f"❌ Could not verify stored report: {response.status_code}")
            else:
                print(f"❌ Weekly report failed: {response.text}")
                
        except Exception as e:
            print(f"❌ Test failed with error: {e}")
            import traceback
            traceback.print_exc()

async def main():
    """Main test function."""
    print("🚀 Bigas Storage Functionality Test")
    print("=" * 50)
    print(f"Server URL: {SERVER_URL}")
    print(f"Timeout: {TIMEOUT}s")
    
    # Test basic storage functionality
    await test_storage_functionality()
    
    # Test weekly report storage (optional - uncomment to test)
    # await test_weekly_report_storage()
    
    print("\n✅ Storage functionality test completed!")

if __name__ == "__main__":
    asyncio.run(main()) 