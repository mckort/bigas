<div align="center">
  <img src="assets/images/bigas-ready-to-serve.png" alt="Bigas Logo" width="200">
</div>

# Bigas - AI Team for Solo Founders, Built for Vibe Coding

Welcome to Bigas, an open-source project hosted on GitHub, designed to empower solo entrepreneurs by providing a virtual team of AI-powered resources. The name Bigas is derived from Latin, meaning "team," and reflects our mission to create a collaborative ecosystem of AI tools to support entrepreneurs in building and scaling their ventures. Our vision is to make advanced AI capabilities accessible to individuals who are navigating the entrepreneurial journey solo, enabling them to achieve more with less.

## Project Overview

Bigas (meaning "my team") is a modular, open-source platform that leverages cutting-edge AI tools to simulate the expertise of a dedicated team. Bigas enables solo entrepreneurs to streamline workflows, automate tasks, and gain actionable insights without the need for extensive technical expertise or a large team, by using coding tools like Cursor.

The initial focus of Bigas is a **Marketing Analytics Resource**, a powerful AI-driven tool that connects to Google Analytics to extract, analyze, and interpret data. This resource provides detailed observations and actionable recommendations to optimize marketing strategies, improve user engagement, and drive business growth. Whether you're looking to enhance your website's performance, refine your campaigns, or better understand your audience, Bigas equips you with the insights needed to make data-driven decisions.

### Deployment & Licensing

**Google Cloud Run Optimized**: This project is natively adapted and optimized to run on Google Cloud Run, providing seamless integration with Google's ecosystem and automatic scaling capabilities. The deployment scripts and configuration are specifically designed for Google Cloud Run.

**MIT License Freedom**: However, as an open-source project under the MIT license, you are free to deploy and run Bigas anywhere you choose. Whether you prefer AWS, Azure, DigitalOcean, your own servers, or any other platform, the MIT license gives you complete freedom to adapt and deploy the solution according to your needs and preferences.

**Flexible Architecture**: The Flask-based architecture and Docker containerization make it easy to deploy on virtually any platform that supports containerized applications.

## Key Features

**AI-Powered Marketing Analytics**: Seamlessly integrates with Google Analytics 4 to collect and analyze data, delivering clear observations and tailored recommendations for improving marketing performance through natural language queries.

**Natural Language Analytics**: Ask questions in plain English like "What are my top traffic sources?" or "Which pages have the highest bounce rates?" and get AI-powered insights and recommendations.

**Automated Weekly Reports**: Get comprehensive weekly analytics reports automatically posted to Discord, with AI-generated summaries and actionable recommendations.

**Built for Solo Entrepreneurs**: Designed with simplicity and accessibility in mind, Bigas empowers non-technical users to leverage advanced AI capabilities without a steep learning curve.

**Extensible Framework**: Built to support additional AI resources in the future, allowing the virtual team to grow with your business needs.

**Open-Source Collaboration**: Hosted on GitHub, Bigas invites developers, growth hackers, and entrepreneurs to contribute to its growth, ensuring a community-driven approach to innovation.

## 🚀 Features

- **Analytics Reporting**: Get comprehensive Google Analytics reports with customizable date ranges
- **Custom Reports**: Create tailored reports with specific dimensions and metrics
- **Natural Language Queries**: Ask analytics questions in plain English
- **Trend Analysis**: Analyze data trends over time with detailed insights
- **Weekly Reports**: Automated weekly analytics reports posted to Discord
- **MCP Server**: Modern Model Context Protocol server for AI tool integration

## 🏗️ Architecture

The Bigas platform is designed as a **Modular Monolith**. This architecture provides the scalability benefits of microservices (like separation of concerns) without the initial operational complexity. A central API gateway routes requests to independent "AI Resources" that are managed within the same application.

Here is a high-level overview of the current structure:

