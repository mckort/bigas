#!/usr/bin/env python3
"""
Test script to debug AI recommendation generation
"""

import os
import json
from bigas.resources.marketing.recommendation_service import RecommendationService

def test_ai_recommendations():
    # Set dummy key just to satisfy constructor (no API calls made)
    os.environ['OPENAI_API_KEY'] = 'sk-test-dummy'
    
    service = RecommendationService()
    
    # Mock report data similar to what we see in production
    report_data = {
        "questions": [
            {
                "raw_data": {
                    "last_30_days": {
                        "summary": {
                            "current_period_total": 35,
                            "previous_period_total": 48,
                            "percentage_change": -27.1,
                            "trend_direction": "down"
                        }
                    },
                    "rows": [
                        {"dimension_values": ["Direct"], "metric_values": ["21"]},
                        {"dimension_values": ["Organic Search"], "metric_values": ["5"]}
                    ]
                }
            }
        ]
    }
    
    summary = "Executive summary here with some analytics data"
    underperforming_analysis = "Some page analysis data here"
    
    print("=== TESTING AI RECOMMENDATION GENERATION ===")
    print(f"Report data keys: {list(report_data.keys())}")
    print(f"Summary: {summary}")
    print(f"Underperforming analysis: {underperforming_analysis}")
    
    try:
        # Test the key metrics extraction
        key_metrics = service._extract_key_metrics(report_data)
        print(f"\n=== KEY METRICS EXTRACTION ===")
        print(f"Extracted {len(key_metrics)} key metric categories")
        print(f"Key metrics: {json.dumps(key_metrics, indent=2)}")
        
        # Test the prompt creation
        prompt = service._create_analysis_prompt(report_data, summary, key_metrics)
        print(f"\n=== PROMPT CREATION ===")
        print(f"Prompt length: {len(prompt)} characters")
        print(f"Prompt preview: {prompt[:500]}...")
        
        # Test with a mock AI response
        print(f"\n=== MOCK AI RESPONSE TESTING ===")
        mock_ai_response = json.dumps([
            {
                "fact": "Users decreased 27.1% from 48 to 35",
                "recommendation": "Investigate the drop in active users",
                "category": "analytics",
                "priority": "high",
                "impact_score": 8
            }
        ])
        
        print(f"Mock AI response: {mock_ai_response}")
        parsed = service._parse_ai_recommendations(mock_ai_response)
        print(f"Parsed result: {parsed}")
        
    except Exception as e:
        print(f"ERROR: Test failed: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    test_ai_recommendations()













