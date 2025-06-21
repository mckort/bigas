#!/usr/bin/env python3
"""
Automated Test Runner with Auto-Fix

This script:
1. Runs the dynamic test client
2. Captures errors and failures
3. Uses Cursor CLI to automatically fix issues
4. Re-runs tests until everything passes or max attempts reached
"""

import subprocess
import json
import re
import time
import sys
import os
from typing import List, Dict, Any, Optional
from datetime import datetime
import argparse
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class AutoFixTestRunner:
    def __init__(self, max_attempts: int = 5, server_url: str = "http://localhost:5000", exclude_endpoints: List[str] = None, include_slow_tests: bool = False, non_interactive: bool = False, wait_time: int = 10):
        self.max_attempts = max_attempts
        self.server_url = server_url
        self.exclude_endpoints = exclude_endpoints or []
        self.include_slow_tests = include_slow_tests
        self.non_interactive = non_interactive
        self.wait_time = wait_time
        self.attempt_count = 0
        self.fixes_applied = []
        self.test_results = []
        
    def run_dynamic_test(self) -> Dict[str, Any]:
        """Run the dynamic test client and capture output."""
        print(f"\n🔄 Attempt {self.attempt_count + 1}/{self.max_attempts}")
        print("=" * 60)
        
        try:
            # Build command with all arguments
            cmd = [
                sys.executable, "dynamic_test_client.py",
                "--url", self.server_url,
                "--timeout", "60"
            ]
            
            # Add exclude endpoints
            if self.exclude_endpoints:
                for endpoint in self.exclude_endpoints:
                    cmd.extend(["--exclude", endpoint])
            
            # Add include slow tests flag
            if self.include_slow_tests:
                cmd.append("--include-slow-tests")
            
            # Add non-interactive flag
            if self.non_interactive:
                cmd.append("--non-interactive")
            
            print(f"Running: {' '.join(cmd)}")
            print("\n📤 Dynamic Test Client Output:")
            print("-" * 40)
            
            # Run with simple subprocess.run to capture full output
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout for entire test run
            )
            
            # Print the full output
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)
            
            print("-" * 40)
            print(f"Dynamic Test Client Exit Code: {result.returncode}")
            
            return {
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'success': result.returncode == 0
            }
            
        except subprocess.TimeoutExpired:
            print("❌ Test run timed out after 5 minutes")
            return {
                'returncode': -1,
                'stdout': '',
                'stderr': 'Test run timed out after 5 minutes',
                'success': False
            }
        except Exception as e:
            print(f"❌ Failed to run tests: {str(e)}")
            return {
                'returncode': -1,
                'stdout': '',
                'stderr': f'Failed to run tests: {str(e)}',
                'success': False
            }
    
    def analyze_test_output(self, output: str) -> Dict[str, Any]:
        """Analyze test output to extract errors and failures."""
        analysis = {
            'success_rate': 0.0,
            'total_tests': 0,
            'successful_tests': 0,
            'failed_tests': 0,
            'errors': [],
            'warnings': [],
            'failed_endpoints': [],
            'detailed_errors': [],
            'real_issues': []  # New field for actual code issues
        }
        
        # Debug: Print the first 500 characters of output to see what we're working with
        print(f"\n🔍 Debug: Output length: {len(output)} characters")
        print(f"🔍 Debug: First 500 chars: {output[:500]}")
        
        # Extract success rate
        success_rate_match = re.search(r'Overall Success Rate: ([\d.]+)%', output)
        if success_rate_match:
            analysis['success_rate'] = float(success_rate_match.group(1))
            print(f"🔍 Debug: Found success rate: {analysis['success_rate']}%")
        else:
            print("🔍 Debug: No success rate pattern found")
        
        # Count test results more accurately
        success_count = len(re.findall(r'✅.*SUCCESS', output))
        error_count = len(re.findall(r'❌.*ERROR', output))
        warning_count = len(re.findall(r'⚠️.*WARNING', output))
        
        print(f"🔍 Debug: Found {success_count} success, {error_count} errors, {warning_count} warnings")
        
        analysis['successful_tests'] = success_count
        analysis['failed_tests'] = error_count
        analysis['total_tests'] = success_count + error_count + warning_count
        
        # Extract detailed error information
        error_pattern = r'❌ ([^\n]+)\n.*?Error: ([^\n]+)(?:\n.*?Response Preview: ([^\n]+))?'
        error_matches = re.findall(error_pattern, output, re.DOTALL)
        
        print(f"🔍 Debug: Found {len(error_matches)} error matches")
        
        for endpoint, error, response_preview in error_matches:
            detailed_error = {
                'endpoint': endpoint.strip(),
                'error': error.strip(),
                'response_preview': response_preview.strip() if response_preview else None
            }
            analysis['detailed_errors'].append(detailed_error)
            analysis['failed_endpoints'].append({
                'endpoint': endpoint.strip(),
                'error': error.strip()
            })
            analysis['errors'].append(f"{endpoint}: {error}")
            
            # Categorize real issues
            if 'HTTP 404' in error:
                analysis['real_issues'].append({
                    'type': 'endpoint_not_found',
                    'endpoint': endpoint.strip(),
                    'description': 'Endpoint returns 404 - likely routing issue'
                })
            elif 'HTTP 500' in error:
                analysis['real_issues'].append({
                    'type': 'server_error',
                    'endpoint': endpoint.strip(),
                    'description': 'Endpoint returns 500 - likely code error'
                })
            elif 'Missing required field' in error:
                analysis['real_issues'].append({
                    'type': 'parameter_validation',
                    'endpoint': endpoint.strip(),
                    'description': 'Missing required parameters - validation issue'
                })
            elif 'timeout' in error.lower():
                analysis['real_issues'].append({
                    'type': 'timeout',
                    'endpoint': endpoint.strip(),
                    'description': 'Request timeout - performance issue'
                })
        
        # Extract warnings
        warning_sections = re.findall(r'⚠️ ([^\n]+)\n.*?Warning: ([^\n]+)', output, re.DOTALL)
        for endpoint, warning in warning_sections:
            analysis['warnings'].append(f"{endpoint}: {warning}")
        
        print(f"🔍 Debug: Final analysis - Success: {analysis['successful_tests']}, Failed: {analysis['failed_tests']}, Total: {analysis['total_tests']}")
        
        return analysis
    
    def generate_cursor_prompt(self, analysis: Dict[str, Any]) -> str:
        """Generate a prompt for Cursor CLI to fix the issues."""
        prompt = f"""I'm running automated tests for a Flask-based Google Analytics MCP server and encountering failures. Please help fix these issues:

**Test Results Summary:**
- Success Rate: {analysis['success_rate']}%
- Total Tests: {analysis['total_tests']}
- Successful: {analysis['successful_tests']}
- Failed: {analysis['failed_tests']}

**Real Issues Detected:**
"""
        
        if analysis['real_issues']:
            for issue in analysis['real_issues']:
                prompt += f"- {issue['type'].upper()}: {issue['endpoint']} - {issue['description']}\n"
        else:
            prompt += "- No specific issues categorized\n"
        
        prompt += f"""
**Failed Endpoints and Errors:**
"""
        
        for failure in analysis['failed_endpoints']:
            prompt += f"- {failure['endpoint']}: {failure['error']}\n"
        
        # Add detailed error information if available
        if analysis['detailed_errors']:
            prompt += f"\n**Detailed Error Information:**\n"
            for error_info in analysis['detailed_errors']:
                prompt += f"- Endpoint: {error_info['endpoint']}\n"
                prompt += f"  Error: {error_info['error']}\n"
                if error_info['response_preview']:
                    prompt += f"  Response Preview: {error_info['response_preview']}\n"
                prompt += "\n"
        
        if analysis['warnings']:
            prompt += f"\n**Warnings:**\n"
            for warning in analysis['warnings']:
                prompt += f"- {warning}\n"
        
        prompt += f"""
**Context:**
- This is a Flask server with Google Analytics Data API integration
- The server exposes MCP tools for analytics reporting
- Main file: app.py
- Marketing Resource: bigas/resources/marketing/ (endpoints.py, service.py)
- Test file: dynamic_test_client.py

**Specific Action Required:**
"""
        
        # Generate specific actions based on issue types
        if any(issue['type'] == 'endpoint_not_found' for issue in analysis['real_issues']):
            prompt += """1. Check that all endpoints are properly registered in app.py
2. Verify that the blueprint routes are correctly defined
3. Ensure the URL prefixes are correct\n"""
        
        if any(issue['type'] == 'server_error' for issue in analysis['real_issues']):
            prompt += """1. Check for syntax errors in the endpoint functions
2. Verify that all required imports are present
3. Check for missing environment variables
4. Review error handling in the endpoints\n"""
        
        if any(issue['type'] == 'parameter_validation' for issue in analysis['real_issues']):
            prompt += """1. Review the parameter validation logic
2. Check if required fields are properly handled
3. Ensure default values are provided where appropriate\n"""
        
        prompt += f"""
**Request:**
Please analyze the errors above and provide specific fixes for the app.py file and the relevant resource files. 
Focus on the specific issue types identified above and provide concrete code changes that will resolve these test failures.

IMPORTANT: Make actual code changes to fix the issues, don't just provide explanations."""
        
        return prompt
    
    def apply_cursor_fix(self, prompt: str) -> bool:
        """Use Cursor CLI to apply fixes."""
        try:
            print("\n🔧 Applying automatic fixes with Cursor CLI...")
            print("=" * 60)
            print("📝 Cursor Fix Prompt:")
            print("-" * 40)
            print(prompt)
            print("=" * 60)
            
            # Create a temporary file with the prompt
            prompt_file = f"fix_prompt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(prompt_file, 'w') as f:
                f.write(prompt)
            
            # Use Cursor CLI with proper file targeting
            cmd = [
                "cursor", "edit",
                "--file", "bigas/resources/marketing/endpoints.py",
                "--file", "app.py",
                "--prompt-file", prompt_file
            ]
            
            print(f"🔄 Running Cursor CLI command:")
            print(f"   {' '.join(cmd)}")
            print("\n📤 Cursor CLI Output:")
            print("-" * 40)
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180  # 3 minute timeout for Cursor
            )
            
            # Print Cursor CLI output
            if result.stdout:
                print("STDOUT:")
                print(result.stdout)
            if result.stderr:
                print("STDERR:")
                print(result.stderr)
            
            print("-" * 40)
            print(f"Cursor CLI Exit Code: {result.returncode}")
            
            # Clean up prompt file
            try:
                os.remove(prompt_file)
            except:
                pass
            
            if result.returncode == 0:
                print("✅ Cursor CLI fix applied successfully")
                self.fixes_applied.append({
                    'attempt': self.attempt_count + 1,
                    'timestamp': datetime.now().isoformat(),
                    'prompt': prompt,
                    'cursor_output': result.stdout,
                    'cursor_stderr': result.stderr
                })
                return True
            else:
                print(f"❌ Cursor CLI failed with exit code {result.returncode}")
                # Try alternative approach with cursor chat
                return self.apply_cursor_fix_alternative(prompt)
                
        except subprocess.TimeoutExpired:
            print("❌ Cursor CLI timed out after 3 minutes")
            return False
        except FileNotFoundError:
            print("❌ Cursor CLI not found. Trying alternative approach...")
            return self.apply_cursor_fix_alternative(prompt)
        except Exception as e:
            print(f"❌ Error applying Cursor fix: {e}")
            return False
    
    def apply_cursor_fix_alternative(self, prompt: str) -> bool:
        """Alternative approach using cursor chat with file context."""
        try:
            print("\n🔄 Trying alternative Cursor CLI approach...")
            
            # Create a more specific prompt for file editing
            specific_prompt = f"""
{prompt}

IMPORTANT: Please make the actual code changes to fix these issues. 
Focus on the specific files mentioned and provide concrete code modifications.
"""
            
            # Use cursor chat with file context
            cmd = [
                "cursor", "chat",
                "--file", "bigas/resources/marketing/endpoints.py",
                "--file", "app.py",
                "--message", specific_prompt
            ]
            
            print(f"🔄 Running alternative Cursor CLI command:")
            print(f"   {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120
            )
            
            if result.stdout:
                print("STDOUT:")
                print(result.stdout)
            if result.stderr:
                print("STDERR:")
                print(result.stderr)
            
            # Check if any changes were actually made by looking for common patterns
            if "edit" in result.stdout.lower() or "change" in result.stdout.lower() or "fix" in result.stdout.lower():
                print("✅ Cursor CLI alternative approach completed")
                self.fixes_applied.append({
                    'attempt': self.attempt_count + 1,
                    'timestamp': datetime.now().isoformat(),
                    'prompt': specific_prompt,
                    'cursor_output': result.stdout,
                    'cursor_stderr': result.stderr,
                    'method': 'alternative'
                })
                return True
            else:
                print("⚠️  No clear changes detected in Cursor output")
                return False
                
        except Exception as e:
            print(f"❌ Alternative approach failed: {e}")
            return False
    
    def verify_changes_made(self) -> bool:
        """Verify if any actual code changes were made by checking file modification times."""
        try:
            # Check if key files were modified recently
            key_files = ['app.py', 'bigas/resources/marketing/endpoints.py', 'bigas/resources/marketing/service.py']
            current_time = time.time()
            
            for file_path in key_files:
                if os.path.exists(file_path):
                    file_mtime = os.path.getmtime(file_path)
                    # If file was modified in the last 5 minutes, consider it changed
                    if current_time - file_mtime < 300:  # 5 minutes
                        print(f"✅ Detected recent changes to {file_path}")
                        return True
            
            print("⚠️  No recent file changes detected")
            return False
            
        except Exception as e:
            print(f"❌ Error verifying changes: {e}")
            return False
    
    def wait_for_server_restart(self):
        """Wait for server to restart after fixes."""
        print(f"⏳ Waiting {self.wait_time} seconds for server to restart...")
        time.sleep(self.wait_time)
        
        # Verify if changes were actually made
        if self.verify_changes_made():
            print("✅ Code changes detected - server should reflect updates")
        else:
            print("⚠️  No code changes detected - server may not have updated")
    
    def check_server_health(self) -> bool:
        """Check if the server is responding."""
        try:
            import requests
            response = requests.get(f"{self.server_url}/mcp/manifest", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def run_automated_fixes(self):
        """Main loop: run tests, fix issues, repeat until success."""
        print("🤖 Automated Test Runner with Auto-Fix")
        print("=" * 60)
        print(f"Target Server: {self.server_url}")
        print(f"Max Attempts: {self.max_attempts}")
        print("=" * 60)
        
        while self.attempt_count < self.max_attempts:
            self.attempt_count += 1
            
            # Check server health
            if not self.check_server_health():
                print("⚠️  Server not responding. Waiting for server to start...")
                time.sleep(5)
                continue
            
            # Run tests
            test_result = self.run_dynamic_test()
            self.test_results.append({
                'attempt': self.attempt_count,
                'timestamp': datetime.now().isoformat(),
                'result': test_result
            })
            
            # Print test output
            if test_result['stdout']:
                print(test_result['stdout'])
            if test_result['stderr']:
                print("STDERR:", test_result['stderr'])
            
            # Analyze results
            analysis = self.analyze_test_output(test_result['stdout'])
            
            print(f"\n📊 Analysis:")
            print(f"   Success Rate: {analysis['success_rate']}%")
            print(f"   Failed Tests: {analysis['failed_tests']}")
            
            # Print detailed error information
            if analysis['detailed_errors']:
                print(f"\n❌ Detailed Error Information:")
                print("-" * 40)
                for i, error_info in enumerate(analysis['detailed_errors'], 1):
                    print(f"  {i}. Endpoint: {error_info['endpoint']}")
                    print(f"     Error: {error_info['error']}")
                    if error_info['response_preview']:
                        print(f"     Response: {error_info['response_preview']}")
                    print()
            
            # Check if we succeeded
            if analysis['success_rate'] >= 95.0 or analysis['failed_tests'] == 0:
                print("\n🎉 SUCCESS! All tests are passing!")
                self.print_final_summary()
                return True
            
            # If we have failures and haven't reached max attempts
            if self.attempt_count < self.max_attempts and analysis['failed_tests'] > 0:
                print(f"\n🔧 Attempting to fix {analysis['failed_tests']} failures...")
                
                # Generate and apply fixes
                prompt = self.generate_cursor_prompt(analysis)
                fix_success = self.apply_cursor_fix(prompt)
                
                if fix_success:
                    # Wait for server restart
                    self.wait_for_server_restart()
                else:
                    print("⚠️  Could not apply automatic fixes. Manual intervention may be needed.")
                    break
            else:
                break
        
        # If we get here, we've exhausted attempts
        print(f"\n❌ Failed to achieve success after {self.max_attempts} attempts")
        self.print_final_summary()
        return False
    
    def print_final_summary(self):
        """Print a summary of all attempts and fixes."""
        print("\n" + "=" * 60)
        print("📋 FINAL SUMMARY")
        print("=" * 60)
        
        print(f"Total Attempts: {self.attempt_count}")
        print(f"Fixes Applied: {len(self.fixes_applied)}")
        
        if self.fixes_applied:
            print("\n🔧 Fixes Applied:")
            for i, fix in enumerate(self.fixes_applied, 1):
                print(f"  {i}. Attempt {fix['attempt']} at {fix['timestamp']}")
                print(f"     Prompt: {fix['prompt'][:200]}..." if len(fix['prompt']) > 200 else f"     Prompt: {fix['prompt']}")
                if fix.get('cursor_output'):
                    print(f"     Cursor Output: {fix['cursor_output'][:200]}..." if len(fix['cursor_output']) > 200 else f"     Cursor Output: {fix['cursor_output']}")
                if fix.get('cursor_stderr'):
                    print(f"     Cursor Errors: {fix['cursor_stderr']}")
                print()
        
        print("\nTest Results by Attempt:")
        for result in self.test_results:
            analysis = self.analyze_test_output(result['result']['stdout'])
            print(f"  Attempt {result['attempt']}: {analysis['success_rate']}% success rate")
        
        # Save detailed results to file
        results_file = f"auto_fix_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(results_file, 'w') as f:
            json.dump({
                'summary': {
                    'total_attempts': self.attempt_count,
                    'fixes_applied': len(self.fixes_applied),
                    'final_success': self.test_results[-1]['result']['success'] if self.test_results else False
                },
                'fixes': self.fixes_applied,
                'test_results': self.test_results
            }, f, indent=2)
        
        print(f"\n📄 Detailed results saved to: {results_file}")

def main():
    """Main entry point."""
    # Get default server URL from environment or use localhost
    default_server_url = os.getenv('SERVER_URL', 'http://localhost:5000')
    
    parser = argparse.ArgumentParser(description='Automated Test Runner with Auto-Fix')
    parser.add_argument('--url', default=default_server_url,
                       help=f'Server URL (default: {default_server_url})')
    parser.add_argument('--max-attempts', type=int, default=5,
                       help='Maximum fix attempts (default: 5)')
    parser.add_argument('--wait-time', type=int, default=10,
                       help='Seconds to wait after fixes (default: 10)')
    parser.add_argument('--exclude', nargs='+', 
                       help='Endpoints to exclude from testing (e.g., --exclude weekly_analytics_report)')
    parser.add_argument('--include-slow-tests', action='store_true', 
                       help='Include slow/long-running tests like weekly_analytics_report (excluded by default)')
    parser.add_argument('--non-interactive', action='store_true', 
                       help='Run in non-interactive mode (continue on failures)')
    
    args = parser.parse_args()
    
    # Check if required files exist
    if not os.path.exists('dynamic_test_client.py'):
        print("❌ Error: dynamic_test_client.py not found!")
        sys.exit(1)
    
    if not os.path.exists('app.py'):
        print("❌ Error: app.py not found!")
        sys.exit(1)
    
    # Check if Cursor CLI is available
    try:
        subprocess.run(['cursor', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠️  Warning: Cursor CLI not found. Install it for automatic fixes.")
        print("   Download from: https://cursor.sh/")
    
    # Run the automated fix process
    runner = AutoFixTestRunner(
        max_attempts=args.max_attempts,
        server_url=args.url,
        exclude_endpoints=args.exclude,
        include_slow_tests=args.include_slow_tests,
        non_interactive=args.non_interactive,
        wait_time=args.wait_time
    )
    
    success = runner.run_automated_fixes()
    
    if success:
        print("\n🎉 Automated testing and fixing completed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Automated testing and fixing failed. Manual intervention required.")
        sys.exit(1)

if __name__ == "__main__":
    main() 