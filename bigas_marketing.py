# main.py (Example using Python and Flask)
from flask import Flask, jsonify, request, send_file
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
    Filter,
    FilterExpression,
    OrderBy,
    MetricAggregation,
)
import os
import openai
from datetime import datetime, timedelta
import time
import logging
import json
from collections import defaultdict
import traceback
from analytics_agent import AnalyticsAgent
import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Replace with your GA4 Property ID
# You can get this from analytics.google.com -> Admin -> Property Settings
# Ensure this environment variable is set before running the app
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID")
if not GA4_PROPERTY_ID:
    raise ValueError("GA4_PROPERTY_ID environment variable not set. Please set it (e.g., export GA4_PROPERTY_ID='123456789').")
# Make sure the property ID is prefixed correctly for the API
GA4_API_PROPERTY_ID = f"properties/{GA4_PROPERTY_ID}"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
# logger.warning("OPENAI_API_KEY environment variable not set. LLM features will not work.")

# Initialize the Google Analytics Data API client
# Authentication will typically be handled by gcloud CLI default credentials
# or by setting GOOGLE_APPLICATION_CREDENTIALS environment variable.
try:
    client = BetaAnalyticsDataClient()
    # logger.info("Google Analytics Data API client initialized.")
except Exception as e:
    logger.error(f"Failed to initialize Google Analytics Data API client: {e}")
    client = None # Set client to None if initialization fails

# Cache configuration - This custom cache remains, but lru_cache is removed from _get_ga_report_uncached
CACHE_DURATION = 3600  # 1 hour in seconds
analytics_cache = {}

def get_cached_data(cache_key):
    """Get data from cache if it exists and is not expired"""
    if cache_key in analytics_cache:
        timestamp, data = analytics_cache[cache_key]
        if time.time() - timestamp < CACHE_DURATION:
            # logger.info(f"Cache hit for key: {cache_key}")
            return data
    # logger.info(f"Cache miss for key: {cache_key}")
    return None

def set_cached_data(cache_key, data):
    """Store data in cache with timestamp"""
    analytics_cache[cache_key] = (time.time(), data)
    # logger.info(f"Data cached for key: {cache_key}")


# Removed @lru_cache from here for debugging purposes
def get_ga_report( # Renamed back from _get_ga_report_uncached
    property_id: str, # Raw GA4 ID
    start_date: str,
    end_date: str,
    metrics: list, # Now expects list, will convert internally for API
    dimensions: list, # Now expects list, will convert internally for API
    dimension_filters: list = None,
    order_bys: list = None,
    limit: int = None,
    comparison_start_date: str = None,
    comparison_end_date: str = None
):
    """
    Fetches a report from Google Analytics Data API.
    Explicitly converts metric/dimension lists to tuples of strings for robustness.
    """
    if not client:
        raise RuntimeError("Google Analytics Data API client is not initialized.")

    property_id_api_format = f"properties/{property_id}" if not property_id.startswith("properties/") else property_id

    date_ranges = [DateRange(start_date=start_date, end_date=end_date, name="current_period")]

    if comparison_start_date and comparison_end_date:
        date_ranges.append(
            DateRange(
                start_date=comparison_start_date,
                end_date=comparison_end_date,
                name="previous_period"
            )
        )

    filter_expression = None
    if dimension_filters:
        # Assuming dimension_filters is a list of Filter objects or similar
        filter_expression = FilterExpression(and_group=FilterExpression.AndGroup(expressions=dimension_filters))

    # Convert metrics and dimensions to the required API objects
    converted_metrics = [Metric(name=m) for m in metrics]
    converted_dimensions = [Dimension(name=d) for d in dimensions]

    request = RunReportRequest(
        property=property_id_api_format,
        date_ranges=date_ranges,
        metrics=converted_metrics,
        dimensions=converted_dimensions,
        dimension_filter=filter_expression,
        order_bys=list(order_bys) if order_bys else None,
        limit=limit
    )

    # logger.info(f"Making GA API request for period {start_date} to {end_date} for metrics={metrics}, dimensions={dimensions}")
    response = client.run_report(request, timeout=20)
    return response

# Custom cache wrapper around get_ga_report (now the primary GA fetcher)
def get_ga_report_with_cache(
    property_id: str,
    start_date: str,
    end_date: str,
    metrics: list,
    dimensions: list,
    dimension_filters: list = None,
    order_bys: list = None,
    limit: int = None,
    comparison_start_date: str = None,
    comparison_end_date: str = None
):
    """
    Wrapper function to add custom caching logic around the get_ga_report call.
    Uses get_ga_report (which directly calls client.run_report).
    """
    # Create a hashable key for the custom cache
    def tuple_to_str(t):
        return ",".join(map(str, t)) if isinstance(t, tuple) else str(t)

    cache_key_parts = [
        property_id, # Use raw ID here
        start_date,
        end_date,
        tuple(metrics), # Convert list to tuple for hashable key
        tuple(dimensions) # Convert list to tuple for hashable key
    ]
    if dimension_filters:
        cache_key_parts.append(json.dumps(str(dimension_filters), sort_keys=True))
    if order_bys:
        cache_key_parts.append(json.dumps(str(order_bys), sort_keys=True))
    if limit is not None:
        cache_key_parts.append(str(limit))
    if comparison_start_date:
        cache_key_parts.append(comparison_start_date)
    if comparison_end_date:
        cache_key_parts.append(comparison_end_date)
    
    cache_key = "_".join([tuple_to_str(part) for part in cache_key_parts])
    
    cached_data = get_cached_data(cache_key)
    if cached_data:
        return cached_data

    try:
        response = get_ga_report( # Call the direct GA API function
            property_id=property_id,
            start_date=start_date,
            end_date=end_date,
            metrics=metrics,
            dimensions=dimensions,
            dimension_filters=dimension_filters,
            order_bys=order_bys,
            limit=limit,
            comparison_start_date=comparison_start_date,
            comparison_end_date=comparison_end_date
        )
        set_cached_data(cache_key, response)
        return response
    except Exception as e:
        logger.error(f"Error in get_ga_report_with_cache: {str(e)}")
        raise # Re-raise to be caught by the calling endpoint