```text
+--------------------------+
|         Clients          |
|  - Manual User (curl)    |
|  - Google Cloud Scheduler|
+--------------------------+
             |
             v
+--------------------------+
|   Google Cloud Run       |
|  (Hosting Environment)   |
+--------------------------+
             |
             v
+-------------------------------------------------+
|  Bigas Platform (app.py - Flask App)            |
|                                                 |
| +---------------------------------------------+ |
| | API Gateway / Router                        | |
| +---------------------------------------------+ |
|   |                      |                      |
|   | (/marketing/*)       | (/product/*)         |
|   v                      v                      |
| +----------------------+ +--------------------+ |
| | Marketing Resource   | | Product Resource   | |
| | (Connects to GA,     | | (Placeholder for   | |
| |  OpenAI, Discord)    | |  Jira, Figma, etc) | |
| +----------------------+ +--------------------+ |
|                                                 |
+-------------------------------------------------+
```

### Data Flow Explained:

1.  **Clients**: A user or automated service sends an HTTP request to an endpoint.
2.  **Google Cloud Run**: The request is received by the hosting environment.
3.  **Bigas Platform (`app.py`)**: The main Flask application receives the request.
4.  **API Router**: The app's internal router inspects the URL and forwards the request to the appropriate resource module.
    *   Requests to `/mcp/tools/ask_analytics_question` are routed to the **Marketing Resource**.
    *   A future request to `/mcp/tools/get_sprint_summary` would be routed to the **Product Resource**.
5.  **AI Resource**: The specific resource (e.g., Marketing) contains all the business logic to connect to external APIs (Google Analytics, OpenAI, etc.) and fulfill the request.
6.  **Response**: The resource returns a response, which is sent back to the client.

This structure allows you to easily add new resources (Sales, Support, etc.) by simply creating a new module in the `bigas/resources` directory, with minimal changes to the core application.

---

## 📡 API Examples

The Bigas Marketing Analytics Server provides a RESTful API for accessing Google Analytics data. Here are some examples of how to use the key endpoints:

### Natural Language Analytics Questions

Ask questions about your analytics data in plain English:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/ask_analytics_question \
  -H "Content-Type: application/json" \
  -d '{"question": "Which country had the most active users last week?"}' | jq
```

This endpoint uses AI to interpret your question and return relevant analytics data in a natural, conversational format.

### Weekly Analytics Reports

Generate comprehensive weekly reports and post them to Discord:

   ```bash
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report \
  -H "Content-Type: application/json" | jq
```

This endpoint automatically:
- Fetches the last 7 days of analytics data
- Generates a comprehensive report with key metrics
- Posts the report to your configured Discord channel
- Creates an AI-powered Q&A summary of the report

### Custom Analytics Reports

Create tailored reports with specific dimensions and metrics:

   ```bash
curl -X POST https://your-deployment-url.com/mcp/tools/fetch_custom_report \
  -H "Content-Type: application/json" \
  -d '{
    "dimensions": ["country", "device_category"],
    "metrics": ["active_users", "sessions"],
    "date_ranges": [{"start_date": "2024-01-01", "end_date": "2024-01-31"}]
  }' | jq
```

### Standard Analytics Reports

Get predefined analytics reports:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/fetch_analytics_report \
  -H "Content-Type: application/json" \
  -d '{"date_range": "last_7_days"}' | jq
```

### Trend Analysis

Analyze data trends over time:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_trends \
  -H "Content-Type: application/json" \
  -d '{"metric": "active_users", "date_range": "last_30_days"}' | jq
```

**To show just the summary:**
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_trends \
  -H "Content-Type: application/json" \
  -d '{"metric": "active_users", "date_range": "last_30_days"}' | jq '.data.last_30_days.summary'
```

**To show top performers:**
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_trends \
  -H "Content-Type: application/json" \
  -d '{"metric": "active_users", "date_range": "last_30_days"}' | jq '.data.last_30_days.top_performers'
```

## 🧪 Testing Weekly Analytics Reports

The weekly analytics report endpoint generates comprehensive reports and posts them to your configured Discord channel. Here's how to test it:

### Testing the Endpoint

```bash
curl -X POST https://your-deployment-url.app/mcp/tools/weekly_analytics_report \
  -H "Content-Type: application/json"
