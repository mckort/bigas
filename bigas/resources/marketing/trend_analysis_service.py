from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import logging
from bigas.resources.marketing.utils import format_trend_data_for_humans

logger = logging.getLogger(__name__)

class TrendAnalysisService:
    """Service for orchestrating trend analysis workflows."""
    
    def __init__(self, ga4_service, marketing_llm_service):
        """Initialize the trend analysis service with dependencies."""
        self.ga4_service = ga4_service
        self.marketing_llm_service = marketing_llm_service
    
    def get_default_time_frames(self) -> List[Dict[str, str]]:
        """Get default time frames for trend analysis."""
        today = datetime.now()
        return [
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
    
    def get_time_frames_for_date_range(self, date_range: str) -> List[Dict[str, str]]:
        """Get time frames based on the specified date range."""
        today = datetime.now()
        
        if date_range == 'last_7_days':
            return [
                {
                    "name": "last_7_days", 
                    "start_date": (today - timedelta(days=7)).strftime("%Y-%m-%d"), 
                    "end_date": today.strftime("%Y-%m-%d"), 
                    "comparison_start_date": (today - timedelta(days=14)).strftime("%Y-%m-%d"), 
                    "comparison_end_date": (today - timedelta(days=8)).strftime("%Y-%m-%d")
                }
            ]
        elif date_range == 'last_30_days':
            return [
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
            return self.get_default_time_frames()
    
    def analyze_trends(self, property_id: str, metrics: List[str], dimensions: List[str], 
                      time_frames: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """Analyze trends for the given metrics and dimensions."""
        if time_frames is None:
            time_frames = self.get_default_time_frames()
        
        # Get raw trend data from GA4
        raw_trend_data = self.ga4_service.get_trend_analysis(property_id, metrics, dimensions, time_frames)
        
        # Validate that we have real data before processing
        has_real_data = False
        for time_frame, data in raw_trend_data.items():
            if data.get("rows") and len(data["rows"]) > 0:
                has_real_data = True
                break
        
        if not has_real_data:
            raise ValueError(f"No real GA4 data available for trend analysis. Time frames: {[tf['name'] for tf in time_frames]}")
        
        # Format the data for human consumption
        formatted_trends = format_trend_data_for_humans(raw_trend_data, time_frames)
        
        return {
            "raw_data": raw_trend_data,
            "formatted_data": formatted_trends
        }
    
    def analyze_trends_with_insights(self, property_id: str, metrics: List[str], dimensions: List[str], 
                                   date_range: str = "last_30_days") -> Dict[str, Any]:
        """Analyze trends and generate AI-powered insights."""
        # Get time frames for the specified date range
        time_frames = self.get_time_frames_for_date_range(date_range)
        
        # Get trend data
        trend_result = self.analyze_trends(property_id, metrics, dimensions, time_frames)
        formatted_trends = trend_result["formatted_data"]
        
        # Generate AI insights
        ai_insights = self.marketing_llm_service.generate_trend_insights(
            formatted_trends, metrics, dimensions, date_range
        )
        
        return {
            "data": formatted_trends,
            "ai_insights": ai_insights,
            "metadata": {
                "metrics_analyzed": metrics,
                "dimensions_analyzed": dimensions,
                "date_range": date_range,
                "time_frames_analyzed": list(formatted_trends.keys())
            }
        }
    
    def get_weekly_trend_analysis(self, property_id: str) -> Dict[str, Any]:
        """Get trend analysis specifically for weekly reports."""
        metrics = ["activeUsers", "sessions"]
        dimensions = ["country"]
        date_range = "last_30_days"
        
        return self.analyze_trends_with_insights(property_id, metrics, dimensions, date_range) 