def process_ga_response(response):
    """
    Process GA4 response into a more usable format (list of dicts).
    Handles multiple date ranges (e.g., current vs. previous period).
    """
    processed_data = []
    dimension_names = [header.name for header in response.dimension_headers]
    metric_names = [header.name for header in response.metric_headers]
    for row in response.rows:
        row_data = {}
        for i, dimension_value in enumerate(row.dimension_values):
            val = dimension_value.value
            if isinstance(val, tuple):
                # logger.warning(f"[process_ga_response] Found tuple in dimension_value: {val}")
                val = str(val)
            row_data[dimension_names[i]] = val
        for i, metric_value in enumerate(row.metric_values):
            val = metric_value.value
            if isinstance(val, tuple):
                # logger.warning(f"[process_ga_response] Found tuple in metric_value: {val}")
                val = str(val)
            row_data[metric_names[i]] = val
        processed_data.append(row_data)
    # logger.info(f"[process_ga_response] Processed rows: {processed_data[:3]}... (total {len(processed_data)})")
    return processed_data

def get_date_range_strings(num_days: int) -> tuple[str, str]:
    """Helper to get start and end dates for a given number of days ago."""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=num_days)).strftime("%Y-%m-%d")
    return start_date, end_date

def get_trend_analysis(
    property_id: str,
    metrics: list,
    dimensions: list,
    time_frames: list = None
):
    """
    Fetches analytics data for various time frames and their comparisons.
    Returns raw processed data for each time frame.
    """
    if not time_frames:
        today = datetime.now()
        time_frames = [
            {
                "name": "last_7_days",
                "start_date": (today - timedelta(days=7)).strftime("%Y-%m-%d"),
                "end_date": today.strftime("%Y-%m-%d"),
                "comparison_start_date": (today - timedelta(days=14)).strftime("%Y-%m-%d"),
                "comparison_end_date": (today - timedelta(days=8)).strftime("%Y-%m-%d")
            },
            {
                "name": "last_30_days",
                "start_date": (today - timedelta(days=30)).strftime("%Y-%m-%d"),
                "end_date": today.strftime("%Y-%m-%d"),
                "comparison_start_date": (today - timedelta(days=60)).strftime("%Y-%m-%d"),
                "comparison_end_date": (today - timedelta(days=31)).strftime("%Y-%m-%d")
            },
            {
                "name": "last_90_days",
                "start_date": (today - timedelta(days=90)).strftime("%Y-%m-%d"),
                "end_date": today.strftime("%Y-%m-%d"),
                "comparison_start_date": (today - timedelta(days=180)).strftime("%Y-%m-%d"),
                "comparison_end_date": (today - timedelta(days=91)).strftime("%Y-%m-%d")
            },
        ]
    trend_data = {}
    for time_frame in time_frames:
        try:
            # logger.info(f"[get_trend_analysis] Fetching for {time_frame['name']} from {time_frame['start_date']} to {time_frame['end_date']}")
            response = get_ga_report_with_cache(
                property_id=property_id,
                start_date=time_frame["start_date"],
                end_date=time_frame["end_date"],
                metrics=metrics,
                dimensions=dimensions,
                comparison_start_date=time_frame.get("comparison_start_date"),
                comparison_end_date=time_frame.get("comparison_end_date")
            )
            # Safely stringify each row for logging
            # logger.info(f"[get_trend_analysis] Raw response rows for {time_frame['name']}: {[str(x) for x in response.rows[:3]]}")
            try:
                processed = process_ga_response(response)
            except Exception as e:
                logger.error(f"[get_trend_analysis] Exception in process_ga_response for {time_frame['name']}: {e}\n{traceback.format_exc()}")
                trend_data[time_frame["name"]] = {"error": str(e)}
                continue
            # logger.info(f"[get_trend_analysis] {time_frame['name']} processed: {[str(x) for x in processed[:3]]}...")
            trend_data[time_frame["name"]] = processed
        except Exception as e:
            logger.error(f"[get_trend_analysis] Error in time frame {time_frame['name']}: {str(e)}")
            trend_data[time_frame["name"]] = {"error": str(e)}
    return trend_data

