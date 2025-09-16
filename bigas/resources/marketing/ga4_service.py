from typing import Dict, List, Optional, Any
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
import logging
import os
from google.auth import default
from google.oauth2 import service_account
from bigas.resources.marketing.utils import (
    convert_ga4_response_to_dict,
    process_ga_response,
    get_default_date_range,
    get_consistent_date_range
)

logger = logging.getLogger(__name__)

class GA4Service:
    """Service for handling Google Analytics 4 API interactions."""
    
    def __init__(self):
        """Initialize the GA4 service with the analytics client using proper service account credentials."""
        try:
            # In Cloud Run, use Application Default Credentials (ADC) which will use the service account
            # that's configured via --service-account parameter
            credentials, project = default()
            self.analytics_client = BetaAnalyticsDataClient(credentials=credentials)
            logger.info(f"GA4Service initialized with credentials for project: {project}")
        except Exception as e:
            logger.error(f"Failed to initialize GA4Service with credentials: {e}")
            # Fallback to default initialization
            self.analytics_client = BetaAnalyticsDataClient()
            logger.warning("Using default GA4 client initialization")
    
    def get_default_date_range(self) -> Dict[str, str]:
        """Get default date range for the last 30 days."""
        return get_default_date_range()
        
    def get_consistent_date_range(self) -> Dict[str, str]:
        """Get a consistent date range for reproducible results."""
        return get_consistent_date_range()
    
    def build_report_request(self, property_id: str, query_params: Dict[str, Any]) -> RunReportRequest:
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
    
    def run_report(self, request: RunReportRequest) -> Any:
        """Execute a GA4 report request."""
        return self.analytics_client.run_report(request)
    
    def get_trend_analysis(self, property_id: str, metrics: List[str], dimensions: List[str], time_frames: List[Dict[str, str]]) -> Dict[str, Any]:
        """Get trend analysis data for multiple time frames."""
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
                logger.error(f"Error getting trend data for {tf['name']}: {e}")
                # Do not provide empty fallback data - re-raise error to fail properly
                raise ValueError(f"Failed to get GA4 trend data for {tf['name']}: {e}")
        
        return raw_trend_data
    
    def run_template_query(self, property_id: str, template: Dict[str, Any], date_range: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Run a template-based query against GA4."""
        if date_range is None:
            date_range = self.get_consistent_date_range()
            
        query_params = {
            "metrics": template["metrics"],
            "dimensions": template["dimensions"],
            "date_range": date_range
        }
        
        # Add optional template parameters
        if "order_by" in template:
            query_params["order_by"] = template["order_by"]
        if "filters" in template:
            query_params["filters"] = template["filters"]
        
        request = self.build_report_request(property_id, query_params)
        response = self.run_report(request)
        return convert_ga4_response_to_dict(response) 