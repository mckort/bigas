from typing import Dict, List, Optional, Any
import openai
import json
import logging
from bigas.resources.marketing.utils import generate_basic_analysis

logger = logging.getLogger(__name__)

class OpenAIService:
    """Service for handling OpenAI API interactions and natural language processing."""
    
    def __init__(self, openai_api_key: str):
        """Initialize the OpenAI service with API key."""
        self.openai_client = openai.OpenAI(api_key=openai_api_key)
    
    def parse_query(self, question: str) -> Dict[str, Any]:
        """Use OpenAI to parse the natural language question into structured query parameters."""
        system_prompt = """You are an expert at converting natural language questions about Google Analytics data into structured query parameters.
        Return a JSON object with the following fields:
        - metrics: list of metric names (e.g., ["totalUsers", "sessions", "screenPageViews", "conversions", "eventCount"])
        - dimensions: list of dimension names (e.g., ["date", "country", "deviceCategory", "landingPage", "pagePath", "source", "medium"])
        - date_range: object with start_date and end_date (must be in YYYY-MM-DD format)
        - filters: list of filter objects with field, operator, and value
        - order_by: list of order by objects with field and direction
        
        For date ranges:
        - Use YYYY-MM-DD format for specific dates
        - Use "today" for current date
        - Use "yesterday" for yesterday
        - Use "NdaysAgo" format (e.g., "7daysAgo") for relative dates
        - IMPORTANT: For consistent results, use a fixed date range like "30daysAgo" to "today" or "7daysAgo" to "today"
        
        For traffic source analysis, use these combinations:
        - Metrics: ["sessions", "totalUsers"]
        - Dimensions: ["sessionDefaultChannelGroup", "source", "medium"]
        - Order by: sessions (descending) to see highest traffic sources first
        - IMPORTANT: Use a consistent date range for traffic source analysis
        
        For basic traffic source analysis, use these combinations:
        - Metrics: ["sessions", "totalUsers"]
        - Dimensions: ["sessionDefaultChannelGroup"]
        - Order by: sessions (descending) to see highest traffic sources first
        - Use this simpler combination if the above causes compatibility issues
        
        For conversion analysis, use these GA4 metrics:
        - conversions: Number of conversions
        - eventCount: Number of events
        - totalUsers: Total unique users
        - sessions: Number of sessions
        - screenPageViews: Number of page views
        - userEngagementDuration: Time users spent engaged
        
        For bounce rate analysis, ALWAYS use these combinations:
        - Metrics: ["bounceRate", "sessions", "totalUsers"]
        - Dimensions: ["landingPage", "pageTitle"]
        - Order by: bounceRate (descending) to see highest bounce rates first
        
        For exit rate analysis, use these combinations:
        - Metrics: ["screenPageViews", "sessions", "totalUsers", "userEngagementDuration"]
        - Dimensions: ["pagePath", "pageTitle"]
        - Order by: screenPageViews (descending) to see most visited pages first
        - IMPORTANT: Do NOT add filters for exit rate analysis unless specifically requested
        - IMPORTANT: Note: GA4 doesn't have direct "exits" or "exitRate" metrics. Use page views, sessions, and engagement duration to identify potential exit rate issues.
        
        For content and conversion attribution analysis, use these combinations:
        - Metrics: ["conversions", "sessions", "totalUsers"]
        - Dimensions: ["pagePath", "pageTitle", "sessionDefaultChannelGroup", "source", "medium"]
        - Order by: conversions (descending) to see pages with most conversions
        - IMPORTANT: Use compatible metric/dimension combinations. Avoid mixing incompatible fields.
        
        For basic content performance analysis, use these combinations:
        - Metrics: ["conversions", "sessions", "totalUsers"]
        - Dimensions: ["pagePath", "pageTitle"]
        - Order by: conversions (descending) to see pages with most conversions
        - Use this simpler combination if the above causes compatibility issues
        
        For content analysis, use these GA4 dimensions:
        - landingPage: Landing page path
        - pagePath: Page path
        - pageTitle: Page title
        - contentGroup: Content group
        - source: Traffic source
        - medium: Traffic medium
        - campaignName: Campaign name
        - sessionDefaultChannelGroup: Channel grouping (for attribution analysis)
        
        Note: GA4 doesn't have separate "assisted conversions" or "last-click conversions" metrics like Universal Analytics.
        Use "conversions" metric with "sessionDefaultChannelGroup" dimension to analyze conversion attribution by channel.
        
        IMPORTANT: When the question mentions "bounce rate", you MUST include "bounceRate" in the metrics and "landingPage" in the dimensions.
        
        IMPORTANT: When the question mentions "exit rate" or "exits", you MUST include "screenPageViews", "sessions", "totalUsers", and "userEngagementDuration" in the metrics and "pagePath" in the dimensions. Note: GA4 doesn't have direct exit rate metrics, so we analyze page performance through views, sessions, and engagement.
        
        IMPORTANT: Only add filters if the question specifically requests filtering by certain values (e.g., "only show data for mobile users" or "only show data from organic search"). For general analysis questions, avoid adding filters to ensure you get comprehensive data.
        
        IMPORTANT: GA4 has compatibility restrictions between metrics and dimensions. Use the recommended combinations above to avoid "incompatible" errors. If you encounter compatibility issues, try using fewer dimensions or different metric combinations.
        
        Only include fields that are relevant to the question. Use standard Google Analytics 4 metric and dimension names without the 'ga:' prefix."""
        
        response = self.openai_client.chat.completions.create(
            model="gpt-4",
            max_tokens=2000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question}
            ]
        )
        
        try:
            query_params = json.loads(response.choices[0].message.content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse OpenAI response as JSON: {e}")
            logger.error(f"Raw response: {response.choices[0].message.content}")
            # Do not provide fallback - fail properly when OpenAI fails to parse query
            raise ValueError(f"OpenAI failed to parse query into valid JSON: {e}")
        
        # Ensure we have date dimension for trends
        if "dimensions" not in query_params:
            query_params["dimensions"] = ["date"]
            
        return query_params
    
    def format_response(self, response: Any, question: str) -> str:
        """Format the analytics response into a natural language answer."""
        # Convert GA4 response to JSON-serializable format
        from bigas.resources.marketing.utils import convert_ga4_response_to_dict
        analytics_data = convert_ga4_response_to_dict(response)
        
        # Check if we have any data - fail properly instead of fallback response
        if not analytics_data["rows"]:
            raise ValueError(f"No GA4 data returned for question: '{question}'. Cannot provide analysis without real data.")
        
        system_prompt = """You are an expert at explaining Google Analytics data in a clear and concise way.
        Given the raw analytics data and the original question, provide a natural language response that:
        1. Directly answers the question
        2. Highlights key insights and trends
        3. Provides relevant context and comparisons
        4. Uses simple, non-technical language
        
        Format numbers appropriately (e.g., "1.2M" instead of "1,200,000").
        If the data shows no results or empty rows, explain what this means and suggest alternative approaches."""
        
        # Recursively convert any list values in analytics_data to strings
        def stringify_lists(obj):
            if isinstance(obj, list):
                return ', '.join(str(x) for x in obj)
            elif isinstance(obj, dict):
                return {k: stringify_lists(v) for k, v in obj.items()}
            else:
                return obj

        analytics_data = stringify_lists(analytics_data)

        response_data = {
            "question": question,
            "analytics_data": analytics_data
        }
        
        completion = self.openai_client.chat.completions.create(
            model="gpt-4",
            max_tokens=2000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(response_data)}
            ]
        )
        
        return completion.choices[0].message.content
    
    def format_response_obj(self, data: dict, question: str) -> str:
        """Format the analytics response from a dict into a natural language answer."""
        # Check if we have any data after filtering - fail properly instead of fallback
        if not data.get("rows", []):
            raise ValueError(f"No GA4 data remaining after filtering for question: '{question}'. Cannot provide analysis without real data.")
        
        # If we have data, provide a human-readable analysis
        num_rows = len(data["rows"])
        dimensions = data.get('dimension_headers', [])
        metrics = data.get('metric_headers', [])
        
        # Create a system prompt for analysis
        system_prompt = """You are an expert at explaining Google Analytics data in a clear and concise way.
        Given the raw analytics data and the original question, provide a natural language response that:
        1. Directly answers the question
        2. Highlights key insights and trends
        3. Provides relevant context and comparisons
        4. Uses simple, non-technical language
        5. Identifies patterns and actionable insights
        
        Format numbers appropriately (e.g., "1.2M" instead of "1,200,000").
        Focus on the most important findings and provide actionable recommendations."""
        
        # Prepare the data for analysis
        analysis_data = {
            "question": question,
            "data_summary": {
                "total_records": num_rows,
                "dimensions": dimensions,
                "metrics": metrics,
                "rows": data["rows"]
            }
        }
        
        try:
            completion = self.openai_client.chat.completions.create(
                model="gpt-4",
                max_tokens=1500,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(analysis_data)}
                ]
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Error generating analysis for filtered data: {e}")
            # Do not provide fallback analysis - fail properly when OpenAI fails
            raise ValueError(f"OpenAI failed to generate analysis: {e}. Cannot provide fallback analysis.")
    
    def generate_trend_insights(self, formatted_trends: dict, metrics: list, dimensions: list, date_range: str) -> str:
        """Generate AI-powered insights for trend analysis."""
        try:
            system_prompt = """You are an expert at analyzing Google Analytics trend data. 
            Given the trend analysis data, provide actionable insights that:
            1. Identify key trends and patterns
            2. Highlight significant changes (positive or negative)
            3. Suggest potential causes for the trends
            4. Provide actionable recommendations
            5. Focus on business impact and next steps
            
            Be concise but insightful. Focus on the most important findings."""
            
            analysis_data = {
                "metrics_analyzed": metrics,
                "dimensions_analyzed": dimensions,
                "date_range": date_range,
                "trend_data": formatted_trends
            }
            
            completion = self.openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(analysis_data, indent=2)}
                ],
                max_tokens=800,
                temperature=0.7
            )
            
            return completion.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"Error generating AI insights for trends: {e}")
            # Do not provide fallback message - fail properly when OpenAI fails
            raise ValueError(f"OpenAI failed to generate trend insights: {e}. Cannot provide fallback insights.")
    
    def generate_traffic_sources_analysis(self, data: dict) -> str:
        """Generate analysis for traffic sources data."""
        system_prompt = (
            "You are an expert at explaining Google Analytics traffic source data. "
            "Given the raw analytics data and the original question, provide a natural language summary that: "
            "1. Lists the primary traffic sources and their respective share of sessions\n"
            "2. Highlights any notable trends or imbalances\n"
            "3. Provides actionable recommendations for improving traffic diversity or volume\n"
            "Format numbers as percentages where appropriate."
        )
        ai_data = {
            "question": "What are the primary traffic sources (e.g., organic search, direct, referral, paid search, social, email) contributing to total sessions, and what is their respective share?",
            "analytics_data": data
        }
        
        completion = self.openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(ai_data)}
            ],
            max_tokens=500,
            temperature=0.7
        )
        
        return completion.choices[0].message.content 