def get_basic_analytics(property_id: str, start_date: str, end_date: str):
    """
    Fetches basic overall website performance metrics.
    A fallback if comprehensive trend analysis fails.
    """
    metrics = ["activeUsers", "sessions", "screenPageViews", "averageSessionDuration", "engagedSessions", "engagementRate"]
    dimensions = [] # No dimensions for overall summary
    try:
        response = get_ga_report_with_cache( # Use the cached version here
            property_id=property_id,
            start_date=start_date,
            end_date=end_date,
            metrics=metrics,
            dimensions=dimensions
        )
        # For overall metrics, the response will often have one row or aggregates in `totals`
        # Let's process the first row for a summary
        if response.rows:
            overall_summary = {}
            for i, metric_value in enumerate(response.rows[0].metric_values):
                overall_summary[response.metric_headers[i].name] = metric_value.value
            return {"metrics": overall_summary}
        return {}
    except Exception as e:
        logger.error(f"Error fetching basic analytics: {str(e)}")
        return {"error": str(e)}


def format_trend_data(raw_trend_data: dict) -> dict:
    """
    Formats raw GA4 report responses into a structured summary for LLM analysis,
    including period-over-period comparisons.
    """
    # logger.info(f"[format_trend_data] Raw input: {json.dumps(raw_trend_data, default=str)[:1000]}...")

    formatted_data = {
        "summary": {
            "overall_performance": {},
            "key_insights": [],
            "recommendations": []
        },
        "time_frames_details": {},
        "period_over_period_trends": {}
    }

    if not raw_trend_data or not isinstance(raw_trend_data, dict):
        # logger.warning("[format_trend_data] No raw_trend_data or not a dict.")
        formatted_data["summary"]["key_insights"].append("No analytics data available.")
        return formatted_data

    aggregated_data_by_time_frame = {}
    for time_frame_name, rows_or_error in raw_trend_data.items():
        if isinstance(rows_or_error, dict) and "error" in rows_or_error:
            # logger.warning(f"[format_trend_data] Error in time frame {time_frame_name}: {rows_or_error['error']}")
            aggregated_data_by_time_frame[time_frame_name] = {"error": rows_or_error["error"]}
            continue
        
        rows = rows_or_error
        if not rows or not isinstance(rows, list):
            # logger.warning(f"[format_trend_data] No rows or not a list for {time_frame_name}.")
            aggregated_data_by_time_frame[time_frame_name] = {"error": "No data for this time frame."}
            continue

        # Sanitize all values in all rows: convert tuples to strings and log them
        for row in rows:
            for k, v in row.items():
                if isinstance(v, tuple):
                    # logger.warning(f"[format_trend_data] Found tuple in row for {time_frame_name}: {k}={v}")
                    row[k] = str(v)
        # logger.info(f"[format_trend_data] {time_frame_name} sanitized rows: {rows[:3]}...")

        aggregated_metrics = defaultdict(float)
        aggregated_dimensions = defaultdict(lambda: defaultdict(float))

        # logger.info(f"[format_trend_data] Aggregating metrics: {aggregated_metrics}")
        # logger.info(f"[format_trend_data] Aggregating dimensions: {aggregated_dimensions}")

        if rows:
            sample_row = rows[0]
            metric_names = [k for k, v in sample_row.items() if isinstance(v, (int, float)) or (isinstance(v, str) and v.replace('.', '', 1).isdigit())]
            dimension_names = [k for k, v in sample_row.items() if k not in metric_names]


            for row in rows:
                for metric_name in metric_names:
                    try:
                        value = float(row.get(metric_name, 0))
                        aggregated_metrics[metric_name] += value
                    except ValueError:
                        continue
                for dim_name in dimension_names:
                    dim_value = row.get(dim_name)
                    if dim_value:
                        relevant_metric_for_dim = "activeUsers"
                        if relevant_metric_for_dim in row:
                            try:
                                aggregated_dimensions[dim_name][dim_value] += float(row[relevant_metric_for_dim])
                            except ValueError:
                                pass

        # Aggregation and sorting with pinpoint logging
        final_metrics = {}
        num_rows_for_avg = max(1, len(rows)) 
        for k, v in aggregated_metrics.items():
            try:
                if k in ["engagementRate", "bounceRate"]:
                    final_metrics[k] = f"{round(v * 100 / num_rows_for_avg, 2)}%"
                elif k in ["averageSessionDuration"]:
                    final_metrics[k] = f"{round(v / num_rows_for_avg, 2)}s"
                elif k in ["totalRevenue"]:
                    final_metrics[k] = f"${round(v, 2)}"
                else:
                    final_metrics[k] = int(v)
            except Exception as e:
                logger.error(f"[format_trend_data] Error aggregating metric {k}: {v} | Error: {e}")
                final_metrics[k] = str(v)

        final_dimensions = {}
        for dim_name, dim_values_map in aggregated_dimensions.items():
            try:
                sorted_items = sorted(dim_values_map.items(), key=lambda item: item[1], reverse=True)[:5]
            except Exception as e:
                logger.error(f"[format_trend_data] Error sorting dim_values_map for {dim_name}: {dim_values_map} | Error: {e}")
                sorted_items = list(dim_values_map.items())[:5]
            try:
                safe_items = [(str(k), float(v)) for k, v in sorted_items]
            except Exception as e:
                logger.error(f"[format_trend_data] Error converting sorted_items to safe_items for {dim_name}: {sorted_items} | Error: {e}")
                safe_items = [(str(k), str(v)) for k, v in sorted_items]
            try:
                final_dimensions[dim_name] = {k: v for k, v in safe_items}
            except Exception as e:
                logger.error(f"[format_trend_data] Error building final_dimensions for {dim_name}: {safe_items} | Error: {e}")
                final_dimensions[dim_name] = {}

        aggregated_data_by_time_frame[time_frame_name] = {
            "metrics": final_metrics,
            "dimensions": final_dimensions,
            "raw_rows": rows
        }
        # logger.info(f"[format_trend_data] Aggregated for {time_frame_name}: metrics={final_metrics}, dimensions={list(final_dimensions.keys())}")

    formatted_data["time_frames_details"] = aggregated_data_by_time_frame

    # Calculate Period-Over-Period Trends
    time_frame_names_sorted = [
        k for k in raw_trend_data.keys() 
        if not k.endswith("_comparison") and 
           isinstance(raw_trend_data[k], list) and 
           raw_trend_data[k]
    ]
    
    def safe_date_parser(time_frame_name_key):
        rows_data = raw_trend_data.get(time_frame_name_key)
        if rows_data and isinstance(rows_data, list) and rows_data and 'date' in rows_data[0]:
            try:
                return datetime.strptime(rows_data[0]['date'], '%Y-%m-%d')
            except ValueError:
                pass
        return datetime.min

    time_frame_names_sorted.sort(key=safe_date_parser)

    for i in range(len(time_frame_names_sorted)):
        current_tf_name = time_frame_names_sorted[i]
        current_data = aggregated_data_by_time_frame.get(current_tf_name)
        
        comparison_tf_name = f"{current_tf_name}_comparison"
        comparison_data = aggregated_data_by_time_frame.get(comparison_tf_name)

        if not current_data or "error" in current_data:
            continue

        if comparison_data and "error" not in comparison_data:
            trend_key = f"{comparison_tf_name.replace('_comparison', '')}_vs_{current_tf_name}"
            period_trend = {}
            for metric, current_val in current_data["metrics"].items():
                try:
                    current_num = float(str(current_val).replace('%', '').replace('s', '').replace('$', ''))
                    
                    comparison_val = comparison_data["metrics"].get(metric)
                    if comparison_val is not None:
                        comparison_num = float(str(comparison_val).replace('%', '').replace('s', '').replace('$', ''))

                        if comparison_num != 0:
                            change_percent = ((current_num - comparison_num) / comparison_num) * 100
                            period_trend[metric] = {
                                "current": current_val,
                                "previous": comparison_val,
                                "change_percent": round(change_percent, 2)
                            }
                        else:
                            period_trend[metric] = {
                                "current": current_val,
                                "previous": comparison_val,
                                "change_percent": "N/A (previous was 0)"
                            }
                except (ValueError, TypeError) as e:
                    # logger.warning(f"[format_trend_data] Error calculating trend for metric '{metric}' in {current_tf_name} vs {comparison_tf_name}: {e}. Skipping this metric for trend calculation.")
                    pass
            
            if period_trend:
                formatted_data["period_over_period_trends"][trend_key] = period_trend

    # Generate overall performance summary (from the latest time frame)
    if time_frame_names_sorted:
        latest_tf_name = time_frame_names_sorted[-1]
        formatted_data["summary"]["overall_performance"] = {
            "current_period": latest_tf_name,
            "metrics": aggregated_data_by_time_frame.get(latest_tf_name, {}).get("metrics", {})
        }

    # Generate key insights and recommendations (Simplified for example)
    insights = []
    recommendations = []

    for trend_name, trend_details in formatted_data["period_over_period_trends"].items():
        for metric, data in trend_details.items():
            if isinstance(data.get("change_percent"), (int, float)):
                change = data["change_percent"]
                direction = "increased" if change > 0 else "decreased"
                abs_change = abs(change)
                insight_desc = f"{metric} {direction} by {abs_change}% in {trend_name}"
                
                insights.append({
                    "type": "metric_change",
                    "metric": metric,
                    "description": insight_desc,
                    "change_percent": change
                })

                # Simple recommendation logic
                if metric == "sessions" and change < -10:
                    recommendations.append(f"Investigate reason for {abs_change}% drop in sessions and improve user acquisition strategies.")
                elif metric == "conversions" and change < -10:
                    recommendations.append(f"Analyze conversion funnel for {abs_change}% drop in conversions and optimize CTAs.")
                elif metric == "engagementRate" and change < -5:
                    recommendations.append(f"Improve content relevance and website user experience to address {abs_change}% drop in engagement rate.")
                elif metric == "activeUsers" and change > 15:
                    recommendations.append(f"Capitalize on the {abs_change}% increase in active users by nurturing existing audience and encouraging repeat visits.")
            
    # Add insights/recommendations based on static dimensions (e.g., top countries, devices)
    if formatted_data["time_frames_details"]:
        latest_tf_name = time_frame_names_sorted[-1] if time_frame_names_sorted else None
        if latest_tf_name:
            latest_dims = formatted_data["time_frames_details"].get(latest_tf_name, {}).get("dimensions", {})
            if "country" in latest_dims:
                for country, value in latest_dims["country"].items():
                    total_users = aggregated_data_by_time_frame.get(latest_tf_name, {}).get("metrics", {}).get("activeUsers", 0)
                    if isinstance(total_users, str):
                        total_users = float(total_users.replace('%', '').replace('s', '').replace('$', ''))
                    
                    if total_users > 0:
                        percentage = (value / total_users) * 100
                        if percentage > 40:
                            insights.append(f"High user concentration: {country} accounts for {round(percentage, 1)}% of active users.")
                            recommendations.append(f"Consider localized content or campaigns for {country} to further engage this dominant audience.")
            if "deviceCategory" in latest_dims:
                for device, value in latest_dims["deviceCategory"].items():
                    total_users = aggregated_data_by_time_frame.get(latest_tf_name, {}).get("metrics", {}).get("activeUsers", 0)
                    if isinstance(total_users, str):
                        total_users = float(total_users.replace('%', '').replace('s', '').replace('$', ''))
                    
                    if total_users > 0:
                        percentage = (value / total_users) * 100
                        if percentage > 60 and device == "mobile":
                            insights.append(f"Mobile is dominant: {device} accounts for {round(percentage, 1)}% of active users.")
                            recommendations.append("Ensure mobile responsiveness and performance are top-notch for optimal user experience.")


    formatted_data["summary"]["key_insights"] = insights
    formatted_data["summary"]["recommendations"] = recommendations

    # logger.info(f"[format_trend_data] Final output: {json.dumps(formatted_data, default=str)[:1000]}...")
    return formatted_data


