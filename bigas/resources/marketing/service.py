from typing import Dict, List, Optional, Any
import openai
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest,
    DateRange,
    Metric,
    Dimension,
    Filter,
    FilterExpression,
    OrderBy,
)
import json
import os
from datetime import datetime, timedelta
import logging
from bigas.resources.marketing.utils import (
    convert_ga4_response_to_dict,
    generate_basic_analysis,
    calculate_session_share,
    find_high_traffic_low_conversion,
    get_default_date_range,
    get_consistent_date_range,
    process_ga_response
)

# Add after imports, before the class
QUESTION_TEMPLATES = {
    # 1. What are the primary traffic sources (e.g., organic search, direct, referral, paid search, social, email) contributing to total sessions, and what is their respective share?
    "traffic_sources": {
        "dimensions": ["sessionDefaultChannelGroup"],
        "metrics": ["sessions"],
        "postprocess": "calculate_session_share"
    },
    # 2. What is the average session duration and pages per session across all users?
    "session_quality": {
        "dimensions": [],
        "metrics": ["averageSessionDuration", "screenPageViewsPerSession"]
    },
    # 3. Which pages are the most visited, and how do they contribute to conversions (e.g., product pages, category pages, blog posts)?
    "top_pages_conversions": {
        "dimensions": ["pagePath"],
        "metrics": ["sessions", "conversions"],
        "order_by": ["sessions"]
    },
    # 4. Which pages or sections (e.g., blog, product pages, landing pages) drive the most engagement (e.g., time on page, low bounce rate)?
    "engagement_pages": {
        "dimensions": ["pagePath"],
        "metrics": ["averageSessionDuration", "bounceRate"],
        "order_by": ["averageSessionDuration"]
    },
    # 5. Are there underperforming pages with high traffic but low conversions?
    "underperforming_pages": {
        "dimensions": ["pagePath"],
        "metrics": ["sessions", "conversions"],
        "postprocess": "find_high_traffic_low_conversion"
    },
    # 6. How do blog posts or content pages contribute to conversions (e.g., assisted conversions, last-click conversions)?
    "blog_conversion": {
        "dimensions": ["pagePath", "sessionDefaultChannelGroup"],
        "metrics": ["conversions", "sessions"],
        "filters": [{"field": "pagePath", "operator": "contains", "value": "blog"}]
    }
}

