from typing import Dict, List, Optional, Any
import logging
from bigas.resources.marketing.ga4_service import GA4Service
from bigas.resources.marketing.openai_service import MarketingLLMService
from bigas.resources.marketing.template_service import TemplateService
from bigas.resources.marketing.trend_analysis_service import TrendAnalysisService
from bigas.resources.marketing.storage_service import StorageService
import os

logger = logging.getLogger(__name__)

class MarketingAnalyticsService:
    """Main service for marketing analytics operations."""
    
    def __init__(self, openai_api_key: str):
        """Initialize the Marketing Analytics Service with dependencies."""
        self.ga4_service = GA4Service()
        self.marketing_llm_service = MarketingLLMService(openai_api_key)
        self.template_service = TemplateService(self.ga4_service)
        self.trend_analysis_service = TrendAnalysisService(self.ga4_service, self.marketing_llm_service)
        self.storage_service = StorageService()
        logger.info("MarketingAnalyticsService initialized successfully")
    
    def answer_question(self, property_id: str, question: str) -> str:
        """Process a natural language question about analytics data and return a formatted answer."""
        try:
            # Parse the question into structured query parameters
            query_params = self.marketing_llm_service.parse_query(question)
            
            # Build and execute the analytics request
            request = self.ga4_service.build_report_request(property_id, query_params)
            response = self.ga4_service.run_report(request)
            
            # Post-process filters in Python if any
            filters = query_params.get("filters", [])
            if filters:
                logger.warning(f"GA4 API filter not supported in this client version. Applying filters in post-processing: {filters}")
                from bigas.resources.marketing.utils import convert_ga4_response_to_dict
                data = convert_ga4_response_to_dict(response)
                filtered_rows = data["rows"]
                headers = data["dimension_headers"]
                for f in filters:
                    field = f["field"]
                    value = f["value"]
                    if field in headers:
                        idx = headers.index(field)
                        filtered_rows = [row for row in filtered_rows if row["dimension_values"][idx] == value]
                        logger.info(f"Applied filter: {field} = {value}, remaining rows: {len(filtered_rows)}")
                    else:
                        logger.warning(f"Filter field '{field}' not in dimension headers; skipping this filter.")
                data["rows"] = filtered_rows
                return self.marketing_llm_service.format_response_obj(data, question)
            else:
                # No filters, proceed as before
                return self.marketing_llm_service.format_response(response, question)
            
        except Exception as e:
            logger.error(f"Failed to process question '{question}': {e}")
            # Do not provide fallback response - re-raise error to fail properly
            raise ValueError(f"Failed to process analytics question: {e}")
    
    def run_template_query(self, template_key: str, date_range: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Run a template-based query."""
        return self.template_service.run_template_query(template_key, os.environ["GA4_PROPERTY_ID"], date_range)
    
    def get_trend_analysis(self, property_id: str, metrics: List[str], dimensions: List[str], 
                          time_frames: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """Get trend analysis data for multiple time frames."""
        return self.ga4_service.get_trend_analysis(property_id, metrics, dimensions, time_frames)
    
    def analyze_trends_with_insights(self, formatted_trends: dict, metrics: list, dimensions: list, date_range: str) -> dict:
        """Analyze trend data and generate AI-powered insights."""
        ai_insights = self.marketing_llm_service.generate_trend_insights(formatted_trends, metrics, dimensions, date_range)
        
        return {
            "data": formatted_trends,
            "ai_insights": ai_insights
        }
    
    def answer_traffic_sources(self, date_range: Optional[Dict[str, str]] = None) -> str:
        """Get traffic sources analysis using template and OpenAI."""
        data = self.template_service.get_traffic_sources_data(os.environ["GA4_PROPERTY_ID"], date_range)
        return self.marketing_llm_service.generate_traffic_sources_analysis(data) 