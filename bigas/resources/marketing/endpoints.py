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
    process_ga_response,
    get_date_range_strings,
    validate_date_range,
    validate_ga4_metrics_dimensions,
    sanitize_error_message,
    validate_request_data
)
import requests
from bs4 import BeautifulSoup
import re
from typing import Dict, Any

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

# Simple rate limiting storage
request_counts = {}
RATE_LIMIT_WINDOW = 3600  # 1 hour
RATE_LIMIT_MAX_REQUESTS = 100  # Max requests per hour per endpoint

def check_rate_limit(endpoint: str, client_ip: str = "default") -> bool:
    """
    Simple rate limiting check.
    
    Args:
        endpoint: The endpoint being accessed
        client_ip: Client IP address (default for simplicity)
        
    Returns:
        True if request is allowed, False if rate limited
    """
    current_time = time.time()
    key = f"{endpoint}:{client_ip}"
    
    # Clean old entries
    if key in request_counts:
        if current_time - request_counts[key]["timestamp"] > RATE_LIMIT_WINDOW:
            del request_counts[key]
    
    # Check rate limit
    if key in request_counts:
        if request_counts[key]["count"] >= RATE_LIMIT_MAX_REQUESTS:
            return False
        request_counts[key]["count"] += 1
    else:
        request_counts[key] = {
            "count": 1,
            "timestamp": current_time
        }
    
    return True

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
    # Rate limiting check
    if not check_rate_limit("fetch_analytics_report"):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    
    # Validate request data
    data = request.json
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    start_date, end_date = get_date_range_strings(30)
    start_date = data.get('start_date', start_date)
    end_date = data.get('end_date', end_date)
    metrics = data.get('metrics', ['activeUsers'])
    dimensions = data.get('dimensions', ['country'])
    
    # Validate date range
    is_valid, error_msg = validate_date_range(start_date, end_date)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    # Validate metrics and dimensions
    is_valid, error_msg = validate_ga4_metrics_dimensions(metrics, dimensions)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    try:
        ga_response = get_ga_report_with_cache(GA4_PROPERTY_ID, start_date, end_date, metrics, dimensions)
        processed_data = process_ga_response(ga_response)
        return jsonify({"status": "success", "data": processed_data})
    except Exception as e:
        logger.error(f"Error in fetch_analytics_report: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500

@marketing_bp.route('/mcp/tools/fetch_custom_report', methods=['POST'])
def fetch_custom_report():
    # Rate limiting check
    if not check_rate_limit("fetch_custom_report"):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    
    # Validate request data
    data = request.json
    is_valid, error_msg = validate_request_data(data, required_fields=['dimensions', 'metrics', 'date_ranges'])
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    try:
        # Normalize dimensions and metrics
        dimensions = [convert_dimension_name(d) for d in data['dimensions']]
        metrics = [convert_metric_name(m) for m in data['metrics']]
        date_ranges = data['date_ranges']
        
        # Validate metrics and dimensions
        is_valid, error_msg = validate_ga4_metrics_dimensions(metrics, dimensions)
        if not is_valid:
            return jsonify({"error": error_msg}), 400
        
        # Validate date ranges
        for dr in date_ranges:
            is_valid, error_msg = validate_date_range(dr['start_date'], dr['end_date'])
            if not is_valid:
                return jsonify({"error": f"Invalid date range: {error_msg}"}), 400
        
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
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500

@marketing_bp.route('/mcp/tools/ask_analytics_question', methods=['POST'])
def ask_analytics_question():
    # Rate limiting check
    if not check_rate_limit("ask_analytics_question"):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    
    # Validate request data
    data = request.json
    is_valid, error_msg = validate_request_data(data, required_fields=['question'])
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    question = data.get('question')
    if not question: 
        return jsonify({"error": "Question not provided"}), 400
    
    # Validate question length
    if len(question) > 500:
        return jsonify({"error": "Question too long (max 500 characters)"}), 400
    
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        answer = service.answer_question(GA4_PROPERTY_ID, question)
        return jsonify({"answer": answer})
    except Exception as e:
        logger.error(f"Error in ask_analytics_question: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500

@marketing_bp.route('/mcp/tools/analyze_trends', methods=['POST'])
def analyze_trends():
    # Rate limiting check
    if not check_rate_limit("analyze_trends"):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    
    # Validate request data
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
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
    
    # Validate metrics and dimensions
    is_valid, error_msg = validate_ga4_metrics_dimensions(metrics, dimensions)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
    
    # Handle date range parameter
    date_range = data.get('date_range', 'last_30_days')
    
    # Validate date range parameter
    valid_date_ranges = ['last_7_days', 'last_30_days']
    if date_range not in valid_date_ranges:
        return jsonify({"error": f"Invalid date_range. Must be one of: {', '.join(valid_date_ranges)}"}), 400
    
    # Check if Discord webhook is available
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    post_to_discord_enabled = webhook_url is not None
    
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
        
        # Get actual trend data using the service
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        result = service.trend_analysis_service.analyze_trends_with_insights(
            GA4_PROPERTY_ID, metrics, dimensions, date_range
        )
        
        # The result already contains formatted data
        formatted_trends = result["data"]
        
        # Post to Discord if webhook is available
        if post_to_discord_enabled:
            # Create a summary message for Discord
            discord_message = f"# 📈 Trend Analysis Report\n\n"
            discord_message += f"**Metrics**: {', '.join(metrics)}\n"
            discord_message += f"**Dimensions**: {', '.join(dimensions)}\n"
            discord_message += f"**Date Range**: {date_range}\n\n"
            
            # Add key insights from the data
            for time_frame, data in formatted_trends.items():
                summary = data.get("summary", {})
                discord_message += f"**{time_frame.replace('_', ' ').title()}**:\n"
                discord_message += f"• Current Period: {summary.get('current_period_total', 0):,}\n"
                discord_message += f"• Previous Period: {summary.get('previous_period_total', 0):,}\n"
                discord_message += f"• Change: {summary.get('percentage_change', 0):+.1f}%\n"
                discord_message += f"• Trend: {summary.get('trend_direction', 'stable')}\n\n"
            
            # Post the data summary
            post_to_discord(webhook_url, discord_message)
            
            # Post AI insights separately
            ai_insights_message = f"**🤖 AI Insights**:\n{result['ai_insights']}"
            post_to_discord(webhook_url, ai_insights_message)
        
        return jsonify({
            "status": "success", 
            "data": result["data"],
            "ai_insights": result["ai_insights"],
            "discord_posted": post_to_discord_enabled,
            "metadata": {
                "metrics_analyzed": metrics,
                "dimensions_analyzed": dimensions,
                "date_range_requested": date_range,
                "time_frames_analyzed": list(result["data"].keys())
            }
        })
    except Exception as e:
        logger.error(f"Error in analyze_trends: {traceback.format_exc()}")
        sanitized_error = sanitize_error_message(str(e))
        return jsonify({"error": sanitized_error}), 500

def get_manifest():
    """Returns the manifest for the marketing tools."""
    return {
        "name": "Marketing Tools",
        "description": "Tools for Google Analytics reporting, natural language queries, trend analysis, and report storage.",
        "tools": [
            {"name": "fetch_analytics_report", "description": "Fetches a standard Google Analytics report.", "path": "/mcp/tools/fetch_analytics_report", "method": "POST"},
            {"name": "fetch_custom_report", "description": "Fetches a custom Google Analytics report with specific dimensions and metrics.", "path": "/mcp/tools/fetch_custom_report", "method": "POST"},
            {"name": "ask_analytics_question", "description": "Asks a question about your Google Analytics data in natural language.", "path": "/mcp/tools/ask_analytics_question", "method": "POST"},
            {"name": "analyze_trends", "description": "Analyzes trends in your Google Analytics data over time.", "path": "/mcp/tools/analyze_trends", "method": "POST"},
            {"name": "weekly_analytics_report", "description": "Generates a comprehensive weekly analytics report, posts it to Discord, and stores it for later analysis.", "path": "/mcp/tools/weekly_analytics_report", "method": "POST"},
            {"name": "get_stored_reports", "description": "Retrieves a list of all available stored weekly reports.", "path": "/mcp/tools/get_stored_reports", "method": "GET"},
            {"name": "get_latest_report", "description": "Retrieves the most recent weekly analytics report with summary.", "path": "/mcp/tools/get_latest_report", "method": "GET"},
            {"name": "analyze_underperforming_pages", "description": "Analyzes underperforming pages from stored reports and generates AI-powered improvement suggestions.", "path": "/mcp/tools/analyze_underperforming_pages", "method": "POST"},
            {"name": "cleanup_old_reports", "description": "Cleans up old weekly reports to manage storage costs.", "path": "/mcp/tools/cleanup_old_reports", "method": "POST"},
        ]
    }

@marketing_bp.route('/mcp/tools/weekly_analytics_report', methods=['POST'])
def weekly_analytics_report():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return jsonify({"error": "DISCORD_WEBHOOK_URL not set."}), 500
    
    post_to_discord(webhook_url, "# 📊 Weekly Analytics Report on its way...")
    
    questions = [
      "What are the key trends in our website performance over the last 30 days, including user growth, session patterns, and any significant changes?",
      "What are the primary traffic sources (e.g., organic search, direct, referral, paid search, social, email) contributing to total sessions, and what is their respective share?",
      "What is the average session duration and pages per session across all users?",
      "Which pages are the most visited, and how do they contribute to conversions (e.g., product pages, category pages, blog posts)?",
      "Which pages or sections (e.g., blog, product pages, landing pages) drive the most engagement (e.g., time on page, low bounce rate)?",
      "Are there underperforming pages with high traffic but low conversions?",
      "How do blog posts or content pages contribute to conversions (e.g., assisted conversions, last-click conversions)?"
    ]
    
    # Map questions to template keys for robust, deterministic analytics
    question_to_template = {
        questions[0]: "trend_analysis",  # Special handling for trend analysis
        questions[1]: "traffic_sources",
        questions[2]: "session_quality", 
        questions[3]: "top_pages_conversions",
        questions[4]: "engagement_pages",
        questions[5]: "underperforming_pages",
        questions[6]: "blog_conversion"
    }
    
    service = MarketingAnalyticsService(OPENAI_API_KEY)
    full_report = ""
    report_data = {
        "questions": [],
        "summary": "",
        "generated_at": datetime.now().isoformat(),
        "total_questions": len(questions)
    }

    for q in questions:
        try:
            template_key = question_to_template[q]
            # Use template-driven approach for reliability
            if template_key == "traffic_sources":
                answer = service.answer_traffic_sources()
                raw_data = None  # Traffic sources doesn't need raw data
            elif template_key == "trend_analysis":
                # Special handling for trend analysis using service
                result = service.trend_analysis_service.get_weekly_trend_analysis(GA4_PROPERTY_ID)
                answer = result["ai_insights"]
                raw_data = result.get("data", {})  # Store trend data
            else:
                # For other templates, use the template service with OpenAI summarization
                raw_data = service.template_service.run_template_query(template_key, GA4_PROPERTY_ID)
                answer = service.openai_service.format_response_obj(raw_data, q)
            
            message = f"**Q: {q}**\nA: {answer}"
            post_to_discord(webhook_url, message)
            full_report += message + "\n\n"
            
            # Store question and answer in report data with raw data for URL extraction
            report_data["questions"].append({
                "question": q,
                "answer": answer,
                "template_key": template_key,
                "raw_data": raw_data  # Store raw GA4 data for URL extraction
            })
            
        except Exception as e:
            error_message = f"Could not answer question '{q}': {e}"
            logger.error(error_message)
            post_to_discord(webhook_url, error_message)
            
            # Store error in report data
            report_data["questions"].append({
                "question": q,
                "answer": f"Error: {str(e)}",
                "template_key": question_to_template.get(q, "unknown"),
                "error": True,
                "raw_data": None
            })

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
        
        # Store summary in report data
        report_data["summary"] = summary
        
    except Exception as e:
        logger.error(f"Could not generate weekly summary: {e}")
        post_to_discord(webhook_url, "Could not generate weekly summary.")
        report_data["summary"] = f"Error generating summary: {str(e)}"

    # Store the report in Google Cloud Storage
    try:
        stored_path = service.storage_service.store_weekly_report(report_data)
        logger.info(f"Weekly report stored successfully at: {stored_path}")
    except Exception as e:
        logger.error(f"Failed to store weekly report: {e}")
        # Don't fail the entire request if storage fails

    return jsonify({
        "status": "Weekly report process completed and sent to Discord.",
        "stored": True,
        "storage_path": stored_path if 'stored_path' in locals() else None
    })

@marketing_bp.route('/mcp/tools/get_stored_reports', methods=['GET'])
def get_stored_reports():
    """Retrieve a list of all available stored weekly reports."""
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        reports = service.storage_service.list_available_reports()
        
        return jsonify({
            "status": "success",
            "reports": reports,
            "total_reports": len(reports)
        })
    except Exception as e:
        logger.error(f"Error retrieving stored reports: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@marketing_bp.route('/mcp/tools/get_latest_report', methods=['GET'])
def get_latest_report():
    """Retrieve the most recent weekly analytics report."""
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        report_data = service.storage_service.get_latest_weekly_report()
        
        if not report_data:
            return jsonify({"error": "No weekly reports found"}), 404
        
        # Create a summary of the report
        summary = service.storage_service.get_report_summary(report_data)
        
        return jsonify({
            "status": "success",
            "report": report_data,
            "summary": summary
        })
    except Exception as e:
        logger.error(f"Error retrieving latest report: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@marketing_bp.route('/mcp/tools/analyze_underperforming_pages', methods=['POST'])
def analyze_underperforming_pages():
    """Analyze underperforming pages from stored reports and suggest improvements."""
    data = request.json or {}
    report_date = data.get('report_date')  # Optional: specific date, otherwise uses latest
    max_pages = data.get('max_pages', 3)  # Limit number of pages to analyze to prevent timeouts
    
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        
        # Get the report data
        if report_date:
            report_data = service.storage_service.get_weekly_report_by_date(report_date)
            if not report_data:
                return jsonify({"error": f"No report found for date: {report_date}"}), 404
        else:
            report_data = service.storage_service.get_latest_weekly_report()
            if not report_data:
                return jsonify({"error": "No weekly reports found"}), 404
        
        # Extract underperforming pages information
        underperforming_data = []
        page_urls_data = []
        
        for question_data in report_data.get("report", {}).get("questions", []):
            if "underperforming" in question_data.get("question", "").lower():
                underperforming_data.append({
                    "question": question_data.get("question"),
                    "answer": question_data.get("answer")
                })
                
                # Extract page URLs from the raw data if available
                raw_data = question_data.get("raw_data", {})
                if raw_data:
                    extracted_urls = service.storage_service._extract_page_urls_from_raw_data(raw_data)
                    page_urls_data.extend(extracted_urls)
        
        if not underperforming_data:
            return jsonify({
                "status": "success",
                "message": "No underperforming pages data found in the report",
                "underperforming_pages": [],
                "page_urls": []
            })
        
        # Limit the number of pages to analyze to prevent timeouts
        underperforming_pages = [p for p in page_urls_data if p.get('is_underperforming')][:max_pages]
        
        # Post to Discord if webhook is available
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        discord_messages_sent = 0
        
        if webhook_url:
            # Send header message
            header_message = f"# 🔧 Underperforming Pages Analysis\n\n"
            header_message += f"📅 **Report Date**: {report_data.get('metadata', {}).get('report_date', 'Unknown')}\n"
            header_message += f"📊 **Pages to Analyze**: {len(underperforming_pages)} (limited to {max_pages} for performance)\n\n"
            header_message += "I'll analyze each page and provide specific improvement suggestions..."
            post_to_discord(webhook_url, header_message)
            discord_messages_sent += 1
            
            # If we have page URLs, analyze each underperforming page individually
            if underperforming_pages:
                for page in underperforming_pages:
                    try:
                        # Analyze the actual page content with timeout
                        page_content = analyze_page_content(page.get('page_url', ''))
                        
                        # Create a detailed analysis prompt using the actual page content
                        page_analysis_prompt = f"""
                        Analyze this specific underperforming page based on its actual content and provide concrete, actionable improvement suggestions.

                        Page Details:
                        - URL: {page.get('page_url', 'Unknown')}
                        - Sessions: {page.get('sessions', 0)}
                        - Conversions: {page.get('conversions', 0)}
                        - Conversion Rate: {page.get('conversion_rate', 0):.1%}

                        Page Content Analysis:
                        - Title: {page_content.get('title', 'No title')}
                        - Meta Description: {page_content.get('meta_description', 'No meta description')}
                        - Main Headings: {[h['text'] for h in page_content.get('headings', [])[:5]]}
                        - CTA Buttons Found: {len(page_content.get('cta_buttons', []))}
                        - Forms Found: {len(page_content.get('forms', []))}
                        - Has Contact Info: {page_content.get('has_contact_info', False)}
                        - Has Social Proof: {page_content.get('has_social_proof', False)}
                        - Word Count: {page_content.get('page_structure', {}).get('word_count', 0)}
                        - Main Content Preview: {page_content.get('text_content', '')[:200]}...

                        Based on this actual page content, please provide:
                        1. **Specific Issues Identified**: What specific problems do you see in the actual page content that are causing low conversions?
                        2. **Concrete Action Items**: List 5-7 specific, implementable changes based on the actual page content
                        3. **Priority Level**: High/Medium/Low based on impact and effort
                        4. **Expected Impact**: What specific improvements in metrics can be expected
                        5. **Implementation Tips**: Quick wins vs. longer-term improvements

                        Focus on practical, tangible suggestions based on the actual page content you can see.
                        Be specific about what to add, change, or remove from the page based on what's actually there.
                        """
                        
                        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
                        page_analysis_response = openai_client.chat.completions.create(
                            model="gpt-4",
                            messages=[{"role": "user", "content": page_analysis_prompt}],
                            max_tokens=800,
                            temperature=0.7,
                            timeout=15  # Add timeout for OpenAI call
                        )
                        page_analysis = page_analysis_response.choices[0].message.content.strip()
                        
                        # Create a formatted Discord message for this page
                        page_message = f"## 📄 {page.get('page_url', 'Unknown Page')}\n\n"
                        page_message += f"📊 **Metrics**: {page.get('sessions', 0)} sessions, {page.get('conversions', 0)} conversions ({page.get('conversion_rate', 0):.1%})\n"
                        page_message += f"📝 **Page Title**: {page_content.get('title', 'No title')}\n"
                        page_message += f"🔗 **CTAs Found**: {len(page_content.get('cta_buttons', []))} | Forms: {len(page_content.get('forms', []))}\n\n"
                        page_message += f"🎯 **Analysis & Recommendations**:\n\n{page_analysis}\n\n"
                        page_message += "---\n💡 *This analysis is based on actual page content and conversion data*"
                        
                        post_to_discord(webhook_url, page_message)
                        discord_messages_sent += 1
                        
                        # Add a small delay between messages to avoid rate limiting
                        time.sleep(1)
                        
                    except Exception as e:
                        logger.error(f"Error analyzing page {page.get('page_url')}: {e}")
                        error_message = f"❌ **Error analyzing {page.get('page_url', 'Unknown Page')}**: {str(e)}"
                        post_to_discord(webhook_url, error_message)
                        discord_messages_sent += 1
            else:
                # If no page URLs extracted, send a general analysis
                general_analysis_prompt = f"""
                Based on the following analytics data about underperforming pages, provide specific, actionable improvement suggestions.
                Focus on practical recommendations that a solo founder can implement.

                Underperforming Pages Data:
                {json.dumps(underperforming_data, indent=2)}

                Please provide:
                1. A summary of the main issues identified
                2. Specific improvement suggestions for underperforming pages
                3. Priority ranking of improvements (High/Medium/Low)
                4. Estimated effort level for each improvement (Quick/Easy/Complex)
                5. Expected impact of each improvement
                6. Specific action items

                Format your response as a structured analysis with clear action items.
                """
                
                openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
                general_analysis_response = openai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": general_analysis_prompt}],
                    max_tokens=800,
                    temperature=0.7,
                    timeout=15  # Add timeout for OpenAI call
                )
                general_analysis = general_analysis_response.choices[0].message.content.strip()
                
                general_message = f"## 📊 General Underperforming Pages Analysis\n\n"
                general_message += f"🎯 **Analysis & Recommendations**:\n\n{general_analysis}\n\n"
                general_message += "---\n💡 *This analysis is based on conversion data and best practices*"
                
                post_to_discord(webhook_url, general_message)
                discord_messages_sent += 1
        
        return jsonify({
            "status": "success",
            "report_date": report_data.get("metadata", {}).get("report_date"),
            "underperforming_pages": underperforming_data,
            "page_urls": page_urls_data,
            "pages_analyzed": len(underperforming_pages),
            "max_pages_limit": max_pages,
            "discord_posted": webhook_url is not None,
            "discord_messages_sent": discord_messages_sent
        })
        
    except Exception as e:
        logger.error(f"Error analyzing underperforming pages: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@marketing_bp.route('/mcp/tools/cleanup_old_reports', methods=['POST'])
def cleanup_old_reports():
    """Clean up old weekly reports to manage storage costs."""
    data = request.json or {}
    keep_days = data.get('keep_days', 30)  # Default to keeping 30 days
    max_reports_to_delete = data.get('max_reports_to_delete', 50)  # Limit to prevent timeouts
    
    try:
        service = MarketingAnalyticsService(OPENAI_API_KEY)
        deleted_count = service.storage_service.delete_old_reports(keep_days, max_reports_to_delete)
        
        return jsonify({
            "status": "success",
            "deleted_reports": deleted_count,
            "keep_days": keep_days,
            "max_reports_to_delete": max_reports_to_delete,
            "message": f"Cleaned up {deleted_count} old reports, keeping reports from the last {keep_days} days"
        })
    except Exception as e:
        logger.error(f"Error cleaning up old reports: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

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

def analyze_page_content(page_url: str) -> Dict[str, Any]:
    """
    Scrape and analyze a web page for conversion optimization opportunities.
    
    Args:
        page_url: The URL of the page to analyze
        
    Returns:
        Dictionary containing page analysis data
    """
    try:
        # Validate URL format
        if not page_url.startswith(('http://', 'https://')):
            return {
                'url': page_url,
                'error': 'Invalid URL format. Must start with http:// or https://',
                'title': 'Error analyzing page',
                'meta_description': '',
                'headings': [],
                'cta_buttons': [],
                'forms': [],
                'images': [],
                'text_content': '',
                'has_contact_info': False,
                'has_social_proof': False,
                'page_structure': {}
            }
        
        # Basic URL security check
        if len(page_url) > 500:  # Prevent extremely long URLs
            return {
                'url': page_url,
                'error': 'URL too long',
                'title': 'Error analyzing page',
                'meta_description': '',
                'headings': [],
                'cta_buttons': [],
                'forms': [],
                'images': [],
                'text_content': '',
                'has_contact_info': False,
                'has_social_proof': False,
                'page_structure': {}
            }
        
        # Fetch the page content with shorter timeout
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(page_url, headers=headers, timeout=5)  # Reduced timeout
        response.raise_for_status()
        
        # Check content size to prevent memory issues (reduced limit)
        if len(response.content) > 2 * 1024 * 1024:  # 2MB limit (reduced from 5MB)
            return {
                'url': page_url,
                'error': 'Page content too large (max 2MB)',
                'title': 'Error analyzing page',
                'meta_description': '',
                'headings': [],
                'cta_buttons': [],
                'forms': [],
                'images': [],
                'text_content': '',
                'has_contact_info': False,
                'has_social_proof': False,
                'page_structure': {}
            }
        
        # Parse the HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract key elements for analysis
        analysis = {
            'url': page_url,
            'title': soup.title.string if soup.title else 'No title found',
            'meta_description': '',
            'headings': [],
            'cta_buttons': [],
            'forms': [],
            'images': [],
            'text_content': '',
            'has_contact_info': False,
            'has_social_proof': False,
            'page_structure': {}
        }
        
        # Extract meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            analysis['meta_description'] = meta_desc.get('content', '')[:200]  # Limit length
        
        # Extract headings (limit to first 10)
        for heading in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])[:10]:
            analysis['headings'].append({
                'level': heading.name,
                'text': heading.get_text(strip=True)[:100]  # Limit heading text length
            })
        
        # Extract CTA buttons and links (limit to first 20)
        cta_keywords = ['buy', 'shop', 'order', 'get', 'start', 'sign up', 'subscribe', 'download', 'learn more', 'contact', 'call']
        for link in soup.find_all('a', href=True)[:20]:  # Limit to first 20 links
            link_text = link.get_text(strip=True).lower()
            if any(keyword in link_text for keyword in cta_keywords):
                analysis['cta_buttons'].append({
                    'text': link.get_text(strip=True)[:50],  # Limit text length
                    'href': link.get('href'),
                    'type': 'link'
                })
        
        # Extract buttons (limit to first 10)
        for button in soup.find_all('button')[:10]:
            button_text = button.get_text(strip=True).lower()
            if any(keyword in button_text for keyword in cta_keywords):
                analysis['cta_buttons'].append({
                    'text': button.get_text(strip=True)[:50],  # Limit text length
                    'type': 'button'
                })
        
        # Extract forms (limit to first 5)
        for form in soup.find_all('form')[:5]:
            form_action = form.get('action', '')
            form_method = form.get('method', 'get')
            form_fields = []
            for input_field in form.find_all('input')[:10]:  # Limit fields per form
                field_type = input_field.get('type', 'text')
                field_name = input_field.get('name', '')
                if field_type != 'hidden':
                    form_fields.append({'type': field_type, 'name': field_name})
            
            analysis['forms'].append({
                'action': form_action,
                'method': form_method,
                'fields': form_fields
            })
        
        # Extract images (limit to first 10)
        for img in soup.find_all('img')[:10]:
            src = img.get('src', '')
            alt = img.get('alt', '')
            if src:
                analysis['images'].append({
                    'src': src,
                    'alt': alt[:100]  # Limit alt text length
                })
        
        # Extract main text content (reduced limit)
        main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|main|body'))
        if main_content:
            analysis['text_content'] = main_content.get_text(strip=True)[:500]  # Reduced from 1000 chars
        else:
            # Fallback to body text
            body = soup.find('body')
            if body:
                analysis['text_content'] = body.get_text(strip=True)[:500]  # Reduced from 1000 chars
        
        # Check for contact information (use smaller text sample)
        contact_patterns = [r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b']
        page_text = soup.get_text()[:5000]  # Limit text for pattern matching
        analysis['has_contact_info'] = any(re.search(pattern, page_text) for pattern in contact_patterns)
        
        # Check for social proof elements (use smaller text sample)
        social_proof_keywords = ['testimonial', 'review', 'customer', 'client', 'trust', 'certified', 'award', 'rating']
        analysis['has_social_proof'] = any(keyword in page_text.lower() for keyword in social_proof_keywords)
        
        # Analyze page structure
        analysis['page_structure'] = {
            'has_navigation': bool(soup.find('nav')),
            'has_footer': bool(soup.find('footer')),
            'has_sidebar': bool(soup.find('aside')),
            'is_responsive': 'viewport' in str(soup.find('meta', attrs={'name': 'viewport'})),
            'word_count': len(page_text.split())
        }
        
        return analysis
        
    except Exception as e:
        logger.error(f"Error analyzing page {page_url}: {e}")
        return {
            'url': page_url,
            'error': str(e),
            'title': 'Error analyzing page',
            'meta_description': '',
            'headings': [],
            'cta_buttons': [],
            'forms': [],
            'images': [],
            'text_content': '',
            'has_contact_info': False,
            'has_social_proof': False,
            'page_structure': {}
        }
