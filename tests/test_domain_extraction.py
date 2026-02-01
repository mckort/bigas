#!/usr/bin/env python3
"""
Test script for domain extraction from GA4 data.

This script tests that the system correctly extracts hostnames from GA4 data
and constructs proper URLs without hardcoded placeholders.
"""

import json
from bigas.resources.marketing.storage_service import StorageService

def test_domain_extraction():
    """Test domain extraction from mock GA4 data."""
    print("Testing Domain Extraction")
    print("=" * 50)
    
    # Mock GA4 data with hostName dimension
    mock_ga4_data = {
        "dimension_headers": ["pagePath", "hostName"],
        "metric_headers": ["sessions", "keyEvents"],
        "rows": [
            {
                "dimension_values": ["/", "bigas.com"],
                "metric_values": ["39", "0"],
                "underperforming": True
            },
            {
                "dimension_values": ["/about-us", "bigas.com"],
                "metric_values": ["10", "0"],
                "underperforming": True
            },
            {
                "dimension_values": ["/blog/sustainable-promo", "bigas.com"],
                "metric_values": ["5", "1"],
                "underperforming": False
            },
            {
                "dimension_values": ["https://external-site.com/page", "external-site.com"],
                "metric_values": ["3", "0"],
                "underperforming": True
            }
        ]
    }
    
    # Create storage service instance
    storage_service = StorageService()
    
    # Test URL extraction
    page_urls = storage_service._extract_page_urls_from_raw_data(mock_ga4_data)
    
    print(f"✅ Extracted {len(page_urls)} page URLs")
    print("\nExtracted URLs:")
    print("-" * 50)
    
    for i, page in enumerate(page_urls, 1):
        print(f"{i}. Page Path: {page['page_path']}")
        print(f"   Hostname: {page['hostname']}")
        print(f"   Full URL: {page['page_url']}")
        print(f"   Sessions: {page['sessions']}, Conversions: {page['conversions']}")
        print(f"   Underperforming: {page['is_underperforming']}")
        print()
    
    # Verify the URLs are correctly constructed
    expected_urls = [
        "https://bigas.com/",
        "https://bigas.com/about-us", 
        "https://bigas.com/blog/sustainable-promo",
        "https://external-site.com/page"
    ]
    
    print("🔍 URL Validation:")
    print("-" * 50)
    
    for i, (page, expected) in enumerate(zip(page_urls, expected_urls)):
        if page['page_url'] == expected:
            print(f"✅ URL {i+1}: {page['page_url']}")
        else:
            print(f"❌ URL {i+1}: Expected {expected}, got {page['page_url']}")
    
    # Test edge cases
    print("\n🧪 Testing Edge Cases:")
    print("-" * 50)
    
    # Test with missing hostname
    edge_case_data = {
        "dimension_headers": ["pagePath", "hostName"],
        "metric_headers": ["sessions", "keyEvents"],
        "rows": [
            {
                "dimension_values": ["/test-page", ""],  # Empty hostname
                "metric_values": ["5", "0"],
                "underperforming": True
            }
        ]
    }
    
    edge_urls = storage_service._extract_page_urls_from_raw_data(edge_case_data)
    print(f"Empty hostname case: {edge_urls[0]['page_url'] if edge_urls else 'No URLs extracted'}")
    
    # Test with different dimension order
    different_order_data = {
        "dimension_headers": ["hostName", "pagePath"],  # Swapped order
        "metric_headers": ["sessions", "keyEvents"],
        "rows": [
            {
                "dimension_values": ["bigas.com", "/test-page"],
                "metric_values": ["5", "0"],
                "underperforming": True
            }
        ]
    }
    
    different_order_urls = storage_service._extract_page_urls_from_raw_data(different_order_data)
    print(f"Different dimension order: {different_order_urls[0]['page_url'] if different_order_urls else 'No URLs extracted'}")
    
    print("\n✅ Domain extraction test completed!")

if __name__ == "__main__":
    test_domain_extraction() 