#!/usr/bin/env python3
"""
Simple health check script for the Bigas Marketing server.
"""

import httpx
import asyncio
import os
import sys

async def check_server_health():
    """Check if the server is running and accessible."""
    
    # Server URL - configurable via environment variable
    default_url = "https://mcp-marketing-919623369853.europe-north1.run.app"
    server_url = os.environ.get('BIGAS_SERVER_URL', default_url)
    
    # Remove trailing slash and add health check endpoint
    server_url = server_url.rstrip('/')
    health_url = f"{server_url}/mcp/manifest"
    
    print(f"Checking server health at: {health_url}")
    
    timeout = httpx.Timeout(10.0, connect=5.0)
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            print("Sending GET request to manifest endpoint...")
            response = await client.get(health_url)
            print(f"✅ Server is responding! Status: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    manifest = response.json()
                    tools = manifest.get('tools', [])
                    print(f"✅ Found {len(tools)} tools in manifest:")
                    for tool in tools:
                        print(f"   - {tool.get('name', 'Unknown')}: {tool.get('path', 'No path')}")
                    return True
                except Exception as e:
                    print(f"⚠️  Server responded but couldn't parse JSON: {e}")
                    print(f"Response: {response.text[:200]}...")
                    return False
            else:
                print(f"⚠️  Server responded with status {response.status_code}")
                print(f"Response: {response.text[:200]}...")
                return False
                
        except httpx.TimeoutException:
            print("❌ Timeout: Server took too long to respond")
            return False
        except httpx.ConnectError:
            print("❌ Connection Error: Could not connect to server")
            return False
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return False

async def main():
    """Main function."""
    print("🏥 Bigas Marketing Server Health Check")
    print("=" * 50)
    
    is_healthy = await check_server_health()
    
    if is_healthy:
        print("\n✅ Server appears to be healthy!")
        sys.exit(0)
    else:
        print("\n❌ Server health check failed!")
        print("\nTroubleshooting tips:")
        print("1. Check if the server URL is correct")
        print("2. Verify the server is deployed and running")
        print("3. Check network connectivity")
        print("4. Try setting BIGAS_SERVER_URL environment variable")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main()) 