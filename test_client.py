import aiohttp
import asyncio
import json
import traceback
from typing import Dict, Any

async def test_analytics_tool():
    """
    Test the fetch_analytics_report MCP tool endpoint of the deployed service.

    This function sends a POST request to the /mcp/tools/fetch_analytics_report endpoint,
    parses the JSON response, and prints the analytics report results in a formatted way.
    It displays the top 5 countries by active users if the request is successful, or prints
    the error message otherwise.
    """
    # Server URL
    url = "https://mcp-marketing-919623369853.europe-north1.run.app/mcp/tools/fetch_analytics_report"
    
    # Send a valid JSON body
    payload = {
        "metrics": ["activeUsers"],
        "dimensions": ["country"]
    }
    headers = {"Content-Type": "application/json"}
    
    async with aiohttp.ClientSession() as session:
        try:
            # Call the fetch_analytics_report endpoint
            async with session.post(url, json=payload, headers=headers) as response:
                try:
                    result = await response.json()
                except Exception:
                    text = await response.text()
                    print("Error: Could not parse JSON. Raw response:")
                    print(text)
                    return
                
                # Print the results in a formatted way
                print("\nAnalytics Report Results:")
                print("=" * 50)
                
                if result.get("status") == "success":
                    data = result.get("data", [])
                    print("Raw data returned from server:", data)
                    print(f"Found {len(data)} countries in the report")
                    print("\nData item types and values:")
                    for i, item in enumerate(data):
                        print(f"  [{i}] type: {type(item)}, value: {item}")
                    print("\nTop 5 countries by active users:")
                    print("-" * 50)
                    
                    # Defensive sorting and type checking
                    filtered_data = [x for x in data if isinstance(x, dict) and 'activeUsers' in x and 'country' in x]
                    sorted_data = sorted(
                        filtered_data,
                        key=lambda x: int(x.get("activeUsers", 0)),
                        reverse=True
                    )
                    for i, country in enumerate(sorted_data[:5]):
                        try:
                            print(f"Country: {country['country']}")
                            print(f"Active Users: {country['activeUsers']}")
                            print("-" * 50)
                        except Exception as e:
                            print(f"Error printing country at index {i}: {e}")
                            print(traceback.format_exc())
                    # Print any unexpected data
                    unexpected = [x for x in data if not (isinstance(x, dict) and 'activeUsers' in x and 'country' in x)]
                    if unexpected:
                        print("\nUnexpected data format in response:")
                        for item in unexpected:
                            print(item)
                else:
                    print("Error:", result.get("message"))
                    if "details" in result:
                        print("Details:", result["details"])
                        
        except Exception as e:
            print(f"Error calling endpoint: {str(e)}")
            print(traceback.format_exc())

async def main():
    """
    Main entry point for testing the analytics server.

    This function tests the fetch_analytics_report MCP tool endpoint.
    """
    print("Testing Analytics Server")
    print("=" * 50)
    
    # Test the analytics tool
    await test_analytics_tool()

if __name__ == "__main__":
    asyncio.run(main()) 