```

### What Gets Posted to Discord

When you run this endpoint, it automatically:
1. Fetches the last 7 days of Google Analytics data
2. Generates a comprehensive report with key metrics
3. Posts the report to your Discord channel
4. Creates an AI-powered Q&A summary

### Example Discord Output

Here's what you'll see posted to your Discord channel:

```
Weekly Analytics report on its way...

Q: What are the primary traffic sources contributing to total sessions?
A: Here's how your 43 total sessions broke down by channel:
• Organic Search: 19 sessions (44%)
• Direct: 18 sessions (42%)
• Organic Social: 6 sessions (14%)

Key takeaways:
• Organic Search is your top source, closely followed by Direct visits
• Social is a smaller driver of traffic right now
• Referral, paid search and email all registered zero sessions—these could be untapped opportunities

Q: What is the average session duration and pages per session?
A: Across all users, you had 43 sessions, with a total engagement time of 764 seconds and 95 page views:
• Average session duration ≈ 17.8 seconds
• Pages per session ≈ 2.2

Key takeaways:
• An average visit of under 20 seconds is quite short
• Viewing just over two pages per visit suggests modest exploration

Q: Which pages are the most visited?
A: Top pages by sessions:
• Homepage "/" – 38 sessions
• Get-in-touch "/get-in-touch-with-us" – 13 sessions
• About us "/about-us" – 9 sessions