@app.route('/mcp/tools/fetch_analytics_report', methods=['POST'])
def fetch_analytics_report():
    """
    Fetch Google Analytics report for the specified property.

    This endpoint queries the Google Analytics Data API for active users by country
    for the property specified by the GA4_PROPERTY_ID environment variable. It returns
    a JSON object with a status and a data field. The data field is a list of dictionaries,
    each containing a country and the corresponding number of active users.

    Args:
        JSON body with optional parameters:
            start_date (str): Start date inYYYY-MM-DD format (default: 30 days ago)
            end_date (str): End date inYYYY-MM-DD format (default: today)
            metrics (list): List of metrics to fetch (default: ["activeUsers"])
            dimensions (list): List of dimensions to fetch (default: ["country"])

    Returns:
        Response: JSON response with status and data or error message.
        Example success response:
            {
                "status": "success",
                "data": [
                    {"country": "United States", "activeUsers": "123"},
                    ...
                ]
            }
        Example error response:
            {
                "status": "error",
                "message": "Error message here."
            }
    """
    try:
        params = request.get_json() or {}
        
        # Get date ranges with defaults
        end_date = params.get("end_date", datetime.now().strftime("%Y-%m-%d"))
        start_date = params.get("start_date", (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
        
        # Get metrics and dimensions with defaults
        metrics = params.get("metrics", ["activeUsers"])
        dimensions = params.get("dimensions", ["country"])

        # Use the get_ga_report_with_cache function with the provided parameters
        response = get_ga_report_with_cache( # Using the cached version
            property_id=GA4_PROPERTY_ID,
            start_date=start_date,
            end_date=end_date,
            metrics=metrics,
            dimensions=dimensions
        )
        report_data = process_ga_response(response)
        return jsonify({"status": "success", "data": report_data})
    except Exception as e:
        logger.error(f"Error in fetch_analytics_report endpoint: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/mcp/tools/fetch_custom_report', methods=['POST'])
def fetch_custom_report():
    """
    Fetch a custom Google Analytics report based on user-specified dimensions, metrics, and date ranges.
    Expects JSON with:
        - dimensions: list of dimension names (e.g., ["country", "city"])
        - metrics: list of metric names (e.g., ["activeUsers"])
        - date_ranges: list of dicts with 'start_date' and 'end_date'
    """
    params = request.get_json()
    dimensions_names = params.get("dimensions", ["country"])
    metrics_names = params.get("metrics", ["activeUsers"])
    date_ranges_dicts = params.get("date_ranges", [{"start_date": "30daysAgo", "end_date": "yesterday"}])

    date_ranges = []
    for dr_dict in date_ranges_dicts:
        start = dr_dict.get("start_date")
        end = dr_dict.get("end_date")

        # Basic relative date parsing, extend as needed
        if start == "yesterday":
            start = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        elif start == "30daysAgo":
            start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        if end == "today":
            end = datetime.now().strftime("%Y-%m-%d")
        elif end == "yesterday":
            end = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        date_ranges.append(DateRange(start_date=start, end_date=end, name=dr_dict.get("name", "period")))

    try:
        if not date_ranges:
            raise ValueError("At least one date range must be provided.")
        
        primary_date_range = date_ranges[0]
        comparison_date_range = date_ranges[1] if len(date_ranges) > 1 else None

        response = get_ga_report_with_cache( # Using the cached version
            property_id=GA4_PROPERTY_ID,
            start_date=primary_date_range.start_date,
            end_date=primary_date_range.end_date,
            metrics=metrics_names,
            dimensions=dimensions_names,
            comparison_start_date=comparison_date_range.start_date if comparison_date_range else None,
            comparison_end_date=comparison_date_range.end_date if comparison_date_range else None
        )
        
        report_data = process_ga_response(response)
        return jsonify({"status": "success", "data": report_data})
    except Exception as e:
        logger.error(f"Error in fetch_custom_report endpoint: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})


@app.route('/mcp/tools/ask_analytics_question', methods=['POST'])
def ask_analytics_question():
    """
    Endpoint for asking natural language questions about analytics data.
    Uses the AnalyticsAgent to process questions and return formatted answers.
    """
    try:
        if not OPENAI_API_KEY:
            return jsonify({
                "error": "OpenAI API key not configured. Please set OPENAI_API_KEY environment variable."
            }), 500

        data = request.get_json()
        if not data or 'question' not in data:
            return jsonify({
                "error": "Missing required field: question"
            }), 400

        question = data['question']
        property_id = data.get('property_id', GA4_PROPERTY_ID)

        # Initialize the analytics agent
        agent = AnalyticsAgent(openai_api_key=OPENAI_API_KEY)
        
        # Process the question and get the answer
        answer = agent.answer_question(property_id, question)
        
        return jsonify({
            "answer": answer,
            "question": question
        })

    except Exception as e:
        logger.error(f"Error in ask_analytics_question: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Failed to process question: {str(e)}"
        }), 500

@app.route('/mcp/tools/analyze_trends', methods=['POST'])
def analyze_trends():
    """
    Analyze trends in analytics data across multiple time frames.
    
    Args:
        JSON body with optional parameters:
            metrics (list): List of metrics to analyze (default: ["activeUsers", "sessions", "conversions"])
            dimensions (list): List of dimensions to analyze (default: ["country"])
            time_frames (list): List of custom time frame configurations (optional)
    
    Returns:
        Response: JSON response with status and formatted trend analysis data or error message.
    """
    try:
        params = request.get_json() or {}
        
        metrics = params.get("metrics", ["activeUsers", "sessions", "conversions"])
        dimensions = params.get("dimensions", ["country", "sessionDefaultChannelGroup", "deviceCategory"])
        time_frames = params.get("time_frames")

        raw_trend_data = get_trend_analysis(
            property_id=GA4_PROPERTY_ID,
            metrics=metrics,
            dimensions=dimensions,
            time_frames=time_frames
        )

        formatted_data = format_trend_data(raw_trend_data)

        return jsonify({
            "status": "success",
            "data": formatted_data
        })
    except Exception as e:
        logger.error(f"Error in analyze_trends endpoint: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/mcp/manifest', methods=['GET'])
def manifest():
    """
    Returns a manifest describing the available MCP tools and their parameters.
    """
    return jsonify({
        "tools": [
            {
                "name": "fetch_analytics_report",
                "description": "Fetch Google Analytics report for the specified property. Returns analytics data based on provided parameters.",
                "endpoint": "/mcp/tools/fetch_analytics_report",
                "method": "POST",
                "parameters": {
                    "start_date": "string (YYYY-MM-DD format, optional, default: 30 days ago)",
                    "end_date": "string (YYYY-MM-DD format, optional, default: today)",
                    "metrics": "list of strings (optional, default: ['activeUsers'])",
                    "dimensions": "list of strings (optional, default: ['country'])"
                },
                "responses": {
                    "success": {
                        "status": "success",
                        "data": [
                            {"dimension1": "string", "metric1": "string"}
                        ]
                    },
                    "error": {
                        "status": "error",
                        "message": "string"
                    }
                }
            },
            {
                "name": "fetch_custom_report",
                "description": "Fetch a custom Google Analytics report based on user-specified dimensions, metrics, and date ranges. Supports multiple date ranges for comparison.",
                "endpoint": "/mcp/tools/fetch_custom_report",
                "method": "POST",
                "parameters": {
                    "dimensions": "list of dimension names (e.g., ['country', 'city'])",
                    "metrics": "list of metric names (e.g., ['activeUsers'])",
                    "date_ranges": "list of dicts with 'start_date' and 'end_date', and optional 'name' (e.g., [{'start_date': '2025-01-01', 'end_date': '2025-01-31', 'name': 'primary'}, {'start_date': '2024-12-01', 'end_date': '2024-12-31', 'name': 'comparison'}])"
                },
                "responses": {
                    "success": {
                        "status": "success",
                        "data": [
                            {"dimension1": "string", "metric1": "string"}
                        ]
                    },
                    "error": {
                        "status": "error",
                        "message": "string"
                    }
                }
            },
            {
                "name": "ask_analytics_question",
                "description": "Accepts a natural language question about analytics data and provides a detailed analysis with insights and recommendations. The response includes overall performance, channel performance, top pages, and demographic data. Requires GA4_PROPERTY_ID and OPENAI_API_KEY environment variables.",
                "endpoint": "/mcp/tools/ask_analytics_question",
                "method": "POST",
                "parameters": {
                    "question": "string (the user's question about analytics data)"
                },
                "responses": {
                    "success": {
                        "status": "success",
                        "answer": "string (markdown formatted analysis)",
                        "formatted": "boolean",
                        "raw_data_sent_to_llm": "object (raw data sent to LLM for debugging)",
                        "full_formatted_analytics": "object (complete structured analytics data)"
                    },
                    "error": {
                        "status": "error",
                        "message": "string"
                    }
                }
            },
            {
                "name": "analyze_trends",
                "description": "Analyze trends in analytics data across multiple time frames (default: last 7, 30, 90 days). Returns formatted data with summaries, key insights, recommendations, and period-over-period comparisons. Requires GA4_PROPERTY_ID.",
                "endpoint": "/mcp/tools/analyze_trends",
                "method": "POST",
                "parameters": {
                    "metrics": "list of strings (optional, default: ['activeUsers', 'sessions', 'conversions'])",
                    "dimensions": "list of strings (optional, default: ['country', 'sessionDefaultChannelGroup', 'deviceCategory'])",
                    "time_frames": "list of objects (optional, custom time frame configurations, e.g., [{'name': 'custom_week', 'start_date': 'YYYY-MM-DD', 'end_date': 'YYYY-MM-DD', 'comparison_start_date': 'YYYY-MM-DD', 'comparison_end_date': 'YYYY-MM-DD'}])"
                },
                "responses": {
                    "success": {
                        "status": "success",
                        "data": {
                            "summary": "object (overall trend summary with insights and recommendations)",
                            "time_frames_details": "object (detailed aggregated data for each period)",
                            "period_over_period_trends": "object (comparison data between periods)"
                        }
                    },
                    "error": {
                        "status": "error",
                        "message": "string"
                    }
                }
            },
            {
                "name": "weekly_analytics_report",
                "description": "Runs the weekly analytics Q&A and posts results to Discord. Returns a JSON summary of all Q&A pairs. Requires DISCORD_WEBHOOK_URL and OPENAI_API_KEY environment variables.",
                "endpoint": "/mcp/tools/weekly_analytics_report",
                "method": "POST",
                "parameters": {},
                "responses": {
                    "success": {
                        "status": "success",
                        "results": [
                            {"question": "string", "answer": "string"}
                        ]
                    },
                    "error": {
                        "error": "string"
                    }
                }
            }
        ]
    })

QUESTIONS = [
    "What are the primary traffic sources (e.g., organic search, direct, referral, paid search, social, email) contributing to total sessions, and what is their respective share?",
    "What is the average session duration and pages per session across all users?",
    "Which pages are the most visited, and how do they contribute to conversions (e.g., product pages, category pages, blog posts)?",
    "Which pages or sections (e.g., blog, product pages, landing pages) drive the most engagement (e.g., time on page, low bounce rate)?",
    "Are there underperforming pages with high traffic but low conversions?",
    "How do blog posts or content pages contribute to conversions (e.g., assisted conversions, last-click conversions)?"
]

@app.route('/mcp/tools/weekly_analytics_report', methods=['POST'])
def weekly_analytics_report():
    """
    Endpoint to run the weekly analytics Q&A and post results to Discord.
    Posts a single headline, then each Q&A as a separate message.
    Returns a JSON summary of all Q&A pairs.
    """
    discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not discord_webhook_url:
        return jsonify({"error": "DISCORD_WEBHOOK_URL environment variable not set."}), 500

    if not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY environment variable not set."}), 500

    agent = AnalyticsAgent(openai_api_key=OPENAI_API_KEY)
    results = []

    logger.info("Starting weekly_analytics_report endpoint.")
    start_time = time.time()

    # Post the headline once
    post_to_discord(discord_webhook_url, "**Weekly Analytics report on its way...**")

    for i, q in enumerate(QUESTIONS):
        question_start = time.time()
        logger.info(f"Processing question {i+1}/{len(QUESTIONS)}: {q}")
        try:
            answer = agent.answer_question(GA4_PROPERTY_ID, q)
        except Exception as e:
            logger.error(f"Error answering question {i+1}: {e}")
            answer = f"Error: {str(e)}"
        if isinstance(answer, list):
            answer = ', '.join(str(x) for x in answer)
        message = f"**Q: {q}**\nA: {answer}"
        if len(message) > 1990:
            message = message[:1990] + "..."
        post_to_discord(discord_webhook_url, message)
        results.append({"question": q, "answer": answer})
        question_end = time.time()
        logger.info(f"Time taken for question {i+1}: {question_end - question_start:.2f} seconds")
        time.sleep(2)

    end_time = time.time()
    logger.info(f"weekly_analytics_report endpoint completed in {end_time - start_time:.2f} seconds.")

    # Validate that we have results before generating summary
    if not results:
        logger.warning("No results generated, posting error message to Discord")
        post_to_discord(discord_webhook_url, "**Error:** No analytics data could be processed. Please check the logs for details.")
        return jsonify({"status": "error", "message": "No results generated", "results": []}), 500

    # Check if we have any valid answers (not just errors)
    valid_answers = [r for r in results if not r['answer'].startswith('Error:')]
    if not valid_answers:
        logger.warning("All answers contain errors, posting error message to Discord")
        post_to_discord(discord_webhook_url, "**Error:** All analytics questions failed to process. Please check the logs for details.")
        return jsonify({"status": "error", "message": "All questions failed", "results": results}), 500

    # Summarize recommendations using OpenAI
    all_answers_text = "\n\n".join(
        f"Q: {item['question']}\nA: {item['answer']}" for item in valid_answers
    )
    
    logger.info(f"Generating summary from {len(valid_answers)} Q&A pairs")
    logger.info(f"Total answers text length: {len(all_answers_text)} characters")
    
    summary_prompt = (
        "You are an expert web analytics consultant. Based on the following analytics Q&A data, "
        "provide 3-5 specific, actionable recommendations to improve website performance and conversions. "
        "Focus on the most important insights from the data. "
        "Format your response as a numbered list of recommendations, starting with the most critical ones.\n\n"
        "Analytics Q&A Data:\n"
        f"{all_answers_text}\n\n"
        "Please provide your recommendations now:"
    )
    
    try:
        logger.info("Calling OpenAI for summary generation...")
        logger.info(f"Prompt length: {len(summary_prompt)} characters")
        
        # Try a simpler, more direct approach
        simple_prompt = (
            "Based on this analytics data, provide 3-5 actionable recommendations to improve website performance. "
            "Keep your response concise and under 1500 characters. Focus only on the most critical insights:\n\n"
            f"{all_answers_text}\n\n"
            "Recommendations:"
        )
        
        logger.info(f"Using simplified prompt. Length: {len(simple_prompt)} characters")
        
        summary_response = agent.openai_client.chat.completions.create(
            model="o4-mini",
            max_completion_tokens=1000,
            messages=[
                {"role": "system", "content": "You are a web analytics expert. Provide clear, actionable recommendations. Keep responses concise and under 1500 characters to fit Discord's limits. Focus on the 3-5 most critical recommendations only. Never return empty responses."},
                {"role": "user", "content": simple_prompt}
            ]
        )
        
        summary = summary_response.choices[0].message.content
        logger.info(f"Summary generated successfully. Length: {len(summary)} characters")
        logger.info(f"Summary content: {summary[:200]}...")
        logger.info(f"Full summary: {summary}")
        
        # Check for empty or invalid summary
        if not summary or summary.strip() == "" or len(summary.strip()) < 10:
            logger.warning("Generated summary is empty or too short, trying fallback model...")
            
            # Try with a different model as fallback
            try:
                fallback_response = agent.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    max_tokens=500,
                    messages=[
                        {"role": "system", "content": "You are a web analytics expert. Provide 3-5 actionable recommendations. Keep responses concise and under 1500 characters to fit Discord's limits. Focus on the most critical insights only."},
                        {"role": "user", "content": f"Based on this analytics data, provide recommendations:\n\n{all_answers_text}"}
                    ]
                )
                fallback_summary = fallback_response.choices[0].message.content
                if fallback_summary and len(fallback_summary.strip()) > 10:
                    logger.info("Fallback model generated summary successfully")
                    summary = fallback_summary
                else:
                    logger.warning("Fallback model also returned empty response, using hardcoded summary")
                    summary = (
                        "**Key Recommendations Based on Analytics Data:**\n\n"
                        "1. **Review Traffic Sources**: Analyze which channels are driving the most valuable traffic and optimize accordingly.\n"
                        "2. **Improve Landing Pages**: Focus on pages with high bounce rates to enhance user engagement.\n"
                        "3. **Optimize Conversion Funnel**: Identify and fix pages with high exit rates in the conversion process.\n"
                        "4. **Content Performance**: Review which content types (blog posts, product pages) drive the most conversions.\n"
                        "5. **User Experience**: Monitor session duration and pages per session to improve overall engagement."
                    )
            except Exception as fallback_error:
                logger.error(f"Fallback model also failed: {fallback_error}")
                summary = (
                    "**Key Recommendations Based on Analytics Data:**\n\n"
                    "1. **Review Traffic Sources**: Analyze which channels are driving the most valuable traffic and optimize accordingly.\n"
                    "2. **Improve Landing Pages**: Focus on pages with high bounce rates to enhance user engagement.\n"
                    "3. **Optimize Conversion Funnel**: Identify and fix pages with high exit rates in the conversion process.\n"
                    "4. **Content Performance**: Review which content types (blog posts, product pages) drive the most conversions.\n"
                    "5. **User Experience**: Monitor session duration and pages per session to improve overall engagement."
                )

    except Exception as e:
        logger.error(f"Error generating summary recommendations: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        summary = (
            "**Key Recommendations Based on Analytics Data:**\n\n"
            "1. **Review Traffic Sources**: Analyze which channels are driving the most valuable traffic and optimize accordingly.\n"
            "2. **Improve Landing Pages**: Focus on pages with high bounce rates to enhance user engagement.\n"
            "3. **Optimize Conversion Funnel**: Identify and fix pages with high exit rates in the conversion process.\n"
            "4. **Content Performance**: Review which content types (blog posts, product pages) drive the most conversions.\n"
            "5. **User Experience**: Monitor session duration and pages per session to improve overall engagement."
        )

    # Post summary to Discord with better formatting
    summary_message = f"**Summary Recommendations:**\n{summary}"
    logger.info(f"Posting summary to Discord. Message length: {len(summary_message)} characters")
    
    try:
        post_to_discord(discord_webhook_url, summary_message)
        logger.info("Summary posted to Discord successfully")
    except Exception as e:
        logger.error(f"Failed to post summary to Discord: {e}")

    return jsonify({"status": "success", "results": results, "summary": summary})

def post_to_discord(webhook_url, message):
    """Post a message to Discord webhook with improved error handling and logging."""
    try:
        logger.info(f"Posting to Discord webhook. Message length: {len(message)} characters")
        
        # Ensure message is not too long for Discord (limit is 2000 characters)
        if len(message) > 1990:
            logger.warning(f"Message too long ({len(message)} chars), truncating to 1990 characters")
            message = message[:1990] + "..."
        
        data = {"content": message}
        response = requests.post(webhook_url, json=data, timeout=10)
        
        logger.info(f"Discord webhook response status: {response.status_code}")
        
        if response.status_code in (200, 204):
            logger.info("Message posted to Discord successfully")
            return True
        else:
            logger.error(f"Failed to post to Discord: {response.status_code} {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        logger.error("Discord webhook request timed out")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Discord webhook request failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error posting to Discord: {e}")
        return False

@app.route('/openapi.json', methods=['GET'])
def openapi_spec():
    """
    Serves the OpenAPI specification for the API.
    """
    return send_file('openapi.json', mimetype='application/json')

if __name__ == '__main__':
    app.run(debug=True, port=8080)
