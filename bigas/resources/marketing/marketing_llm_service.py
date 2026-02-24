from typing import Dict, List, Optional, Any
import json
import logging
from bigas.resources.marketing.utils import generate_basic_analysis
from bigas.llm.factory import get_llm_client

logger = logging.getLogger(__name__)

class MarketingLLMService:
    """Service for marketing LLM API interactions and natural language processing.

    Uses the shared bigas.llm abstraction; supports OpenAI and Gemini via
    model name and BIGAS_MARKETING_LLM_MODEL / LLM_MODEL.
    """

    def __init__(self, openai_api_key: str):
        """Initialize the LLM service.

        Note: GA4Service should be initialized BEFORE this service, which removes
        GOOGLE_APPLICATION_CREDENTIALS_GA4 to prevent httpx interference.

        openai_api_key is still required for backward compatibility (callers pass it).
        The actual provider/model is resolved via get_llm_client(feature='marketing').
        """
        if not openai_api_key:
            raise ValueError("API key is required for marketing LLM")
        
        self._llm, self._model = get_llm_client(
            feature="marketing",
            explicit_model=None,
        )
        logger.info("LLM client initialized for marketing (model=%s)", self._model)
    
    def parse_query(self, question: str) -> Dict[str, Any]:
        """Use OpenAI to parse the natural language question into structured query parameters."""
        system_prompt = """You are an expert at converting natural language questions about Google Analytics data into structured query parameters.
        Return a JSON object with the following fields:
        - metrics: list of metric names (e.g., ["totalUsers", "sessions", "screenPageViews", "keyEvents", "eventCount"])
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
        
        For conversion/key-event analysis, use these GA4 metrics:
        - keyEvents: Number of key events (GA4's current metric; replaces deprecated "conversions")
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
        - Metrics: ["keyEvents", "sessions", "totalUsers"]
        - Dimensions: ["pagePath", "pageTitle", "sessionDefaultChannelGroup", "source", "medium"]
        - Order by: keyEvents (descending) to see pages with most key events
        - IMPORTANT: Use compatible metric/dimension combinations. Avoid mixing incompatible fields.
        
        For basic content performance analysis, use these combinations:
        - Metrics: ["keyEvents", "sessions", "totalUsers"]
        - Dimensions: ["pagePath", "pageTitle"]
        - Order by: keyEvents (descending) to see pages with most key events
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
        Use "keyEvents" metric with "sessionDefaultChannelGroup" dimension to analyze key-event attribution by channel.
        
        IMPORTANT: When the question mentions "bounce rate", you MUST include "bounceRate" in the metrics and "landingPage" in the dimensions.
        
        IMPORTANT: When the question mentions "exit rate" or "exits", you MUST include "screenPageViews", "sessions", "totalUsers", and "userEngagementDuration" in the metrics and "pagePath" in the dimensions. Note: GA4 doesn't have direct exit rate metrics, so we analyze page performance through views, sessions, and engagement.
        
        IMPORTANT: Only add filters if the question specifically requests filtering by certain values (e.g., "only show data for mobile users" or "only show data from organic search"). For general analysis questions, avoid adding filters to ensure you get comprehensive data.
        
        IMPORTANT: GA4 has compatibility restrictions between metrics and dimensions. Use the recommended combinations above to avoid "incompatible" errors. If you encounter compatibility issues, try using fewer dimensions or different metric combinations.
        
        Only include fields that are relevant to the question. Use standard Google Analytics 4 metric and dimension names without the 'ga:' prefix."""
        
        response = self._llm.complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question}
            ],
            max_tokens=2000,
        )
        
        try:
            query_params = json.loads(response)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.error(f"Raw response: {response}")
            raise ValueError(f"LLM failed to parse query into valid JSON: {e}")
        
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
        
        print(f"🔧 DEBUG: Calling LLM API for question: {question[:50]}...")
        try:
            content = self._llm.complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(response_data)}
                ],
                max_tokens=2000,
            )
            print(f"✅ DEBUG: LLM API call successful")
            text = (content or "").strip()
            if not text:
                raise ValueError("LLM returned no content (empty response). Check safety settings or increase max_output_tokens.")
            return text
        except Exception as e:
            print(f"❌ DEBUG: LLM API call failed: {e}")
            raise
    
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
        
        # Prepare the data for analysis.
        # IMPORTANT: keep payload small enough to stay within model context.
        rows = data.get("rows", [])
        max_rows_for_llm = 50
        rows_truncated = False
        if len(rows) > max_rows_for_llm:
            rows = rows[:max_rows_for_llm]
            rows_truncated = True

        analysis_data = {
            "question": question,
            "data_summary": {
                "total_records": num_rows,
                "dimensions": dimensions,
                "metrics": metrics,
                "rows_included": len(rows),
                "rows_truncated": rows_truncated,
                "rows": rows,
            }
        }
        
        try:
            content = self._llm.complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(analysis_data)}
                ],
                max_tokens=2048,
            )
            text = (content or "").strip()
            if not text:
                raise ValueError("LLM returned no content (empty response). Check safety settings or increase max_output_tokens. Cannot provide fallback analysis.")
            return text
        except Exception as e:
            logger.error(f"Error generating analysis for filtered data: {e}")
            raise ValueError(f"LLM failed to generate analysis: {e}. Cannot provide fallback analysis.")
    
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
            
            content = self._llm.complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(analysis_data, indent=2)}
                ],
                max_tokens=2048,
                temperature=0.7
            )
            text = (content or "").strip()
            if not text:
                raise ValueError("LLM returned no content (empty response). Check safety settings or increase max_output_tokens. Cannot provide fallback insights.")
            return text
            
        except Exception as e:
            logger.error(f"Error generating AI insights for trends: {e}")
            raise ValueError(f"LLM failed to generate trend insights: {e}. Cannot provide fallback insights.")
    
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
        
        content = self._llm.complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(ai_data)}
            ],
            max_tokens=2048,
            temperature=0.7
        )
        text = (content or "").strip()
        if not text:
            raise ValueError("LLM returned no content (empty response). Check safety settings or increase max_output_tokens.")
        return text
