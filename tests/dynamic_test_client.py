#!/usr/bin/env python3
"""
Dynamic MCP Test Client

This script acts as an MCP client to:
1. Fetch the MCP manifest from the server
2. Parse all available tools/capabilities
3. Dynamically generate and run tests for each endpoint
4. Report results with detailed information
5. Automatically attempt fixes up to 3 times for each failure
"""

import requests
import json
import time
import sys
import subprocess
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import random
import httpx
import asyncio
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class DynamicMCPTestClient:
    def __init__(self, base_url: str = "https://mcp-marketing-919623369853.europe-north1.run.app", exclude_endpoints: List[str] = None, include_slow_tests: bool = False, interactive: bool = True):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'DynamicMCPTestClient/1.0'
        })
        self.results = []
        self.exclude_endpoints = exclude_endpoints or []
        self.include_slow_tests = include_slow_tests
        self.interactive = interactive
        self.fixes_applied = []
        
        # Default exclusions for slow/long-running tests
        if not self.include_slow_tests:
            self.exclude_endpoints.extend([
                'weekly_analytics_report',
                'mcp/tools/weekly_analytics_report'
            ])
    
    def fetch_manifest(self) -> Dict[str, Any]:
        """Fetch the MCP manifest from the server."""
        try:
            print(f"Fetching MCP manifest from {self.base_url}/mcp/manifest...")
            response = self.session.get(f"{self.base_url}/mcp/manifest", timeout=10)
            response.raise_for_status()
            manifest = response.json()
            print(f"✓ Successfully fetched manifest with {len(manifest.get('tools', []))} tools")
            return manifest
        except requests.exceptions.RequestException as e:
            print(f"✗ Failed to fetch manifest: {e}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"✗ Failed to parse manifest JSON: {e}")
            sys.exit(1)
    
    def should_test_endpoint(self, tool: Dict[str, Any]) -> bool:
        """Determine if an endpoint should be tested based on exclusions."""
        tool_name = tool.get('name', '').lower()
        endpoint = tool.get('path', '').lower()
        
        # Check if this endpoint is in the exclusion list
        for excluded in self.exclude_endpoints:
            if excluded.lower() in tool_name or excluded.lower() in endpoint:
                return False
        
        return True
    
    def generate_test_data(self, param_name: str, param_description: str) -> Any:
        """Generate appropriate test data based on parameter name and description."""
        param_lower = param_name.lower()
        desc_lower = param_description.lower()
        
        # Date-related parameters
        if any(word in param_lower for word in ['date', 'start_date', 'end_date']):
            if 'start' in param_lower:
                # Use a date that's 30 days ago
                return (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            elif 'end' in param_lower:
                # Use today's date
                return datetime.now().strftime("%Y-%m-%d")
            else:
                # For generic date parameters, use today
                return datetime.now().strftime("%Y-%m-%d")
        
        # List parameters
        if 'list' in desc_lower or param_lower.endswith('s'):
            if 'metric' in param_lower:
                return ["activeUsers", "sessions", "screenPageViews"]
            elif 'dimension' in param_lower:
                return ["country", "deviceCategory"]
            elif 'time_frame' in param_lower:
                # Generate valid date ranges with start before end
                end_date = datetime.now()
                start_date = end_date - timedelta(days=30)
                return [
                    {
                        "name": "last_30_days",
                        "start_date": start_date.strftime("%Y-%m-%d"),
                        "end_date": end_date.strftime("%Y-%m-%d")
                    }
                ]
            elif 'date_ranges' in param_lower or 'date_ranges' in desc_lower:
                # Generate valid date ranges for custom reports
                end_date = datetime.now()
                start_date = end_date - timedelta(days=30)
                comparison_end = start_date - timedelta(days=1)
                comparison_start = comparison_end - timedelta(days=30)
                return [
                    {
                        "name": "current_period",
                        "start_date": start_date.strftime("%Y-%m-%d"),
                        "end_date": end_date.strftime("%Y-%m-%d")
                    },
                    {
                        "name": "previous_period", 
                        "start_date": comparison_start.strftime("%Y-%m-%d"),
                        "end_date": comparison_end.strftime("%Y-%m-%d")
                    }
                ]
            else:
                return []
        
        # String parameters
        if 'string' in desc_lower or 'question' in param_lower:
            if 'question' in param_lower:
                return "What are the top traffic sources for our website?"
            else:
                return "test_value"
        
        # Integer parameters
        if 'int' in desc_lower or 'limit' in param_lower:
            return 10
        
        # Boolean parameters
        if 'bool' in desc_lower or param_lower.startswith('is_'):
            return True
        
        # Default fallback
        return "test_value"
    
    def generate_custom_report_test_data(self) -> Dict[str, Any]:
        """Generate specific test data for the fetch_custom_report endpoint."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        
        return {
            "dimensions": ["country", "deviceCategory"],
            "metrics": ["activeUsers", "sessions"],
            "date_ranges": [
                {
                    "start_date": start_date.strftime("%Y-%m-%d"),
                    "end_date": end_date.strftime("%Y-%m-%d"),
                    "name": "current_period"
                }
            ]
        }
    
    def analyze_error_and_generate_fix_prompt(self, result: Dict[str, Any], attempt: int) -> str:
        """Analyze error and generate a fix prompt for Cursor CLI."""
        error = result.get('error', '').lower()
        status_code = result.get('status_code', 0)
        tool_name = result.get('tool_name', 'unknown')
        endpoint = result.get('endpoint', '')
        method = result.get('method', 'GET')
        
        # Build the fix prompt
        prompt_parts = [
            f"Fix the failing endpoint '{tool_name}' in bigas_marketing.py. Here are the details:",
            f"- Endpoint: {method} {endpoint}",
            f"- Error: {result.get('error', 'Unknown error')}",
            f"- Status Code: {status_code}",
            f"- Attempt: {attempt}/3"
        ]
        
        if result.get('response_preview'):
            prompt_parts.append(f"- Response Preview: {result['response_preview']}")
        
        # Add specific analysis based on error type
        if status_code == 404:
            prompt_parts.extend([
                "The endpoint is returning 404 Not Found. This suggests:",
                "- The Flask route is not properly defined",
                "- The endpoint path is incorrect",
                "- The route decorator is missing or wrong"
            ])
        elif status_code == 500:
            prompt_parts.extend([
                "The endpoint is returning 500 Internal Server Error. This suggests:",
                "- There's a Python exception in the endpoint function",
                "- Missing environment variables",
                "- Google Analytics API authentication issues",
                "- Invalid parameter handling"
            ])
        elif status_code == 400:
            prompt_parts.extend([
                "The endpoint is returning 400 Bad Request. This suggests:",
                "- Invalid request parameters",
                "- Missing required parameters",
                "- Wrong parameter types or formats"
            ])
        
        # Add specific error analysis
        if 'timeout' in error:
            prompt_parts.extend([
                "The request is timing out. Consider:",
                "- Increasing timeout values",
                "- Checking Google Analytics API responsiveness",
                "- Optimizing the endpoint performance"
            ])
        
        if 'connection' in error or 'refused' in error:
            prompt_parts.extend([
                "Connection is being refused. Check:",
                "- Server is running and accessible",
                "- Correct URL and port",
                "- Firewall settings"
            ])
        
        if 'json' in error:
            prompt_parts.extend([
                "JSON parsing error. Check:",
                "- Endpoint returns valid JSON",
                "- No Python exceptions in response",
                "- Proper error handling"
            ])
        
        if 'ga4' in error or 'analytics' in error:
            prompt_parts.extend([
                "Google Analytics error. Check:",
                "- GA4_PROPERTY_ID environment variable",
                "- Service account permissions",
                "- Property has data"
            ])
        
        if 'openai' in error or 'api key' in error:
            prompt_parts.extend([
                "OpenAI API error. Check:",
                "- OPENAI_API_KEY environment variable",
                "- API quota and billing",
                "- API key permissions"
            ])
        
        prompt_parts.extend([
            "",
            "Please fix the issue in bigas_marketing.py and ensure the endpoint works correctly.",
            "Focus on the specific error and provide a working solution."
        ])
        
        return "\n".join(prompt_parts)
    
    def apply_fix_with_cursor(self, fix_prompt: str) -> bool:
        """Apply a fix using Cursor CLI."""
        try:
            print(f"🔧 Applying fix with Cursor CLI...")
            
            # Check if cursor CLI is available
            try:
                subprocess.run(['cursor', '--version'], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("❌ Cursor CLI not found. Please install it first.")
                return False
            
            # Apply the fix
            result = subprocess.run([
                'cursor', 'edit', 'bigas_marketing.py',
                '--prompt', fix_prompt
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                print("✅ Fix applied successfully")
                self.fixes_applied.append({
                    'timestamp': datetime.now().isoformat(),
                    'prompt': fix_prompt[:100] + "..." if len(fix_prompt) > 100 else fix_prompt
                })
                return True
            else:
                print(f"❌ Failed to apply fix: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            print("❌ Cursor CLI timed out")
            return False
        except Exception as e:
            print(f"❌ Error applying fix: {e}")
            return False
    
    def restart_server(self) -> bool:
        """Restart the Flask server to apply changes."""
        try:
            print("🔄 Restarting server to apply changes...")
            
            # Kill existing server process (if running)
            try:
                subprocess.run(['pkill', '-f', 'bigas_marketing.py'], capture_output=True)
                time.sleep(2)
            except:
                pass
            
            # Start server in background
            subprocess.Popen([
                'python', 'bigas_marketing.py'
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Wait for server to start
            print("⏳ Waiting for server to start...")
            for i in range(30):  # Wait up to 30 seconds
                if self.check_server_health():
                    print("✅ Server restarted successfully")
                    return True
                time.sleep(1)
            
            print("❌ Server failed to start")
            return False
            
        except Exception as e:
            print(f"❌ Error restarting server: {e}")
            return False
    
    def test_endpoint(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """Test a single endpoint and return results."""
        tool_name = tool.get('name', 'unknown')
        endpoint = tool.get('path', '')
        method = tool.get('method', 'GET')
        parameters = tool.get('parameters', {})
        
        print(f"   Endpoint: {method} {endpoint}")
        print(f"   Parameters: {list(parameters.keys()) if parameters else 'None'}")
        
        # Generate test data
        test_data = {}
        if parameters:
            # Special handling for fetch_custom_report endpoint
            if tool_name == 'fetch_custom_report' or 'fetch_custom_report' in endpoint:
                test_data = self.generate_custom_report_test_data()
                print(f"   Using custom test data for fetch_custom_report")
            else:
                for param_name, param_desc in parameters.items():
                    test_data[param_name] = self.generate_test_data(param_name, param_desc)
        else:
            # Handle endpoints that require specific data even without parameters
            if tool_name == 'fetch_custom_report':
                test_data = self.generate_custom_report_test_data()
                print(f"   Using custom test data for fetch_custom_report")
            elif tool_name == 'ask_analytics_question':
                test_data = {
                    'question': 'What are the top 5 countries by active users in the last 30 days?'
                }
                print(f"   Using test question for ask_analytics_question")
            elif tool_name == 'analyze_underperforming_pages':
                # Add parameters to limit analysis for faster testing
                test_data = {
                    'max_pages': 1  # Only analyze 1 page for testing
                }
                print(f"   Using limited analysis for analyze_underperforming_pages")
            elif tool_name == 'cleanup_old_reports':
                # Add parameters to limit cleanup for faster testing
                test_data = {
                    'max_reports_to_delete': 5  # Only delete 5 reports for testing
                }
                print(f"   Using limited cleanup for cleanup_old_reports")
        
        print(f"   Test Data: {json.dumps(test_data, indent=2)}")
        
        # Prepare request
        url = f"{self.base_url}{endpoint}"
        start_time = time.time()
        
        # Set timeout based on endpoint type
        if tool_name in ['analyze_underperforming_pages', 'cleanup_old_reports']:
            timeout = 60  # Longer timeout for slower endpoints
        else:
            timeout = 30  # Standard timeout for other endpoints
        
        try:
            if method.upper() == "POST":
                response = self.session.post(url, json=test_data, timeout=timeout)
            elif method.upper() == "GET":
                response = self.session.get(url, params=test_data, timeout=timeout)
            else:
                return {
                    'tool_name': tool_name,
                    'endpoint': endpoint,
                    'method': method,
                    'status': 'error',
                    'error': f'Unsupported method: {method}',
                    'duration': 0
                }
            
            duration = time.time() - start_time
            
            # Analyze response
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    return {
                        'tool_name': tool_name,
                        'endpoint': endpoint,
                        'method': method,
                        'status': 'success',
                        'status_code': response.status_code,
                        'duration': round(duration, 3),
                        'response_keys': list(response_data.keys()) if isinstance(response_data, dict) else 'N/A',
                        'response_preview': str(response_data)[:200] + "..." if len(str(response_data)) > 200 else str(response_data)
                    }
                except json.JSONDecodeError:
                    return {
                        'tool_name': tool_name,
                        'endpoint': endpoint,
                        'method': method,
                        'status': 'warning',
                        'status_code': response.status_code,
                        'duration': round(duration, 3),
                        'error': 'Response is not valid JSON',
                        'response_preview': response.text[:200] + "..." if len(response.text) > 200 else response.text
                    }
            else:
                return {
                    'tool_name': tool_name,
                    'endpoint': endpoint,
                    'method': method,
                    'status': 'error',
                    'status_code': response.status_code,
                    'duration': round(duration, 3),
                    'error': f'HTTP {response.status_code}',
                    'response_preview': response.text[:200] + "..." if len(response.text) > 200 else response.text
                }
                
        except requests.exceptions.Timeout:
            return {
                'tool_name': tool_name,
                'endpoint': endpoint,
                'method': method,
                'status': 'error',
                'error': 'Request timeout',
                'duration': timeout
            }
        except requests.exceptions.RequestException as e:
            return {
                'tool_name': tool_name,
                'endpoint': endpoint,
                'method': method,
                'status': 'error',
                'error': str(e),
                'duration': 0
            }
    
    def attempt_fixes_for_endpoint(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """Attempt to fix a failing endpoint up to 3 times."""
        tool_name = tool.get('name', 'unknown')
        max_attempts = 3
        
        print(f"\n🔧 Attempting to fix '{tool_name}' (max {max_attempts} attempts)...")
        
        for attempt in range(1, max_attempts + 1):
            print(f"\n📋 Attempt {attempt}/{max_attempts}:")
            print("=" * 40)
            
            # Test the endpoint
            result = self.test_endpoint(tool)
            
            if result['status'] == 'success':
                print(f"✅ {tool_name}: SUCCESS on attempt {attempt} ({result.get('duration', 0)}s)")
                return result
            
            # Handle failure
            print(f"❌ {tool_name}: FAILED on attempt {attempt}")
            print(f"   Error: {result.get('error', 'Unknown error')}")
            print(f"   Status Code: {result.get('status_code', 'N/A')}")
            print(f"   Duration: {result.get('duration', 0)}s")
            
            if result.get('response_preview'):
                print(f"   Response Preview: {result['response_preview']}")
            
            # If this is the last attempt, return the failure
            if attempt == max_attempts:
                print(f"💀 {tool_name}: FAILED after {max_attempts} attempts - giving up")
                return result
            
            # Generate fix prompt
            fix_prompt = self.analyze_error_and_generate_fix_prompt(result, attempt)
            
            # Apply fix with Cursor CLI
            fix_success = self.apply_fix_with_cursor(fix_prompt)
            
            if fix_success:
                # Restart server to apply changes
                if self.restart_server():
                    print(f"⏳ Waiting 3 seconds for server to stabilize...")
                    time.sleep(3)
                else:
                    print("⚠️  Server restart failed, continuing anyway...")
            else:
                print("⚠️  Fix application failed, continuing anyway...")
            
            print(f"🔄 Retrying in 2 seconds...")
            time.sleep(2)
        
        # This should never be reached, but just in case
        return result
    
    def check_server_health(self) -> bool:
        """Check if the server is responding."""
        try:
            response = requests.get(f"{self.base_url}/mcp/manifest", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def run_all_tests(self) -> None:
        """Fetch manifest and run tests for all tools one by one."""
        print("🚀 Dynamic MCP Test Client with Auto-Fix")
        print("=" * 50)
        
        # Fetch manifest
        manifest = self.fetch_manifest()
        all_tools = manifest.get('tools', [])
        
        # Filter tools based on exclusions
        tools_to_test = [tool for tool in all_tools if self.should_test_endpoint(tool)]
        excluded_tools = [tool for tool in all_tools if not self.should_test_endpoint(tool)]
        
        if not tools_to_test:
            print("⚠️  No tools found to test after applying exclusions")
            return
        
        print(f"\n📋 Found {len(all_tools)} total tools:")
        print(f"   • {len(tools_to_test)} tools to test")
        print(f"   • {len(excluded_tools)} tools excluded")
        
        if excluded_tools:
            print(f"\n🚫 Excluded tools:")
            for tool in excluded_tools:
                print(f"   • {tool.get('name', 'unknown')} ({tool.get('method', 'GET')} {tool.get('path', 'unknown')})")
        
        print(f"\n🧪 Tools to test:")
        for i, tool in enumerate(tools_to_test, 1):
            print(f"   {i}. {tool.get('name', 'unknown')} ({tool.get('method', 'GET')} {tool.get('path', 'unknown')})")
        
        # Run tests one by one
        print(f"\n🧪 Running tests with auto-fix...")
        start_time = time.time()
        
        for i, tool in enumerate(tools_to_test, 1):
            print(f"\n{'='*60}")
            print(f"Test {i}/{len(tools_to_test)}: {tool.get('name', 'unknown')}")
            print(f"{'='*60}")
            
            # Test the endpoint with auto-fix
            result = self.attempt_fixes_for_endpoint(tool)
            self.results.append(result)
        
        total_duration = time.time() - start_time
        self.print_summary(total_duration)
    
    def print_summary(self, total_duration: float) -> None:
        """Print a comprehensive test summary."""
        print("\n" + "=" * 50)
        print("📊 TEST SUMMARY")
        print("=" * 50)
        
        # Count results by status
        status_counts = {}
        for result in self.results:
            status = result['status']
            status_counts[status] = status_counts.get(status, 0) + 1
        
        print(f"Total Tools Tested: {len(self.results)}")
        print(f"Total Duration: {total_duration:.2f}s")
        if self.results:
            print(f"Average per Test: {total_duration/len(self.results):.2f}s")
        print(f"Fixes Applied: {len(self.fixes_applied)}")
        print()
        
        print("Results by Status:")
        for status, count in status_counts.items():
            emoji = {'success': '✅', 'warning': '⚠️', 'error': '❌'}.get(status, '❓')
            print(f"  {emoji} {status.upper()}: {count}")
        
        if self.fixes_applied:
            print(f"\n🔧 Fixes Applied:")
            for i, fix in enumerate(self.fixes_applied, 1):
                print(f"  {i}. {fix['timestamp']}: {fix['prompt']}")
        
        print("\nDetailed Results:")
        print("-" * 50)
        
        for result in self.results:
            status_emoji = {
                'success': '✅',
                'warning': '⚠️',
                'error': '❌'
            }.get(result['status'], '❓')
            
            print(f"{status_emoji} {result['tool_name']}")
            print(f"   Endpoint: {result['method']} {result['endpoint']}")
            print(f"   Status: {result['status'].upper()}")
            
            if result['status'] == 'success':
                print(f"   Duration: {result.get('duration', 0)}s")
                print(f"   Response Keys: {result.get('response_keys', 'N/A')}")
                print(f"   Response Preview: {result.get('response_preview', 'N/A')}")
            elif result['status'] == 'warning':
                print(f"   Duration: {result.get('duration', 0)}s")
                print(f"   Warning: {result.get('error', 'Unknown warning')}")
                print(f"   Response Preview: {result.get('response_preview', 'N/A')}")
            else:  # error
                print(f"   Error: {result.get('error', 'Unknown error')}")
                if result.get('response_preview'):
                    print(f"   Response Preview: {result['response_preview']}")
            
            print()
        
        # Overall success rate
        if self.results:
            success_rate = (status_counts.get('success', 0) / len(self.results)) * 100
            print(f"Overall Success Rate: {success_rate:.1f}%")
            
            if success_rate == 100:
                print("🎉 All tests passed!")
            elif success_rate >= 80:
                print("👍 Most tests passed!")
            else:
                print("⚠️  Many tests failed. Check the server logs for details.")

def main():
    """Main entry point."""
    import argparse
    
    # Get default server URL from environment or use Google Cloud deployment
    default_server_url = os.getenv('SERVER_URL', 'https://mcp-marketing-919623369853.europe-north1.run.app')
    
    parser = argparse.ArgumentParser(description='Dynamic MCP Test Client with Auto-Fix')
    parser.add_argument('--url', default=default_server_url, 
                       help=f'Base URL of the MCP server (default: {default_server_url})')
    parser.add_argument('--timeout', type=int, default=30,
                       help='Request timeout in seconds (default: 30)')
    parser.add_argument('--exclude', nargs='+', default=[],
                       help='Endpoints to exclude from testing (e.g., --exclude weekly_analytics_report)')
    parser.add_argument('--include-slow-tests', action='store_true',
                       help='Include slow/long-running tests like weekly_analytics_report (excluded by default)')
    parser.add_argument('--non-interactive', action='store_true',
                       help='Run in non-interactive mode (continue on failures)')
    
    args = parser.parse_args()
    
    # Create and run the test client
    client = DynamicMCPTestClient(
        base_url=args.url,
        exclude_endpoints=args.exclude,
        include_slow_tests=args.include_slow_tests,
        interactive=not args.non_interactive
    )
    client.session.timeout = args.timeout
    
    try:
        client.run_all_tests()
    except KeyboardInterrupt:
        print("\n\n⚠️  Testing interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 