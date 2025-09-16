#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test runner for Bigas Core Analytics tests.
Runs tests against the live Google Cloud deployment.
"""

import asyncio
import os
import sys
import subprocess
import time
from datetime import datetime

# Configuration
SERVER_URL = os.environ.get("BIGAS_SERVER_URL", "https://mcp-marketing-919623369853.europe-north1.run.app")

def print_header(title):
    """Print a formatted header."""
    print(f"\n{'=' * 60}")
    print(f"{title}")
    print(f"{'=' * 60}")

def print_test_result(test_name, success, duration=None, error=None):
    """Print formatted test result."""
    status = "PASS" if success else "FAIL"
    duration_str = f" ({duration:.2f}s)" if duration else ""
    print(f"[{status}] {test_name}{duration_str}")
    if error and not success:
        print(f"    Error: {error}")

async def run_health_check():
    """Run the health check test."""
    print_header("Running Health Check Test")
    start_time = time.time()
    
    try:
        # Import and run health check
        from tests.health_check import main as health_main
        
        # Capture the result by running the health check
        result = await health_main()
        duration = time.time() - start_time
        print_test_result("Health Check", True, duration)
        return True
        
    except Exception as e:
        duration = time.time() - start_time
        print_test_result("Health Check", False, duration, str(e))
        return False

async def run_analytics_test():
    """Run the analytics client test."""
    print_header("Running Analytics Client Test")
    start_time = time.time()
    
    try:
        from tests.test_client import main as client_main
        
        # Run the analytics test
        await client_main()
        duration = time.time() - start_time
        print_test_result("Analytics Client", True, duration)
        return True
        
    except Exception as e:
        duration = time.time() - start_time
        print_test_result("Analytics Client", False, duration, str(e))
        return False

async def run_storage_test():
    """Run the storage functionality test."""
    print_header("Running Storage Test")
    start_time = time.time()
    
    try:
        from tests.test_storage import main as storage_main
        
        # Run the storage test
        await storage_main()
        duration = time.time() - start_time
        print_test_result("Storage Test", True, duration)
        return True
        
    except Exception as e:
        duration = time.time() - start_time
        print_test_result("Storage Test", False, duration, str(e))
        return False

def run_expert_analysis_test():
    """Run the expert analysis test."""
    print_header("Running Expert Analysis Test")
    start_time = time.time()
    
    try:
        from tests.test_expert_analysis import test_expert_analysis
        
        # Run the expert analysis test
        test_expert_analysis()
        duration = time.time() - start_time
        print_test_result("Expert Analysis", True, duration)
        return True
        
    except Exception as e:
        duration = time.time() - start_time
        print_test_result("Expert Analysis", False, duration, str(e))
        return False

async def run_keyword_analysis_test():
    """Run the keyword analysis test."""
    print_header("Running Keyword Analysis Test")
    start_time = time.time()
    
    try:
        from tests.test_keyword_analysis import test_keyword_analysis
        
        # Run the keyword analysis test
        await test_keyword_analysis()
        duration = time.time() - start_time
        print_test_result("Keyword Analysis", True, duration)
        return True
        
    except Exception as e:
        duration = time.time() - start_time
        print_test_result("Keyword Analysis", False, duration, str(e))
        return False

async def run_all_tests():
    """Run all tests and report results."""
    print_header("Bigas Core Analytics Test Suite")
    print(f"Target Server: {SERVER_URL}")
    print(f"Test Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Track test results
    results = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "tests": []
    }
    
    # List of tests to run (name, async function)
    tests = [
        ("Health Check", run_health_check),
        ("Analytics Client", run_analytics_test),
        ("Storage Test", run_storage_test),
        ("Expert Analysis", run_expert_analysis_test),
        ("Keyword Analysis", run_keyword_analysis_test),
    ]
    
    # Run each test
    start_time = time.time()
    
    for test_name, test_func in tests:
        results["total"] += 1
        
        try:
            if asyncio.iscoroutinefunction(test_func):
                success = await test_func()
            else:
                success = test_func()
                
            if success:
                results["passed"] += 1
            else:
                results["failed"] += 1
                
            results["tests"].append({
                "name": test_name,
                "success": success
            })
            
        except Exception as e:
            results["failed"] += 1
            results["tests"].append({
                "name": test_name,
                "success": False,
                "error": str(e)
            })
            print_test_result(test_name, False, error=str(e))
    
    # Print summary
    total_duration = time.time() - start_time
    print_header("Test Summary")
    print(f"Total Tests: {results['total']}")
    print(f"Passed: {results['passed']}")
    print(f"Failed: {results['failed']}")
    print(f"Success Rate: {(results['passed']/results['total']*100):.1f}%")
    print(f"Total Duration: {total_duration:.2f}s")
    
    if results["failed"] > 0:
        print(f"\nFailed Tests:")
        for test in results["tests"]:
            if not test["success"]:
                error_info = f" - {test.get('error', 'Unknown error')}" if test.get('error') else ""
                print(f"  - {test['name']}{error_info}")
    
    return results["failed"] == 0

def main():
    """Main entry point."""
    # Set up Python path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, current_dir)
    
    try:
        # Run the test suite
        success = asyncio.run(run_all_tests())
        
        if success:
            print(f"\nAll tests passed! Server at {SERVER_URL} is working correctly.")
            sys.exit(0)
        else:
            print(f"\nSome tests failed. Check the server at {SERVER_URL} and logs.")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print(f"\nTest run interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()