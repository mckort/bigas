"""
Enhanced AI Service for generating data-driven, website-specific recommendations.
This service provides improved prompts and industry benchmark comparisons.
"""

import json
import re
import openai
from typing import Dict, List, Any, Optional
from datetime import datetime

class EnhancedAIService:
    """Service for generating high-quality, data-driven recommendations with proper AI prompts."""
    
    def __init__(self, openai_api_key: str):
        self.openai_client = openai.OpenAI(api_key=openai_api_key)
        
        # Industry benchmarks for comparison
        self.industry_benchmarks = {
            "ecommerce": {
                "avg_session_duration": 150,  # seconds
                "bounce_rate": 0.45,  # as decimal
                "pages_per_session": 2.5,
                "organic_traffic_percentage": 35,
                "conversion_rate": 0.023,  # as decimal
                "direct_traffic_percentage": 25
            },
            "b2b_services": {
                "avg_session_duration": 180,
                "bounce_rate": 0.55,
                "pages_per_session": 3.2,
                "organic_traffic_percentage": 45,
                "conversion_rate": 0.031,
                "direct_traffic_percentage": 30
            },
            "promotional_products": {
                "avg_session_duration": 135,
                "bounce_rate": 0.50,
                "pages_per_session": 2.1,
                "organic_traffic_percentage": 30,
                "conversion_rate": 0.018,
                "direct_traffic_percentage": 40
            }
        }
    
    def detect_industry(self, company_context: Dict) -> str:
        """Detect industry based on company information."""
        name = company_context.get('name', '').lower()
        domain = company_context.get('domain', '').lower()
        description = company_context.get('description', '').lower()
        
        combined_text = f"{name} {domain} {description}"
        
        if any(term in combined_text for term in ['promo', 'swag', 'promotional', 'merchandise']):
            return 'promotional_products'
        elif any(term in combined_text for term in ['b2b', 'saas', 'software', 'service']):
            return 'b2b_services'
        else:
            return 'ecommerce'
    
    def create_enhanced_recommendations_prompt(self, ga4_data: Dict, company_context: Dict, target_keywords: Optional[List[str]] = None) -> Optional[str]:
        """Create comprehensive prompt for generating multiple high-quality recommendations."""
        
        # Enhanced AI prompt creation with real data validation
        
        try:
            industry = self.detect_industry(company_context)
        except Exception as industry_error:
            # Log error but don't expose details
            raise ValueError("Failed to detect industry from company context")
        benchmarks = self.industry_benchmarks.get(industry, self.industry_benchmarks["ecommerce"])
        
        # Extract key metrics from GA4 data with safe defaults
        sessions = 0
        avg_session_duration = 0
        bounce_rate = 0
        pages_per_session = 0
        conversion_rate = 0
        traffic_sources = {}
        
        # Parse GA4 data with focus on specific question context
        specific_insights = []
        if isinstance(ga4_data, dict):
            # Extract metrics from various possible GA4 response formats
            questions = ga4_data.get('questions', [])
            print(f"🔍 PROMPT: Processing {len(questions)} questions")
            
            for idx, question_data in enumerate(questions):
                print(f"🔍 PROMPT: Question {idx+1}: {question_data.get('question', 'Unknown')}")
                
                # Extract specific insights from this question
                question_text = question_data.get('question', '')
                answer_text = question_data.get('answer', '')
                
                # Create specific insight for this question
                if question_text and answer_text:
                    specific_insights.append(f"Q: {question_text}\nA: {answer_text}")
                
                # Still extract aggregate metrics for benchmarking
                raw_data = question_data.get('raw_data', {})
                if raw_data and 'rows' in raw_data:
                    rows = raw_data['rows']
                    metric_headers = raw_data.get('metric_headers', [])
                    
                    # Extract session data
                    if 'sessions' in str(metric_headers).lower():
                        for row in rows[:3]:  # Check first few rows
                            if 'metric_values' in row:
                                # Handle both string and dict formats for metric values
                                for mv in row['metric_values']:
                                    if isinstance(mv, dict):
                                        # Old format: dict with 'value' key
                                        sessions += float(mv.get('value', 0))
                                    elif isinstance(mv, (str, int, float)):
                                        # New format: direct string/number value
                                        sessions += float(mv)
                                    else:
                                        print(f"🔍 WARNING: Unexpected metric_value type: {type(mv)} = {mv}")
                    
                    # Extract session duration
                    if 'averagesessionduration' in str(metric_headers).lower():
                        for row in rows[:3]:
                            if 'metric_values' in row:
                                for mv in row['metric_values']:
                                    if isinstance(mv, dict):
                                        avg_session_duration = max(avg_session_duration, float(mv.get('value', 0)))
                                    elif isinstance(mv, (str, int, float)):
                                        avg_session_duration = max(avg_session_duration, float(mv))
                    
                    # Extract bounce rate
                    if 'bouncerate' in str(metric_headers).lower():
                        for row in rows[:3]:
                            if 'metric_values' in row:
                                for mv in row['metric_values']:
                                    if isinstance(mv, dict):
                                        bounce_rate = max(bounce_rate, float(mv.get('value', 0)))
                                    elif isinstance(mv, (str, int, float)):
                                        bounce_rate = max(bounce_rate, float(mv))
                    
                    # Extract conversions
                    if 'conversions' in str(metric_headers).lower():
                        conversions = 0
                        for row in rows[:3]:
                            if 'metric_values' in row:
                                for mv in row['metric_values']:
                                    if isinstance(mv, dict):
                                        conversions += float(mv.get('value', 0))
                                    elif isinstance(mv, (str, int, float)):
                                        conversions += float(mv)
                        if sessions > 0:
                            conversion_rate = conversions / sessions
        
        # Only proceed if we have actual data - no fake fallbacks
        if sessions == 0:
            return None
            
        # Calculate pages per session estimate only if we have real data
        if pages_per_session == 0 and sessions > 0:
            # Try to calculate from available data
            pages_per_session = 1.0  # Minimum realistic value
        
        return f"""
You are a senior digital marketing analyst providing specific, actionable recommendations based on detailed analytics insights.

COMPANY PROFILE:
Name: {company_context.get('name', 'Unknown Company')}
Website: {company_context.get('domain', 'N/A')}
Industry: {industry.replace('_', ' ').title()}
Business: {company_context.get('description', 'E-commerce business')}

SPECIFIC ANALYTICS INSIGHTS:
{chr(10).join(specific_insights) if specific_insights else 'Generic analytics data processed'}

AGGREGATE PERFORMANCE CONTEXT:
Sessions: {int(sessions):,}
Avg Session Duration: {int(avg_session_duration)} seconds
Pages per Session: {pages_per_session:.2f}
Bounce Rate: {bounce_rate*100:.1f}% (as decimal: {bounce_rate:.3f})
Conversion Rate: {conversion_rate*100:.2f}% (as decimal: {conversion_rate:.4f})

TRAFFIC SOURCES:
{json.dumps(traffic_sources, indent=2)}

INDUSTRY BENCHMARKS FOR COMPARISON:
- Avg Session Duration: {benchmarks['avg_session_duration']} seconds
- Bounce Rate: {benchmarks['bounce_rate']*100:.1f}%
- Pages per Session: {benchmarks['pages_per_session']}
- Organic Traffic: {benchmarks['organic_traffic_percentage']}%
- Conversion Rate: {benchmarks['conversion_rate']*100:.2f}%
- Direct Traffic: {benchmarks['direct_traffic_percentage']}%

TARGET KEYWORDS: {', '.join(target_keywords) if target_keywords else 'Not specified'}

INSTRUCTIONS:
Based on the SPECIFIC ANALYTICS INSIGHTS above, generate 3-5 unique recommendations that address the particular data points and questions analyzed. Each recommendation should be tailored to the specific insight, not generic advice.

Generate recommendations in this EXACT JSON format:

{{
  "fact": "Specific finding from the analytics insights with numbers",
  "recommendation": "Targeted action addressing the specific insight",
  "category": "traffic|content|conversion|technical|seo",
  "priority": "high|medium|low"
}}

REQUIREMENTS:
✅ Base facts on the SPECIFIC ANALYTICS INSIGHTS, not just aggregate data
✅ Address the particular questions/templates being analyzed
✅ Include specific numbers and comparisons from the insights
✅ Make recommendations specific to the data findings
✅ Avoid generic advice - be specific to what the data shows
✅ Each recommendation should address a different aspect of the analytics

RECOMMENDATION REQUIREMENTS:
✅ Provide implementable actions, not generic advice
✅ Address the performance issue identified in the fact
✅ Be relevant to this specific business and industry
✅ Reference specific website improvements when possible
✅ Keep under 80 characters for clean UI display

EXCELLENT EXAMPLES OF COMPLETE RECOMMENDATIONS:

{{
  "fact": "Avg session duration {int(avg_session_duration)} seconds vs {benchmarks['avg_session_duration']} second industry benchmark",
  "recommendation": "Add FAQ section to contact page for deeper engagement",
  "category": "content",
  "priority": "high"
}}

{{
  "fact": "Organic search traffic {traffic_sources.get('organic_search', 16.5):.1f}% vs industry target of {benchmarks['organic_traffic_percentage']}%",
  "recommendation": "Create product pages targeting '{target_keywords[0] if target_keywords else 'sustainable promotional items'}'",
  "category": "seo",
  "priority": "high"
}}

{{
  "fact": "Conversion rate {conversion_rate*100:.2f}% vs industry average {benchmarks['conversion_rate']*100:.2f}%",
  "recommendation": "Add customer testimonials showcase to homepage hero section",
  "category": "conversion",
  "priority": "medium"
}}

{{
  "fact": "Direct traffic {traffic_sources.get('direct', 60.8):.1f}% vs organic {traffic_sources.get('organic_search', 16.5):.1f}% shows high brand dependence",
  "recommendation": "Launch content marketing for '{target_keywords[0] if target_keywords else 'industry keywords'}'",
  "category": "seo",
  "priority": "high"
}}

{{
  "fact": "Bounce rate {bounce_rate*100:.1f}% vs industry benchmark {benchmarks['bounce_rate']*100:.1f}%",
  "recommendation": "Optimize mobile page loading speed and navigation",
  "category": "technical",
  "priority": "medium"
}}

AVOID THESE BAD EXAMPLES:
❌ {{"fact": "Website needs improvement", "recommendation": "Improve SEO"}}
❌ {{"fact": "Traffic could be better", "recommendation": "Enhance content"}}
❌ {{"fact": "Users don't stay long", "recommendation": "Make site more engaging"}}

CRITICAL: You MUST return ONLY a valid JSON array in this exact format:

[
  {{
    "fact": "Specific metric with numbers vs benchmark",
    "recommendation": "Actionable step",
    "category": "traffic",
    "priority": "high"
  }}
]

Return ONLY the JSON array. No explanations, no markdown, no additional text. Just the raw JSON array.
"""
    
    def generate_enhanced_recommendations(self, ga4_data: Dict, company_context: Dict, target_keywords: Optional[List[str]] = None) -> List[Dict]:
        """Generate enhanced recommendations using improved prompts."""
        try:
            print(f"🔍 Enhanced AI: ===== STARTING ENHANCED RECOMMENDATIONS =====")
            print(f"🔍 Enhanced AI: ga4_data type: {type(ga4_data)}")
            print(f"🔍 Enhanced AI: company_context type: {type(company_context)}")
            print(f"🔍 Enhanced AI: target_keywords type: {type(target_keywords)}")
            
            # Debug company_context access
            if isinstance(company_context, dict):
                company_name = company_context.get('name', 'Unknown')
                print(f"🔍 Enhanced AI: Company name via .get(): {company_name}")
                print(f"🔍 Enhanced AI: Company context keys: {list(company_context.keys())}")
            else:
                print(f"🔍 Enhanced AI: WARNING - company_context is not a dict: {type(company_context)}")
                company_name = str(company_context)
            
            # Debug ga4_data access
            if isinstance(ga4_data, dict):
                print(f"🔍 Enhanced AI: GA4 data keys: {list(ga4_data.keys())}")
                for key, value in ga4_data.items():
                    print(f"🔍 Enhanced AI: GA4[{key}] type: {type(value)}")
                    if hasattr(value, 'get'):
                        print(f"🔍 Enhanced AI: WARNING - GA4[{key}] has .get() method")
            else:
                print(f"🔍 Enhanced AI: WARNING - ga4_data is not a dict: {type(ga4_data)}")
            
            print(f"🔍 Enhanced AI: Generating recommendations for {company_name}")
            print(f"🔍 Enhanced AI: Target keywords: {target_keywords}")
            
            try:
                prompt = self.create_enhanced_recommendations_prompt(ga4_data, company_context, target_keywords)
                if prompt is None:
                    print(f"❌ Enhanced AI: Cannot generate recommendations without real data")
                    return []
                print(f"🔍 Enhanced AI: Generated prompt length: {len(prompt)} characters")
            except Exception as prompt_error:
                print(f"🔍 Enhanced AI: ERROR in create_enhanced_recommendations_prompt: {prompt_error}")
                print(f"🔍 Enhanced AI: prompt_error type: {type(prompt_error)}")
                raise prompt_error
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000,
                temperature=0.3
            )
            print(f"🔍 Enhanced AI: OpenAI response received successfully")
            
            content = response.choices[0].message.content.strip()
            
            print(f"🔍 Enhanced AI: Raw OpenAI response (first 500 chars): {content[:500]}")
            print(f"🔍 Enhanced AI: Response type: {type(content)}")
            print(f"🔍 Enhanced AI: Response length: {len(content)}")
            
            # Try to parse as JSON first
            recommendations = []
            try:
                parsed_json = json.loads(content)
                print(f"🔍 Enhanced AI: Successfully parsed JSON, type: {type(parsed_json)}")
                
                # Handle different response formats
                if isinstance(parsed_json, list):
                    recommendations = parsed_json
                    print(f"🔍 Enhanced AI: Got list with {len(recommendations)} items")
                elif isinstance(parsed_json, dict):
                    if 'recommendations' in parsed_json:
                        recommendations = parsed_json['recommendations']
                        print(f"🔍 Enhanced AI: Extracted from 'recommendations' key")
                    elif any(key in parsed_json for key in ['fact', 'recommendation', 'category', 'priority']):
                        recommendations = [parsed_json]
                        print(f"🔍 Enhanced AI: Single recommendation object")
                    else:
                        recommendations = list(parsed_json.values()) if parsed_json else []
                        print(f"🔍 Enhanced AI: Extracted values from dict")
                else:
                    print(f"🔍 Enhanced AI: Unexpected JSON type: {type(parsed_json)}")
                    
            except json.JSONDecodeError as e:
                print(f"🔍 Enhanced AI: JSON parsing failed: {e}")
                print(f"🔍 Enhanced AI: Treating as text response, trying to extract recommendations")
                
                # If JSON parsing fails, create a simple recommendation from the text
                if content and len(content) > 10:
                    # Try to extract key insights from the text response
                    recommendations = [{
                        "fact": f"Based on analytics data analysis",
                        "recommendation": content[:100] + "..." if len(content) > 100 else content,
                        "category": "general",
                        "priority": "medium"
                    }]
                    print(f"🔍 Enhanced AI: Created fallback recommendation from text")
                else:
                    recommendations = []
                    print(f"🔍 Enhanced AI: Text too short, no recommendations created")
            
            # Validate and filter recommendations
            print(f"🔍 Enhanced AI: Starting validation of {len(recommendations)} recommendations")
            print(f"🔍 Enhanced AI: Recommendations type: {type(recommendations)}")
            
            valid_recommendations = []
            for i, rec in enumerate(recommendations):
                print(f"🔍 Enhanced AI: ===== Processing recommendation {i} =====")
                print(f"🔍 Enhanced AI: Recommendation type: {type(rec)}")
                
                # Handle potential string recommendations that need to be parsed
                if isinstance(rec, str):
                    print(f"🔍 Enhanced AI: Got string recommendation: '{rec}'")
                    try:
                        # Try to parse string as JSON
                        rec = json.loads(rec)
                        print(f"🔍 Enhanced AI: Successfully parsed string to: {type(rec)}")
                    except json.JSONDecodeError:
                        print(f"🔍 Enhanced AI: Could not parse string as JSON, skipping")
                        continue
                
                # Skip if it's not a dictionary after processing
                if not isinstance(rec, dict):
                    print(f"🔍 Enhanced AI: Skipping non-dict recommendation: {type(rec)} = {rec}")
                    continue
                
                print(f"🔍 Enhanced AI: Dictionary keys: {list(rec.keys())}")
                
                # Check for potential .get() issues - log any non-string values
                for key, value in rec.items():
                    if hasattr(value, 'get'):
                        print(f"🔍 Enhanced AI: WARNING - Field '{key}' has .get() method: {type(value)} = {value}")
                    elif not isinstance(value, (str, int, float, bool, type(None))):
                        print(f"🔍 Enhanced AI: WARNING - Field '{key}' is complex type: {type(value)} = {value}")
                
                if self.validate_recommendation_format(rec):
                    print(f"🔍 Enhanced AI: ✅ Recommendation {i} passed validation")
                    valid_recommendations.append(rec)
                else:
                    print(f"🔍 Enhanced AI: ❌ Recommendation {i} failed validation")
            
            print(f"🔍 Enhanced AI: Valid recommendations: {len(valid_recommendations)}/{len(recommendations)}")
            
            return valid_recommendations[:5]  # Limit to 5 recommendations
            
        except Exception as e:
            print(f"Error generating enhanced recommendations: {e}")
            # Return fallback recommendations
            return self._generate_fallback_recommendations(ga4_data, company_context)
    
    def _extract_json_objects(self, content: str) -> List[Dict]:
        """Try to extract JSON objects from response content."""
        recommendations = []
        # Look for JSON objects in the content
        json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        matches = re.findall(json_pattern, content)
        
        for match in matches:
            try:
                obj = json.loads(match)
                if all(key in obj for key in ['fact', 'recommendation', 'category', 'priority']):
                    recommendations.append(obj)
            except json.JSONDecodeError:
                continue
        
        return recommendations
    
    def _generate_fallback_recommendations(self, ga4_data: Dict, company_context: Dict) -> List[Dict]:
        """Generate fallback recommendations when AI fails."""
        return [
            {
                "fact": "Sessions data available but AI analysis failed",
                "recommendation": "Review analytics data manually for insights",
                "category": "content",
                "priority": "medium"
            }
        ]
    
    def validate_recommendation_format(self, recommendation: Dict) -> bool:
        """Validate that recommendation follows proper format with data-driven facts."""
        
        print(f"🔍 VALIDATION: Starting validation for recommendation")
        print(f"🔍 VALIDATION: Recommendation type: {type(recommendation)}")
        print(f"🔍 VALIDATION: Recommendation value: {recommendation}")
        
        # Check if recommendation is actually a dict
        if not isinstance(recommendation, dict):
            print(f"🔍 VALIDATION: FAILED - Not a dictionary, got {type(recommendation)}")
            return False
        
        required_fields = ['fact', 'recommendation', 'category', 'priority']
        missing_fields = [field for field in required_fields if field not in recommendation]
        
        if missing_fields:
            print(f"🔍 VALIDATION: FAILED - Missing fields: {missing_fields}")
            print(f"🔍 VALIDATION: Available fields: {list(recommendation.keys())}")
            return False
        
        print(f"🔍 VALIDATION: All required fields present: {required_fields}")
            
        fact = recommendation['fact']
        rec_text = recommendation['recommendation']
        category = recommendation['category']
        priority = recommendation['priority']
        
        print(f"🔍 VALIDATION: fact type: {type(fact)}, value: '{fact}'")
        print(f"🔍 VALIDATION: recommendation type: {type(rec_text)}, value: '{rec_text}'")
        print(f"🔍 VALIDATION: category type: {type(category)}, value: '{category}'")
        print(f"🔍 VALIDATION: priority type: {type(priority)}, value: '{priority}'")
        
        # Ensure all fields are strings
        if not all(isinstance(field, str) for field in [fact, rec_text, category, priority]):
            non_str_fields = {k: type(v) for k, v in recommendation.items() if not isinstance(v, str)}
            print(f"🔍 VALIDATION: FAILED - Non-string fields: {non_str_fields}")
            return False
        
        # Check if fact contains numbers or percentages
        has_numbers = bool(re.search(r'\d+', fact))
        print(f"🔍 VALIDATION: has_numbers in fact: {has_numbers}")
        
        # Check if recommendation is specific (not overly generic)
        generic_terms = ['improve', 'enhance', 'strengthen', 'increase', 'optimize', 'better']
        word_count = len(rec_text.split())
        contains_generic = any(term in rec_text.lower() for term in generic_terms)
        is_generic = contains_generic and word_count < 6
        
        print(f"🔍 VALIDATION: word_count: {word_count}")
        print(f"🔍 VALIDATION: contains_generic_terms: {contains_generic}")
        print(f"🔍 VALIDATION: is_generic: {is_generic}")
        
        # Fact should have numbers, recommendation should be specific
        is_valid = has_numbers and not is_generic
        print(f"🔍 VALIDATION: Final result: {is_valid} (has_numbers: {has_numbers}, not_generic: {not is_generic})")
        
        return is_valid