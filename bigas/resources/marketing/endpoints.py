from flask import Blueprint, jsonify, request, send_file
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
    Filter,
    FilterExpression,
    OrderBy,
)
import os
import openai
from datetime import datetime, timedelta
import time
import logging
import json
import traceback
from bigas.resources.marketing.service import MarketingAnalyticsService
from bigas.resources.marketing.utils import (
    convert_metric_name,
    convert_dimension_name,
    format_trend_data_for_humans,
    process_ga_response,
    get_date_range_strings,
    get_trend_analysis
)
import requests

logger = logging.getLogger(__name__)

marketing_bp = Blueprint('marketing_bp', __name__)

# This logic is moved from the original app file to be specific to this blueprint.
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID")
GA4_API_PROPERTY_ID = f"properties/{GA4_PROPERTY_ID}" if GA4_PROPERTY_ID else None
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

try:
    client = BetaAnalyticsDataClient()
except Exception as e:
    logger.error(f"Failed to initialize Google Analytics Data API client: {e}")
    client = None

CACHE_DURATION = 3600
analytics_cache = {}

def get_cached_data(cache_key):
    if cache_key in analytics_cache:
        timestamp, data = analytics_cache[cache_key]
        if time.time() - timestamp < CACHE_DURATION:
            return data
    return None

def set_cached_data(cache_key, data):
    analytics_cache[cache_key] = (time.time(), data)

def get_ga_report(property_id, start_date, end_date, metrics, dimensions, dimension_filters=None, order_bys=None, limit=None, comparison_start_date=None, comparison_end_date=None):
    if not client:
        raise RuntimeError("Google Analytics Data API client is not initialized.")
    property_id_api_format = f"properties/{property_id}" if not property_id.startswith("properties/") else property_id
    date_ranges = [DateRange(start_date=start_date, end_date=end_date, name="current_period")]
    if comparison_start_date and comparison_end_date:
        date_ranges.append(DateRange(start_date=comparison_start_date, end_date=comparison_end_date, name="previous_period"))
    filter_expression = FilterExpression(and_group=FilterExpression.AndGroup(expressions=dimension_filters)) if dimension_filters else None
    converted_metrics = [Metric(name=m) for m in metrics]
    converted_dimensions = [Dimension(name=d) for d in dimensions]
    request_obj = RunReportRequest(property=property_id_api_format, date_ranges=date_ranges, metrics=converted_metrics, dimensions=converted_dimensions, dimension_filter=filter_expression, order_bys=list(order_bys) if order_bys else None, limit=limit)
    response = client.run_report(request_obj, timeout=20)
    return response

def get_ga_report_with_cache(property_id, start_date, end_date, metrics, dimensions, dimension_filters=None, order_bys=None, limit=None, comparison_start_date=None, comparison_end_date=None):
    cache_key_parts = [property_id, start_date, end_date, tuple(metrics), tuple(dimensions)]
    if dimension_filters: cache_key_parts.append(json.dumps(str(dimension_filters), sort_keys=True))
    if order_bys: cache_key_parts.append(json.dumps(str(order_bys), sort_keys=True))
    if limit is not None: cache_key_parts.append(str(limit))
    if comparison_start_date: cache_key_parts.append(comparison_start_date)
    if comparison_end_date: cache_key_parts.append(comparison_end_date)
    cache_key = "_".join([str(part) for part in cache_key_parts])
    cached_data = get_cached_data(cache_key)
    if cached_data: return cached_data
    try:
        response = get_ga_report(property_id, start_date, end_date, metrics, dimensions, dimension_filters, order_bys, limit, comparison_start_date, comparison_end_date)
        set_cached_data(cache_key, response)
        return response
    except Exception as e:
        logger.error(f"Error in get_ga_report_with_cache: {str(e)}")
        raise

