"""
Utility functions for Google Analytics data processing and formatting.

This module contains pure utility functions for:
- Data conversion and processing
- Date range calculations
- Basic analytics analysis
- Response formatting helpers
"""

from datetime import datetime, timedelta
import time
import json
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

def convert_metric_name(metric_name: str) -> str:
    """Convert common metric names from snake_case to camelCase for Google Analytics API."""
    metric_mappings = {
        'active_users': 'activeUsers',
        'total_users': 'totalUsers',
        'new_users': 'newUsers',
        'sessions': 'sessions',
        'page_views': 'screenPageViews',
        'bounce_rate': 'bounceRate',
        'session_duration': 'averageSessionDuration',
        'pages_per_session': 'screenPageViewsPerSession',
        'conversions': 'conversions',
        'revenue': 'totalRevenue',
        'transactions': 'transactions',
        'ecommerce_purchases': 'ecommercePurchases'
    }
    
    return metric_mappings.get(metric_name.lower(), metric_name)

def convert_dimension_name(dimension_name: str) -> str:
    """Convert common dimension names from snake_case to camelCase for Google Analytics API."""
    dimension_mappings = {
        'device_category': 'deviceCategory',
        'country': 'country',
        'city': 'city',
        'page_path': 'pagePath',
        'page_title': 'pageTitle',
        'landing_page': 'landingPage',
        'session_default_channel_group': 'sessionDefaultChannelGroup',
        'source': 'source',
        'medium': 'medium',
        'campaign': 'campaignName',
        'hostname': 'hostName',
        'browser': 'browser',
        'operating_system': 'operatingSystem',
        'content_group': 'contentGroup',
    }
    # Try mapping, else convert snake_case to camelCase
    if dimension_name in dimension_mappings:
        return dimension_mappings[dimension_name]
    if '_' in dimension_name:
        parts = dimension_name.split('_')
        return parts[0] + ''.join(word.capitalize() for word in parts[1:])
    return dimension_name

def get_date_range_strings(num_days: int) -> tuple[str, str]:
    """Get start and end date strings for a given number of days."""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=num_days)).strftime("%Y-%m-%d")
    return start_date, end_date

def get_default_date_range() -> Dict[str, str]:
    """
    Get default date range for the last 30 days.
    
    Returns:
        Dict with 'start_date' and 'end_date' in YYYY-MM-DD format
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    return {
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d")
    }

def get_consistent_date_range() -> Dict[str, str]:
    """
    Get a consistent date range for reproducible results.
    
    Uses a fixed 30-day period ending yesterday for consistency
    across different runs and time zones.
    
    Returns:
        Dict with 'start_date' and 'end_date' in YYYY-MM-DD format
    """
    # Use a fixed 30-day period ending yesterday for consistency
    end_date = datetime.now() - timedelta(days=1)  # Yesterday
    start_date = end_date - timedelta(days=30)     # 30 days before yesterday
    return {
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d")
    }

def convert_ga4_response_to_dict(response: Any) -> Dict[str, Any]:
    """
    Convert GA4 API response to a JSON-serializable dictionary.
    
    This function takes the raw GA4 API response object and converts it
    into a standard Python dictionary that can be easily serialized to JSON
    and processed by other functions.
    
    Args:
        response: Raw GA4 API response object
        
    Returns:
        Dict containing:
        - dimension_headers: List of dimension names
        - metric_headers: List of metric names  
        - rows: List of row data with dimension_values and metric_values
    """
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

def process_ga_response(response):
    """Process GA4 API response into a more usable format."""
    processed_data = []
    dimension_names = [header.name for header in response.dimension_headers]
    metric_names = [header.name for header in response.metric_headers]
    for row in response.rows:
        row_data = {dim_name: dim_val.value for dim_name, dim_val in zip(dimension_names, row.dimension_values)}
        row_data.update({met_name: met_val.value for met_name, met_val in zip(metric_names, row.metric_values)})
        processed_data.append(row_data)
    return processed_data

def calculate_session_share(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate the percentage share of sessions for each row in the data.
    
    This function processes GA4 data to add a 'session_share' field to each row,
    representing what percentage of total sessions that row represents.
    
    Args:
        data: GA4 data dictionary with 'rows' containing metric_values
        
    Returns:
        Updated data dictionary with 'session_share' added to each row
        
    Example:
        If you have traffic source data with sessions, this will add
        percentages showing each source's share of total traffic.
    """
    rows = data.get("rows", [])
    if not rows:
        return data
    
    try:
        # Calculate total sessions across all rows
        total_sessions = sum(int(row["metric_values"][0]) for row in rows)
        
        # Calculate percentage share for each row
        for row in rows:
            sessions = int(row["metric_values"][0])
            share = (sessions / total_sessions) * 100 if total_sessions else 0
            row["session_share"] = round(share, 1)
            
    except Exception as e:
        logging.warning(f"Failed to calculate session share: {e}")
    
    return data