Summary Recommendations:
• Set up and verify conversion tracking
• Boost homepage engagement & conversions
• Optimize high-traffic Contact & About Us pages
• Deepen session duration & page depth
• Diversify traffic sources
```

### Prerequisites

Before testing, ensure you have:
- ✅ `GA4_PROPERTY_ID` configured
- ✅ `OPENAI_API_KEY` configured  
- ✅ `DISCORD_WEBHOOK_URL` configured
- ✅ Valid Google Analytics data for the last 7 days

### Testing Tips

- **First Time**: Run once to verify Discord integration works
- **Data Check**: Ensure your GA4 property has recent data
- **Webhook Test**: Verify your Discord webhook URL is correct
- **Rate Limits**: The report generation can take 30-60 seconds

### Scheduling Weekly Reports

For automated weekly reports, you can use **Google Cloud Scheduler** to call the endpoint automatically. This allows you to:

- **Schedule regular reports**: Set up weekly, daily, or custom intervals
- **Automate workflows**: No manual intervention required
- **Flexible timing**: Choose the best time for your team to receive reports
- **Easy management**: Pause, resume, or update schedules as needed

Simply configure a Cloud Scheduler job to make a POST request to your weekly analytics report endpoint at your preferred schedule.

## Adding a New Analytics Question (Template-Driven)

To add a new analytics question using the template-driven system:

1. **Edit `bigas/resources/marketing/service.py`:**
   - Add a new entry to the `QUESTION_TEMPLATES` dictionary at the top of the file. Use the existing entries as examples. Specify the GA4 metrics, dimensions, and (optionally) filters, order_by, or postprocess helpers.
   - Example:
     ```python
     QUESTION_TEMPLATES = {
         # ... existing templates ...
         "my_new_question": {
             "dimensions": ["country", "deviceCategory"],
             "metrics": ["activeUsers", "sessions"],
             "postprocess": "my_custom_processor"  # Optional
         }
     }
     ```

2. **Add a post-processing function (if needed):**
   - Edit `bigas/resources/marketing/utils.py` and add your custom processing function
   - Example:
     ```python
     def my_custom_processor(data: Dict[str, Any]) -> Dict[str, Any]:
         """Custom processing for my new question."""
         # Your processing logic here
         return data
     ```

3. **Use the template in your code:**
   - Call `service.run_template_query("my_new_question")` to get the data
   - Or create a dedicated method like `answer_my_new_question()` in the service

## Code Structure

The project follows a clean, modular architecture:

### **`bigas/resources/marketing/`**
- **`endpoints.py`** - Flask route handlers (HTTP layer)
- **`service.py`** - Main orchestrator service that coordinates other services
- **`ga4_service.py`** - Google Analytics 4 API interactions
- **`openai_service.py`** - OpenAI API interactions and natural language processing
- **`template_service.py`** - Template-driven analytics queries
- **`trend_analysis_service.py`** - Trend analysis orchestration
- **`utils.py`** - Pure utility functions for data processing and formatting

### **Service Layer Architecture**

The application follows a clean service-oriented architecture with single responsibility principles:

#### **Main Orchestrator (`service.py`)**
- `MarketingAnalyticsService` - Main service that coordinates other specialized services
- Provides a unified interface for endpoints
- Handles dependency injection between services

#### **GA4 Service (`ga4_service.py`)**
- `GA4Service` - Handles all Google Analytics 4 API interactions
- `build_report_request()` - Builds GA4 API requests with proper field mapping
- `run_report()` - Executes GA4 API calls
- `get_trend_analysis()` - Gets trend data for multiple time frames
- `run_template_query()` - Runs template-based queries against GA4

#### **OpenAI Service (`openai_service.py`)**
- `OpenAIService` - Handles all OpenAI API interactions and natural language processing
- `parse_query()` - Converts natural language questions to structured GA4 queries
- `format_response()` - Formats GA4 responses into natural language answers
- `generate_trend_insights()` - Generates AI-powered insights for trend analysis
- `generate_traffic_sources_analysis()` - Analyzes traffic sources data

#### **Template Service (`template_service.py`)**
- `TemplateService` - Manages template-driven analytics queries
- `run_template_query()` - Executes predefined analytics templates
- `get_traffic_sources_data()` - Gets traffic sources analysis
- `get_session_quality_data()` - Gets session quality metrics
- `get_top_pages_conversions_data()` - Gets top pages by conversions
- `get_engagement_pages_data()` - Gets engagement metrics by page
- `get_underperforming_pages_data()` - Identifies high-traffic, low-conversion pages
- `get_blog_conversion_data()` - Analyzes blog post conversion performance

#### **Trend Analysis Service (`trend_analysis_service.py`)**
- `TrendAnalysisService` - Orchestrates trend analysis workflows
- `analyze_trends_with_insights()` - Combines GA4 data with AI insights
- `get_weekly_trend_analysis()` - Specialized analysis for weekly reports
- `get_time_frames_for_date_range()` - Manages time frame calculations
- `analyze_trends()` - Core trend analysis functionality

### **Utility Functions (`utils.py`)**

The `utils.py` module contains pure helper functions for data processing:

#### **Data Conversion**
- `convert_metric_name()` - Convert snake_case to camelCase for GA4 metrics
- `convert_dimension_name()` - Convert snake_case to camelCase for GA4 dimensions
- `convert_ga4_response_to_dict()` - Convert GA4 API response to JSON-serializable dict

#### **Date Range Helpers**
- `get_default_date_range()` - Get last 30 days date range
- `get_consistent_date_range()` - Get reproducible date range (ending yesterday)
- `get_date_range_strings()` - Get start/end date strings for given days

#### **Data Processing**
- `process_ga_response()` - Process GA4 response into usable format
- `calculate_session_share()` - Calculate percentage share of sessions
- `find_high_traffic_low_conversion()` - Flag underperforming pages
- `generate_basic_analysis()` - Fallback analysis when OpenAI fails

#### **Data Formatting**
- `format_trend_data_for_humans()` - Format trend data for human consumption (pure data transformation)

### **Benefits of This Architecture**
- ✅ **Single Responsibility Principle** - Each service has one clear purpose
- ✅ **Dependency Injection** - Services are injected where needed
- ✅ **Easier Testing** - Each service can be tested independently
- ✅ **Better Maintainability** - Changes to one service don't affect others
- ✅ **Clear Separation of Concerns** - API calls, NLP, templates, and orchestration are separate
- ✅ **Reduced Coupling** - Services are loosely coupled through interfaces
- ✅ **Scalability** - Easy to add new services or modify existing ones

## 🛠️ Installation & Deployment

Getting started with Bigas is straightforward. The primary method for deployment is using the provided `deploy.sh` script, which automates the process of building and deploying the application to Google Cloud Run.

### Quick Start with `