@marketing_bp.route('/mcp/tools/fetch_analytics_report', methods=['POST'])
def fetch_analytics_report():
    data = request.json
    start_date, end_date = get_date_range_strings(30)
    start_date = data.get('start_date', start_date)
    end_date = data.get('end_date', end_date)
    metrics = data.get('metrics', ['activeUsers'])
    dimensions = data.get('dimensions', ['country'])
    try:
        ga_response = get_ga_report_with_cache(GA4_PROPERTY_ID, start_date, end_date, metrics, dimensions)
        processed_data = process_ga_response(ga_response)
        return jsonify({"status": "success", "data": processed_data})
    except Exception as e:
        logger.error(f"Error in fetch_analytics_report: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@marketing_bp.route('/mcp/tools/fetch_custom_report', methods=['POST'])
def fetch_custom_report():
    data = request.json
    try:
        # Normalize dimensions and metrics
        dimensions = [convert_dimension_name(d) for d in data['dimensions']]
        metrics = [convert_metric_name(m) for m in data['metrics']]
        date_ranges = data['date_ranges']
        all_processed_data = {}
        for dr in date_ranges:
            ga_response = get_ga_report_with_cache(GA4_PROPERTY_ID, dr['start_date'], dr['end_date'], metrics, dimensions)
            processed_data = process_ga_response(ga_response)
            all_processed_data[dr.get('name', f"{dr['start_date']}_to_{dr['end_date']}")] = processed_data
        return jsonify({"status": "success", "data": all_processed_data})
    except KeyError as e:
        return jsonify({"error": f"Missing required field: {e}"}), 400
    except Exception as e:
        logger.error(f"Error in fetch_custom_report: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@marketing_bp.route('/mcp/tools/ask_analytics_question', methods=['POST'])
def ask_analytics_question():
    data = request.json
    question = data.get('question')
    if not question: return jsonify({"error": "Question not provided"}), 400
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        answer = service.answer_question(GA4_PROPERTY_ID, question)
        return jsonify({"answer": answer})
    except Exception as e:
        logger.error(f"Error in ask_analytics_question: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@marketing_bp.route('/mcp/tools/analyze_trends', methods=['POST'])
def analyze_trends():
    data = request.json or {}
    
    # Handle different parameter formats
    if 'metric' in data:
        # Single metric format - convert to proper format
        metric_name = convert_metric_name(data['metric'])
        metrics = [metric_name]
    else:
        # Handle array of metrics
        raw_metrics = data.get('metrics', ['activeUsers'])
        metrics = [convert_metric_name(m) for m in raw_metrics]
    
    dimensions = data.get('dimensions', ['country'])
    
    # Handle date range parameter
    date_range = data.get('date_range', 'last_30_days')
    
    try:
        # Define time frames based on date_range parameter
        today = datetime.now()
        if date_range == 'last_7_days':
            time_frames = [
                {
                    "name": "last_7_days", 
                    "start_date": (today - timedelta(days=7)).strftime("%Y-%m-%d"), 
                    "end_date": today.strftime("%Y-%m-%d"), 
                    "comparison_start_date": (today - timedelta(days=14)).strftime("%Y-%m-%d"), 
                    "comparison_end_date": (today - timedelta(days=8)).strftime("%Y-%m-%d")
                }
            ]
        elif date_range == 'last_30_days':
            time_frames = [
                {
                    "name": "last_30_days", 
                    "start_date": (today - timedelta(days=30)).strftime("%Y-%m-%d"), 
                    "end_date": today.strftime("%Y-%m-%d"), 
                    "comparison_start_date": (today - timedelta(days=60)).strftime("%Y-%m-%d"), 
                    "comparison_end_date": (today - timedelta(days=31)).strftime("%Y-%m-%d")
                }
            ]
        else:
            # Default to both time frames
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
                }
            ]
        
        # Get actual trend data
        raw_trend_data = get_trend_analysis(GA4_PROPERTY_ID, metrics, dimensions, time_frames)
        
        # Format the trend data for better presentation
        formatted_trends = format_trend_data_for_humans(raw_trend_data, time_frames)
        
        return jsonify({
            "status": "success", 
            "data": formatted_trends,
            "metadata": {
                "metrics_analyzed": metrics,
                "dimensions_analyzed": dimensions,
                "date_range_requested": date_range,
                "time_frames_analyzed": list(raw_trend_data.keys())
            }
        })
    except Exception as e:
        logger.error(f"Error in analyze_trends: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

def get_manifest():
    """Returns the manifest for the marketing tools."""
    return {
        "name": "Marketing Tools",
        "description": "Tools for Google Analytics reporting, natural language queries, and trend analysis.",
        "tools": [
            {"name": "fetch_analytics_report", "description": "Fetches a standard Google Analytics report.", "path": "/mcp/tools/fetch_analytics_report", "method": "POST"},
            {"name": "fetch_custom_report", "description": "Fetches a custom Google Analytics report with specific dimensions and metrics.", "path": "/mcp/tools/fetch_custom_report", "method": "POST"},
            {"name": "ask_analytics_question", "description": "Asks a question about your Google Analytics data in natural language.", "path": "/mcp/tools/ask_analytics_question", "method": "POST"},
            {"name": "analyze_trends", "description": "Analyzes trends in your Google Analytics data over time.", "path": "/mcp/tools/analyze_trends", "method": "POST"},
            {"name": "weekly_analytics_report", "description": "Generates a comprehensive weekly analytics report and posts it to Discord.", "path": "/mcp/tools/weekly_analytics_report", "method": "POST"},
        ]
    }

@marketing_bp.route('/mcp/tools/weekly_analytics_report', methods=['POST'])
def weekly_analytics_report():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return jsonify({"error": "DISCORD_WEBHOOK_URL not set."}), 500
    
    post_to_discord(webhook_url, "# 📊 Weekly Analytics Report on its way...")
    
    questions = [
      "What are the primary traffic sources (e.g., organic search, direct, referral, paid search, social, email) contributing to total sessions, and what is their respective share?",
      "What is the average session duration and pages per session across all users?",
      "Which pages are the most visited, and how do they contribute to conversions (e.g., product pages, category pages, blog posts)?",
      "Which pages or sections (e.g., blog, product pages, landing pages) drive the most engagement (e.g., time on page, low bounce rate)?",
      "Are there underperforming pages with high traffic but low conversions?",
      "How do blog posts or content pages contribute to conversions (e.g., assisted conversions, last-click conversions)?"
    ]
    
    # Map questions to template keys for robust, deterministic analytics
    question_to_template = {
        questions[0]: "traffic_sources",
        questions[1]: "session_quality", 
        questions[2]: "top_pages_conversions",
        questions[3]: "engagement_pages",
        questions[4]: "underperforming_pages",
        questions[5]: "blog_conversion"
    }
    
    service = MarketingAnalyticsService(OPENAI_API_KEY)
    full_report = ""

    for q in questions:
        try:
            template_key = question_to_template[q]
            # Use template-driven approach for reliability
            if template_key == "traffic_sources":
                answer = service.answer_traffic_sources()
            else:
                # For other templates, use the generic template runner with OpenAI summarization
                data = service.run_template_query(template_key)
                answer = service._format_response_obj(data, q)
            
            message = f"**Q: {q}**\nA: {answer}"
            post_to_discord(webhook_url, message)
            full_report += message + "\n\n"
        except Exception as e:
            error_message = f"Could not answer question '{q}': {e}"
            logger.error(error_message)
            post_to_discord(webhook_url, error_message)

    try:
        summary_prompt = "Please summarize the following Q&A session about website analytics into a short, high-level executive summary with 5-7 key, actionable recommendations. Focus on the most critical insights a solo founder should act on this week:\n\n" + full_report
        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        summary_response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=500,
            temperature=0.7
        )
        summary = summary_response.choices[0].message.content.strip()
        post_to_discord(webhook_url, f"Summary Recommendations:\n{summary}")
    except Exception as e:
        logger.error(f"Could not generate weekly summary: {e}")
        post_to_discord(webhook_url, "Could not generate weekly summary.")

    return jsonify({"status": "Weekly report process initiated and sent to Discord."})

def post_to_discord(webhook_url, message):
    if len(message) > 2000:
        message = message[:1997] + "..."
    data = {"content": message}
    response = requests.post(webhook_url, json=data)
    if response.status_code != 204:
        logger.error(f"Failed to post to Discord: {response.status_code}, {response.text}")

@marketing_bp.route('/openapi.json', methods=['GET'])
def openapi_spec():
    # This should also be dynamically generated in a mature version
    try:
        return send_file('openapi.json', mimetype='application/json')
    except FileNotFoundError:
        return jsonify({"error": "openapi.json not found."}), 404