def find_high_traffic_low_conversion(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Identify pages with high traffic but low conversions.
    
    This function analyzes GA4 data to flag pages that receive significant
    traffic but have zero conversions, indicating potential optimization opportunities.
    
    Args:
        data: GA4 data dictionary with 'rows' containing metric_values
              (first metric should be sessions/traffic, second should be conversions)
        
    Returns:
        Updated data dictionary with 'underperforming' flag added to each row
        
    Example:
        Useful for identifying landing pages or product pages that need
        conversion optimization despite high traffic.
    """
    rows = data.get("rows", [])
    if not rows:
        return data
    
    try:
        # Extract sessions and conversions from each row
        sessions = [int(row["metric_values"][0]) for row in rows]
        conversions = [int(row["metric_values"][1]) for row in rows]
        
        # Calculate average sessions to determine "high traffic" threshold
        avg_sessions = sum(sessions) / len(sessions) if sessions else 0
        
        # Flag rows with high traffic but no conversions
        for i, row in enumerate(rows):
            if sessions[i] > avg_sessions and conversions[i] == 0:
                row["underperforming"] = True
            else:
                row["underperforming"] = False
                
    except Exception as e:
        logging.warning(f"Failed to flag underperforming pages: {e}")
    
    return data

def generate_basic_analysis(data: dict, question: str) -> str:
    """
    Generate a basic human-readable analysis when OpenAI fails.
    
    This is a fallback function that provides basic insights from GA4 data
    without requiring OpenAI API access. It focuses on key metrics and
    identifies patterns like high-traffic, low-conversion pages.
    
    Args:
        data: GA4 data dictionary with 'rows' containing analytics data
        question: Original question that was asked
        
    Returns:
        Human-readable analysis string with key insights and recommendations
    """
    rows = data.get("rows", [])
    if not rows:
        return "No data available for analysis."
    
    # Log the data structure for debugging
    logging.info(f"Analyzing {len(rows)} rows for question: {question}")
    if rows:
        logging.info(f"Sample row structure: {rows[0]}")
    
    # Extract key metrics more robustly
    sessions = []
    conversions = []
    page_names = []
    
    for row in rows:
        metric_values = row.get("metric_values", [])
        dimension_values = row.get("dimension_values", [])
        
        # Try to extract metrics based on available data
        if len(metric_values) >= 1:
            try:
                # First metric could be sessions, page views, or conversions
                first_metric = int(metric_values[0])
                sessions.append(first_metric)
                
                if len(metric_values) >= 2:
                    second_metric = int(metric_values[1])
                    conversions.append(second_metric)
                else:
                    conversions.append(0)
                
                # Try to get page name from dimensions
                if dimension_values:
                    page_name = "Unknown"
                    for i, dim in enumerate(dimension_values):
                        if dim and dim != "(not set)":
                            page_name = dim
                            break
                    page_names.append(page_name)
                else:
                    page_names.append("Unknown")
                    
            except (ValueError, IndexError) as e:
                logging.warning(f"Failed to parse metrics from row: {e}")
                continue
    
    if not sessions:
        return f"Unable to analyze the data due to format issues. Question: {question}. Available data: {data.get('metric_headers', [])} metrics, {data.get('dimension_headers', [])} dimensions."
    
    # Calculate key insights
    total_sessions = sum(sessions)
    total_conversions = sum(conversions)
    avg_sessions = total_sessions / len(sessions) if sessions else 0
    
    # Find pages with high traffic but low conversions
    high_traffic_low_conversion = []
    for i, row in enumerate(rows):
        if i < len(sessions) and i < len(conversions) and i < len(page_names):
            try:
                session_count = sessions[i]
                conversion_count = conversions[i]
                page_name = page_names[i]
                
                if session_count > avg_sessions and conversion_count == 0:
                    high_traffic_low_conversion.append((page_name, session_count))
            except (ValueError, IndexError):
                continue
    
    # Generate comprehensive analysis
    analysis = f"Based on the analytics data for '{question}':\n\n"
    analysis += f"• **Total Records Analyzed**: {len(rows)}\n"
    analysis += f"• **Total Sessions**: {total_sessions:,}\n"
    analysis += f"• **Total Conversions**: {total_conversions:,}\n"
    analysis += f"• **Average Sessions per Record**: {avg_sessions:.1f}\n\n"
    
    # Add metric headers info for debugging
    metric_headers = data.get("metric_headers", [])
    dimension_headers = data.get("dimension_headers", [])
    analysis += f"**Data Structure**: {len(metric_headers)} metrics ({', '.join(metric_headers)}) and {len(dimension_headers)} dimensions ({', '.join(dimension_headers)})\n\n"
    
    if high_traffic_low_conversion:
        analysis += "**Records with High Traffic but No Conversions:**\n"
        for page_name, session_count in sorted(high_traffic_low_conversion, key=lambda x: x[1], reverse=True):
            analysis += f"• {page_name}: {session_count:,} sessions, 0 conversions\n"
    else:
        analysis += "**Good News**: No records with high traffic and zero conversions were found.\n"
    
    return analysis

def get_trend_analysis(property_id, metrics, dimensions, time_frames=None, client=None):
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
            from .endpoints import get_ga_report_with_cache
            response = get_ga_report_with_cache(property_id, tf["start_date"], tf["end_date"], metrics, dimensions, comparison_start_date=tf.get("comparison_start_date"), comparison_end_date=tf.get("comparison_end_date"))
            raw_trend_data[tf["name"]] = process_ga_response(response)
        except Exception as e:
            logger.error(f"Error getting trend data for {tf['name']}: {e}")
            raw_trend_data[tf["name"]] = []
    
    return raw_trend_data

def format_trend_data_for_humans(raw_trend_data, time_frames):
    """Format trend data in a more human-readable way."""
    formatted_trends = {}
    
    for time_frame, data in raw_trend_data.items():
        # Find the corresponding time frame config
        tf_config = next((tf for tf in time_frames if tf["name"] == time_frame), {})
        
        # Group data by period (current vs previous)
        current_period = []
        previous_period = []
        
        for row in data:
            if row.get('dateRange') == 'current_period':
                current_period.append(row)
            elif row.get('dateRange') == 'previous_period':
                previous_period.append(row)
        
        # Calculate summary statistics
        current_total = sum(int(row.get('activeUsers', 0)) for row in current_period)
        previous_total = sum(int(row.get('activeUsers', 0)) for row in previous_period)
        
        # Calculate percentage change
        percentage_change = 0
        if previous_total > 0:
            percentage_change = ((current_total - previous_total) / previous_total) * 100
        
        # Format top performers with better structure
        top_performers = []
        for row in data[:5]:  # Top 5 overall
            top_performers.append({
                "country": row.get('country', 'Unknown'),
                "active_users": int(row.get('activeUsers', 0)),
                "period": row.get('dateRange', 'unknown')
            })
        
        formatted_trends[time_frame] = {
            "period": time_frame,
            "data_points": len(data),
            "summary": {
                "current_period_total": current_total,
                "previous_period_total": previous_total,
                "percentage_change": round(percentage_change, 1),
                "trend_direction": "up" if percentage_change > 0 else "down" if percentage_change < 0 else "stable",
                "date_range": {
                    "current_start": tf_config.get("start_date"),
                    "current_end": tf_config.get("end_date"),
                    "comparison_start": tf_config.get("comparison_start_date"),
                    "comparison_end": tf_config.get("comparison_end_date")
                }
            },
            "top_performers": top_performers,
            "insights": {
                "total_countries": len(set(row.get('country') for row in data if row.get('country') != '(not set)')),
                "has_growth": percentage_change > 0,
                "growth_rate": f"{percentage_change:+.1f}%" if percentage_change != 0 else "0%"
            }
        }
    
    return formatted_trends 