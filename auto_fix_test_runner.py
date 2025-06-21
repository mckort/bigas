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
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout for entire test run
            )
            
            return {
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'success': result.returncode == 0
            }
            
        except subprocess.TimeoutExpired:
            return {
                'returncode': -1,
                'stdout': '',
                'stderr': 'Test run timed out after 5 minutes',
                'success': False
            }
        except Exception as e:
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
            'failed_endpoints': []
        }
        
        # Extract success rate
        success_rate_match = re.search(r'Overall Success Rate: ([\d.]+)%', output)
        if success_rate_match:
            analysis['success_rate'] = float(success_rate_match.group(1))
        
        # Count test results
        success_count = len(re.findall(r'✅.*SUCCESS', output))
        error_count = len(re.findall(r'❌.*ERROR', output))
        warning_count = len(re.findall(r'⚠️.*WARNING', output))
        
        analysis['successful_tests'] = success_count
        analysis['failed_tests'] = error_count
        analysis['total_tests'] = success_count + error_count + warning_count
        
        # Extract specific errors
        error_sections = re.findall(r'❌ ([^\n]+)\n.*?Error: ([^\n]+)', output, re.DOTALL)
        for endpoint, error in error_sections:
            analysis['failed_endpoints'].append({
                'endpoint': endpoint.strip(),
                'error': error.strip()
            })
            analysis['errors'].append(f"{endpoint}: {error}")
        
        # Extract warnings
        warning_sections = re.findall(r'⚠️ ([^\n]+)\n.*?Warning: ([^\n]+)', output, re.DOTALL)
        for endpoint, warning in warning_sections:
            analysis['warnings'].append(f"{endpoint}: {warning}")
        
        return analysis
    
    def generate_cursor_prompt(self, analysis: Dict[str, Any]) -> str:
        """Generate a prompt for Cursor CLI to fix the issues."""
        prompt = f"""I'm running automated tests for a Flask-based Google Analytics MCP server and encountering failures. Please help fix these issues:

**Test Results Summary:**
- Success Rate: {analysis['success_rate']}%
- Total Tests: {analysis['total_tests']}
- Successful: {analysis['successful_tests']}
- Failed: {analysis['failed_tests']}

**Failed Endpoints and Errors:**
"""
        
        for failure in analysis['failed_endpoints']:
            prompt += f"- {failure['endpoint']}: {failure['error']}\n"
        
        if analysis['warnings']:
            prompt += f"\n**Warnings:**\n"
            for warning in analysis['warnings']:
                prompt += f"- {warning}\n"
        
        prompt += f"""
**Context:**
- This is a Flask server with Google Analytics Data API integration
- The server exposes MCP tools for analytics reporting
- Main file: bigas_marketing.py
- Test file: dynamic_test_client.py

**Request:**
Please analyze the errors above and provide specific fixes for the bigas_marketing.py file. Focus on:
1. API endpoint issues (404, 500 errors)
2. Parameter validation problems
3. Google Analytics API integration issues
4. Response format problems
5. Authentication or configuration issues

Provide concrete code changes that will resolve these test failures."""
        
        return prompt
    
    def apply_cursor_fix(self, prompt: str) -> bool:
        """Use Cursor CLI to apply fixes."""
        try:
            print("\n🔧 Applying automatic fixes with Cursor CLI...")
            
            # Create a temporary file with the prompt
            prompt_file = f"fix_prompt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(prompt_file, 'w') as f:
                f.write(prompt)
            
            # Use Cursor CLI to apply fixes
            cmd = [
                "cursor", "chat",
                "--file", "bigas_marketing.py",
                "--prompt-file", prompt_file
            ]
            
            print(f"Running: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout for Cursor
            )
            
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
                    'prompt': prompt[:200] + "..." if len(prompt) > 200 else prompt
                })
                return True
            else:
                print(f"❌ Cursor CLI failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            print("❌ Cursor CLI timed out")
            return False
        except FileNotFoundError:
            print("❌ Cursor CLI not found. Please install Cursor CLI first.")
            return False
        except Exception as e:
            print(f"❌ Error applying Cursor fix: {e}")
            return False
    
    def wait_for_server_restart(self):
        """Wait for server to restart after fixes."""
        print(f"⏳ Waiting {self.wait_time} seconds for server to restart...")
        time.sleep(self.wait_time)
    
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
            print("\nFixes Applied:")
            for i, fix in enumerate(self.fixes_applied, 1):
                print(f"  {i}. Attempt {fix['attempt']} at {fix['timestamp']}")
                print(f"     Prompt: {fix['prompt']}")
        
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
    parser = argparse.ArgumentParser(description='Automated Test Runner with Auto-Fix')
    parser.add_argument('--url', default='http://localhost:5000',
                       help='Server URL (default: http://localhost:5000)')
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
    
    if not os.path.exists('bigas_marketing.py'):
        print("❌ Error: bigas_marketing.py not found!")
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