class MarketingAnalyticsService:
    def __init__(self, openai_api_key: str):
        """Initialize the Marketing Analytics Service with OpenAI API key."""
        self.openai_client = openai.OpenAI(api_key=openai_api_key)
        self.analytics_client = BetaAnalyticsDataClient()
        
    def _get_default_date_range(self) -> Dict[str, str]:
        """Get default date range for the last 30 days."""
        return get_default_date_range()
        
    def _get_consistent_date_range(self) -> Dict[str, str]:
        """Get a consistent date range for reproducible results."""
        return get_consistent_date_range()
        
    def _parse_query(self, question: str) -> Dict[str, Any]:
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
            ],
            response_format={"type": "json_object"}
        )
        
        try:
            query_params = json.loads(response.choices[0].message.content)
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse OpenAI response as JSON: {e}")
            logging.error(f"Raw response: {response.choices[0].message.content}")
            # Fallback to a basic query
            query_params = {
                "metrics": ["totalUsers", "sessions"],
                "dimensions": ["date"],
                "date_range": self._get_default_date_range()
            }
        
        # Ensure we have date dimension for trends
        if "dimensions" not in query_params:
            query_params["dimensions"] = ["date"]
            
        return query_params
    
    def _build_report_request(self, property_id: str, query_params: Dict[str, Any]) -> RunReportRequest:
        """Build a Google Analytics report request from the parsed query parameters."""
        # Map deprecated or invalid metrics to valid GA4 metrics
        metric_map = {
            "pageViews": "screenPageViews",
            "pageviews": "screenPageViews",
            "exitRate": "bounceRate",
            "averageUserEngagementDuration": "userEngagementDuration"
        }
        metrics = [metric_map.get(m, m) for m in query_params.get("metrics", ["totalUsers"])]
        
        # Map deprecated or invalid dimensions to valid GA4 dimensions
        dimension_map = {
            "pageCategory": "itemCategory",
            "landingPagePath": "landingPage",
            "ga:landingPagePath": "landingPage",
            "pagePath": "pagePath",
            "ga:pagePath": "pagePath",
            "pageTitle": "pageTitle",
            "ga:pageTitle": "pageTitle",
            "contentGroup": "contentGroup",
            "ga:contentGroup": "contentGroup",
            "hostname": "hostName",
            "ga:hostname": "hostName",
            "source": "source",
            "ga:source": "source",
            "medium": "medium",
            "ga:medium": "medium",
            "campaign": "campaignName",
            "ga:campaign": "campaignName",
            "keyword": "searchTerm",
            "ga:keyword": "searchTerm",
            "deviceCategory": "deviceCategory",
            "ga:deviceCategory": "deviceCategory",
            "country": "country",
            "ga:country": "country",
            "city": "city",
            "ga:city": "city",
            "browser": "browser",
            "ga:browser": "browser",
            "operatingSystem": "operatingSystem",
            "ga:operatingSystem": "operatingSystem",
            "attributionModel": "sessionDefaultChannelGroup",
            "ga:attributionModel": "sessionDefaultChannelGroup",
            "assistedConversions": "conversions",
            "ga:assistedConversions": "conversions",
            "lastClickConversions": "conversions",
            "ga:lastClickConversions": "conversions"
        }
        dimensions = [dimension_map.get(d, d) for d in query_params.get("dimensions", ["date"])]
        
        # Process order_by fields and ensure they're included in metrics/dimensions
        order_by_fields = []
        if "order_by" in query_params:
            for order in query_params["order_by"]:
                field = order["field"]
                # Map the field if it's deprecated
                mapped_field = metric_map.get(field, dimension_map.get(field, field))
                order_by_fields.append(mapped_field)
                
                # Ensure the field is included in either metrics or dimensions
                if mapped_field not in metrics and mapped_field not in dimensions:
                    # Try to determine if it's a metric or dimension based on common patterns
                    # Most GA4 metrics end with common suffixes, dimensions are usually descriptive
                    if any(mapped_field.endswith(suffix) for suffix in ['Users', 'Sessions', 'Views', 'Rate', 'Duration', 'Count']):
                        # Likely a metric
                        if mapped_field not in metrics:
                            metrics.append(mapped_field)
                            logging.info(f"Added order_by field '{mapped_field}' to metrics list")
                    else:
                        # Likely a dimension
                        if mapped_field not in dimensions:
                            dimensions.append(mapped_field)
                            logging.info(f"Added order_by field '{mapped_field}' to dimensions list")
        
        request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(
                start_date=query_params.get("date_range", {}).get("start_date", "30daysAgo"),
                end_date=query_params.get("date_range", {}).get("end_date", "today")
            )],
            metrics=[Metric(name=metric) for metric in metrics],
            dimensions=[Dimension(name=dim) for dim in dimensions]
        )
        
        # Add ordering if specified, using the mapped field names
        if order_by_fields:
            request.order_bys = []
            for i, order in enumerate(query_params["order_by"]):
                field = order["field"]
                mapped_field = metric_map.get(field, dimension_map.get(field, field))
                
                # Determine if it's a metric or dimension for ordering
                if mapped_field in metrics:
                    request.order_bys.append(
                        OrderBy(
                            metric=OrderBy.MetricOrderBy(
                                metric_name=mapped_field
                            ),
                            desc=order.get("direction", "DESCENDING") == "DESCENDING"
                        )
                    )
                elif mapped_field in dimensions:
                    request.order_bys.append(
                        OrderBy(
                            dimension=OrderBy.DimensionOrderBy(
                                dimension_name=mapped_field
                            ),
                            desc=order.get("direction", "DESCENDING") == "DESCENDING"
                        )
                    )
                else:
                    logging.warning(f"Order by field '{mapped_field}' not found in metrics or dimensions")
        
        return request
    
    def _format_response(self, response: Any, question: str) -> str:
        """Format the analytics response into a natural language answer."""
        # Convert GA4 response to JSON-serializable format
        analytics_data = convert_ga4_response_to_dict(response)
        
        # Check if we have any data
        if not analytics_data["rows"]:
            return (
                f"I couldn't find any data for your question about '{question}'. "
                "This could be due to:\n"
                "• No data available for the specified date range\n"
                "• The metrics/dimensions combination doesn't have data\n"
                "• The property might not have sufficient traffic\n\n"
                "Try asking about broader metrics like total users, sessions, or page views, "
                "or specify a different date range."
            )
        
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
    
    def _format_response_obj(self, data: dict, question: str) -> str:
        """Format the analytics response from a dict into a natural language answer."""
        # Check if we have any data after filtering
        if not data.get("rows", []):
            return (
                f"I couldn't find any data for your question about '{question}' after applying filters. "
                "This could be due to:\n"
                "• The filter criteria are too restrictive\n"
                "• No data matches the specified filter values\n"
                "• The filtered data is outside the date range\n\n"
                "Try relaxing the filter criteria or asking about broader metrics."
            )
        
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
            logging.error(f"Error generating analysis for filtered data: {e}")
            # Fallback to basic analysis
            return generate_basic_analysis(data, question)
    
    def answer_question(self, property_id: str, question: str) -> str:
        """Process a natural language question about analytics data and return a formatted answer."""
        try:
            # Parse the question into structured query parameters
            query_params = self._parse_query(question)
            
            # Build and execute the analytics request
            request = self._build_report_request(property_id, query_params)
            response = self.analytics_client.run_report(request)
            
            # Post-process filters in Python if any
            filters = query_params.get("filters", [])
            if filters:
                logging.warning(f"GA4 API filter not supported in this client version. Applying filters in post-processing: {filters}")
                data = convert_ga4_response_to_dict(response)
                filtered_rows = data["rows"]
                headers = data["dimension_headers"]
                for f in filters:
                    field = f["field"]
                    value = f["value"]
                    if field in headers:
                        idx = headers.index(field)
                        filtered_rows = [row for row in filtered_rows if row["dimension_values"][idx] == value]
                        logging.info(f"Applied filter: {field} = {value}, remaining rows: {len(filtered_rows)}")
                    else:
                        logging.warning(f"Filter field '{field}' not in dimension headers; skipping this filter.")
                data["rows"] = filtered_rows
                return self._format_response_obj(data, question)
            else:
                # No filters, proceed as before
                return self._format_response(response, question)
            
        except Exception as e:
            return f"I encountered an error while processing your question: {str(e)}"

    def run_template_query(self, template_key, date_range=None):
        template = QUESTION_TEMPLATES[template_key]
        metrics = template["metrics"]
        dimensions = template["dimensions"]
        if date_range is None:
            date_range = self._get_consistent_date_range()
        query_params = {
            "metrics": metrics,
            "dimensions": dimensions,
            "date_range": date_range
        }
        request = self._build_report_request(os.environ["GA4_PROPERTY_ID"], query_params)
        response = self.analytics_client.run_report(request)
        data = convert_ga4_response_to_dict(response)
        if template.get("postprocess") == "calculate_session_share":
            data = calculate_session_share(data)
        elif template.get("postprocess") == "find_high_traffic_low_conversion":
            data = find_high_traffic_low_conversion(data)
        return data

    def get_trend_analysis(self, property_id, metrics, dimensions, time_frames=None):
        """Get trend analysis data for multiple time frames."""
        if not time_frames:
            today = datetime.now()
            time_frames = [
                {"name": "last_7_days", "start_date": (today - timedelta(days=7)).strftime("%Y-%m-%d"), "end_date": today.strftime("%Y-%m-%d"), "comparison_start_date": (today - timedelta(days=14)).strftime("%Y-%m-%d"), "comparison_end_date": (today - timedelta(days=8)).strftime("%Y-%m-%d")},
                {"name": "last_30_days", "start_date": (today - timedelta(days=30)).strftime("%Y-%m-%d"), "end_date": today.strftime("%Y-%m-%d"), "comparison_start_date": (today - timedelta(days=60)).strftime("%Y-%m-%d"), "comparison_end_date": (today - timedelta(days=31)).strftime("%Y-%m-%d")}
            ]
        
        raw_trend_data = {}
        for tf in time_frames:
            try:
                # Build request for trend analysis
                request = RunReportRequest(
                    property=f"properties/{property_id}",
                    date_ranges=[
                        DateRange(start_date=tf["start_date"], end_date=tf["end_date"], name="current_period"),
                        DateRange(start_date=tf["comparison_start_date"], end_date=tf["comparison_end_date"], name="previous_period")
                    ],
                    metrics=[Metric(name=m) for m in metrics],
                    dimensions=[Dimension(name=d) for d in dimensions]
                )
                
                response = self.analytics_client.run_report(request)
                # Convert to the format expected by format_trend_data_for_humans
                raw_trend_data[tf["name"]] = {"rows": process_ga_response(response)}
                
            except Exception as e:
                logging.error(f"Error getting trend data for {tf['name']}: {e}")
                raw_trend_data[tf["name"]] = {"rows": []}
        
        return raw_trend_data

    def analyze_trends_with_insights(self, formatted_trends: dict, metrics: list, dimensions: list, date_range: str) -> dict:
        """
        Analyze trend data and generate AI-powered insights.
        
        Args:
            formatted_trends: The formatted trend data from format_trend_data_for_humans
            metrics: List of metrics that were analyzed
            dimensions: List of dimensions that were analyzed
            date_range: The date range that was analyzed
            
        Returns:
            Dict containing the original data and AI-generated insights
        """
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
            
            ai_insights = completion.choices[0].message.content.strip()
            
        except Exception as e:
            logging.error(f"Error generating AI insights for trends: {e}")
            ai_insights = "Unable to generate AI insights due to an error."
        
        return {
            "data": formatted_trends,
            "ai_insights": ai_insights
        }

    def answer_traffic_sources(self, date_range=None):
        data = self.run_template_query("traffic_sources", date_range)
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