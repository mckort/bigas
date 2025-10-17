"""
Marketing Endpoints for Bigas Core
Clean version with proper RecommendationService integration
"""

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
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

marketing_bp = Blueprint('marketing_bp', __name__)

# This logic is moved from the original app file to be specific to this blueprint.
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID")
GA4_API_PROPERTY_ID = f"properties/{GA4_PROPERTY_ID}" if GA4_PROPERTY_ID else None
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# In SaaS mode, don't initialize client at module load  
# Client will be initialized per-request from SaaS layer
DEPLOYMENT_MODE = os.environ.get("DEPLOYMENT_MODE", "standalone")
if DEPLOYMENT_MODE == "saas":
    logger.info("SaaS mode: Google Analytics client will be initialized per-request")
    client = None
else:
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
    """Simple rate limiting check."""
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

# ... existing code would continue here ...
