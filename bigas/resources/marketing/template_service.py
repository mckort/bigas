from typing import Dict, List, Optional, Any
import logging
from bigas.resources.marketing.utils import (
    calculate_session_share,
    find_high_traffic_low_conversion
)

logger = logging.getLogger(__name__)

# Question templates for deterministic analytics
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
        "dimensions": ["pagePath", "hostName", "eventName"],
        "metrics": ["sessions", "conversions"],
        "order_by": [{"field": "sessions", "direction": "DESCENDING"}]
    },
    # 4. Which pages or sections (e.g., blog, product pages, landing pages) drive the most engagement (e.g., time on page, low bounce rate)?
    "engagement_pages": {
        "dimensions": ["pagePath", "hostName"],
        "metrics": ["averageSessionDuration", "bounceRate"],
        "order_by": [{"field": "averageSessionDuration", "direction": "DESCENDING"}]
    },
    # 5. Are there underperforming pages with high traffic but low conversions?
    "underperforming_pages": {
        "dimensions": ["pagePath", "hostName"],
        "metrics": ["sessions", "conversions"],
        "postprocess": "find_high_traffic_low_conversion"
    },
    # 6. How do blog posts or content pages contribute to conversions (e.g., assisted conversions, last-click conversions)?
    "blog_conversion": {
        "dimensions": ["pagePath", "hostName", "sessionDefaultChannelGroup"],
        "metrics": ["conversions", "sessions"],
        "filters": [{"field": "pagePath", "operator": "contains", "value": "blog"}]
    }
}

class TemplateService:
    """Service for handling template-driven analytics queries."""
    
    def __init__(self, ga4_service):
        """Initialize the template service with GA4 service dependency."""
        self.ga4_service = ga4_service
    
    def get_template(self, template_key: str) -> Optional[Dict[str, Any]]:
        """Get a template by key."""
        return QUESTION_TEMPLATES.get(template_key)
    
    def list_templates(self) -> List[str]:
        """List all available template keys."""
        return list(QUESTION_TEMPLATES.keys())
    
    def run_template_query(self, template_key: str, property_id: str, date_range: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Run a template-based query against GA4."""
        template = self.get_template(template_key)
        if not template:
            raise ValueError(f"Template '{template_key}' not found")
        
        # Get the raw data from GA4
        data = self.ga4_service.run_template_query(property_id, template, date_range)
        
        # Validate that we have real data before processing
        if not data.get('rows') or len(data['rows']) == 0:
            raise ValueError(f"Template '{template_key}' returned no GA4 data. Cannot provide analysis without real data.")
        
        # Debug logging
        logger.info(f"Template {template_key}: Got data with {len(data.get('rows', []))} rows")
        if data.get('rows'):
            logger.info(f"Sample row structure: {data['rows'][0]}")
        
        # Apply post-processing if specified
        if template.get("postprocess") == "calculate_session_share":
            logger.info(f"Applying calculate_session_share to {template_key}")
            data = calculate_session_share(data)
        elif template.get("postprocess") == "find_high_traffic_low_conversion":
            logger.info(f"Applying find_high_traffic_low_conversion to {template_key}")
            data = find_high_traffic_low_conversion(data)
        
        return data
    
    def get_traffic_sources_data(self, property_id: str, date_range: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Get traffic sources data using the traffic_sources template."""
        return self.run_template_query("traffic_sources", property_id, date_range)
    
    def get_session_quality_data(self, property_id: str, date_range: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Get session quality data using the session_quality template."""
        return self.run_template_query("session_quality", property_id, date_range)
    
    def get_top_pages_conversions_data(self, property_id: str, date_range: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Get top pages conversions data using the top_pages_conversions template."""
        return self.run_template_query("top_pages_conversions", property_id, date_range)
    
    def get_engagement_pages_data(self, property_id: str, date_range: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Get engagement pages data using the engagement_pages template."""
        return self.run_template_query("engagement_pages", property_id, date_range)
    
    def get_underperforming_pages_data(self, property_id: str, date_range: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Get underperforming pages data using the underperforming_pages template."""
        return self.run_template_query("underperforming_pages", property_id, date_range)
    
    def get_blog_conversion_data(self, property_id: str, date_range: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Get blog conversion data using the blog_conversion template."""
        return self.run_template_query("blog_conversion", property_id, date_range) 