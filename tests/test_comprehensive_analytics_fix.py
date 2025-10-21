#!/usr/bin/env python3
"""
Test script to verify the comprehensive analytics fix works correctly.
"""

import os
import logging
from bigas.resources.marketing.endpoints import get_comprehensive_analytics_data

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_comprehensive_analytics():
    """Test the comprehensive analytics function"""
    
    # Check environment variables
    property_id = os.environ.get("GA4_PROPERTY_ID")
    if not property_id:
        logger.error("GA4_PROPERTY_ID environment variable not set")
        return False
    
    logger.info(f"Testing comprehensive analytics with property ID: {property_id}")
    
    try:
        # Test standalone mode
        logger.info("Testing standalone mode...")
        result = get_comprehensive_analytics_data(property_id, mode="standalone")
        
        if "error" in result:
            logger.error(f"Error in standalone mode: {result['error']}")
            return False
        
        logger.info(f"Standalone mode successful: {len(result.get('rows', []))} rows returned")
        
        # Test SaaS mode (should fail without credentials but not crash)
        logger.info("Testing SaaS mode...")
        result_saas = get_comprehensive_analytics_data(property_id, mode="saas")
        
        if "error" in result_saas:
            logger.info(f"SaaS mode error (expected): {result_saas['error']}")
        else:
            logger.info(f"SaaS mode successful: {len(result_saas.get('rows', []))} rows returned")
        
        return True
        
    except Exception as e:
        logger.error(f"Exception during test: {e}")
        return False

if __name__ == "__main__":
    success = test_comprehensive_analytics()
    if success:
        print("✅ Comprehensive analytics test PASSED")
        exit(0)
    else:
        print("❌ Comprehensive analytics test FAILED")
        exit(1)











