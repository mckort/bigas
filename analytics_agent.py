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

class AnalyticsAgent:
    def __init__(self, openai_api_key: str):
        """Initialize the Analytics Agent with OpenAI API key."""
        self.openai_client = openai.OpenAI(api_key=openai_api_key)
        self.analytics_client = BetaAnalyticsDataClient()
        
    def _get_default_date_range(self) -> Dict[str, str]:
        """Get default date range for the last 30 days."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        return {
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d")
        }
        
    def _get_consistent_date_range(self) -> Dict[str, str]:
        """Get a consistent date range for reproducible results."""
        # Use a fixed 30-day period ending yesterday for consistency
        end_date = datetime.now() - timedelta(days=1)  # Yesterday
        start_date = end_date - timedelta(days=30)     # 30 days before yesterday
        return {
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d")
        }
        
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
            model="o4-mini",
            max_completion_tokens=2000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question}
            ],
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        if not content:
            logging.error("OpenAI response content is empty.")
            return {"error": "OpenAI returned an empty response."}
        try:
            query_params = json.loads(content)
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse OpenAI response as JSON: {e} | Content: {content}")
            return {"error": f"Failed to parse OpenAI response as JSON. {e}"}
        
        # Validate that we have the required fields
        if not isinstance(query_params, dict):
            logging.error(f"OpenAI response is not a dictionary: {type(query_params)}")
            return {"error": "OpenAI response is not in the expected format"}
        
        # Log the generated query parameters for debugging
        logging.info(f"Generated query parameters for question '{question}': {json.dumps(query_params, indent=2)}")
        
        # Log the date range being used
        date_range = query_params.get("date_range", {})
        start_date = date_range.get("start_date", "30daysAgo")
        end_date = date_range.get("end_date", "today")
        logging.info(f"Using date range: {start_date} to {end_date}")
        
        # Ensure we have valid date range
        if "date_range" not in query_params:
            query_params["date_range"] = self._get_consistent_date_range()
            logging.info("Using consistent date range for reproducibility")
            
        # Ensure we have metrics for user trends
        if "metrics" not in query_params:
            query_params["metrics"] = ["totalUsers", "newUsers"]
            
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
    
    def _convert_ga4_response_to_dict(self, response: Any) -> Dict[str, Any]:
        """Convert GA4 API response to a JSON-serializable dictionary."""
        result = {
            "dimension_headers": [header.name for header in response.dimension_headers],
            "metric_headers": [header.name for header in response.metric_headers],
            "rows": []
        }
        
        for row in response.rows:
            row_data = {
                "dimension_values": [value.value for value in row.dimension_values],
                "metric_values": [value.value for value in row.metric_values]
            }
            result["rows"].append(row_data)
            
        return result
    
    def _format_response(self, response: Any, question: str) -> str:
        """Format the analytics response into a natural language answer."""
        # Convert GA4 response to JSON-serializable format
        analytics_data = self._convert_ga4_response_to_dict(response)
        
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
            model="o4-mini",
            max_completion_tokens=2000,
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
                model="o4-mini",
                max_completion_tokens=1500,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(analysis_data)}
                ]
            )
            return completion.choices[0].message.content
        except Exception as e:
            logging.error(f"Error generating analysis for filtered data: {e}")
            # Fallback to basic analysis
            return self._generate_basic_analysis(data, question)
    
    def _generate_basic_analysis(self, data: dict, question: str) -> str:
        """Generate a basic human-readable analysis when OpenAI fails."""
        rows = data.get("rows", [])
        if not rows:
            return "No data available for analysis."
        
        # Extract key metrics
        page_views = []
        conversions = []
        
        for row in rows:
            if len(row.get("metric_values", [])) >= 2:
                try:
                    page_views.append(int(row["metric_values"][0]))
                    conversions.append(int(row["metric_values"][1]))
                except (ValueError, IndexError):
                    continue
        
        if not page_views:
            return "Unable to analyze the data due to format issues."
        
        # Calculate insights
        total_page_views = sum(page_views)
        total_conversions = sum(conversions)
        avg_page_views = total_page_views / len(page_views)
        
        # Find pages with high traffic but low conversions
        high_traffic_low_conversion = []
        for i, row in enumerate(rows):
            if len(row.get("metric_values", [])) >= 2:
                try:
                    pv = int(row["metric_values"][0])
                    conv = int(row["metric_values"][1])
                    if pv > avg_page_views and conv == 0:
                        page_name = row.get("dimension_values", ["Unknown"])[1] if len(row.get("dimension_values", [])) > 1 else "Unknown"
                        high_traffic_low_conversion.append((page_name, pv))
                except (ValueError, IndexError):
                    continue
        
        # Generate analysis
        analysis = f"Based on the analytics data for '{question}':\n\n"
        analysis += f"• **Total Pages Analyzed**: {len(rows)}\n"
        analysis += f"• **Total Page Views**: {total_page_views:,}\n"
        analysis += f"• **Total Conversions**: {total_conversions:,}\n"
        analysis += f"• **Average Page Views per Page**: {avg_page_views:.1f}\n\n"
        
        if high_traffic_low_conversion:
            analysis += "**Pages with High Traffic but No Conversions:**\n"
            for page_name, page_views in sorted(high_traffic_low_conversion, key=lambda x: x[1], reverse=True):
                analysis += f"• {page_name}: {page_views:,} page views, 0 conversions\n"
        else:
            analysis += "**Good News**: No pages with high traffic and zero conversions were found.\n"
        
        return analysis
    
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
                data = self._convert_ga4_response_to_dict(response)
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