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
1. Fetches the last 30 days of Google Analytics data
2. Generates a comprehensive report with key metrics
3. Posts the report to your Discord channel
4. Creates an AI-powered Q&A summary

### Example Discord Output

Here's what you'll see posted to your Discord channel:

```
📊 Weekly Analytics Report on its way...

Q: What are the key trends in our website performance over the last 30 days, including user growth, session patterns, and any significant changes?
A: 1. Key Trends and Patterns: Over the last 30 days, we've seen a significant decrease in both active users and sessions, with a total drop of 32.7%. The top-performing countries were the United States and Sweden, although both saw a decrease in active users during this period.

Significant Changes: The total number of active users decreased from 52 to 35, a drop of nearly one-third. This is a negative change that suggests a decline in user engagement.

Potential Causes: The reason for the decrease is not immediately clear from the data provided. It may be due to seasonal fluctuations, changes in marketing strategies, competitor activities, or other external factors.

Actionable Recommendations: 
Investigate the cause of the drop in active users and sessions. Look into any changes in marketing strategies, competitor activities, or external factors that may have caused the decrease.
Analyse the performance of the United States and Sweden more deeply to understand why they're top-performing countries and why they've seen a decrease in active users.
For the countries listed as "(not set)", investigate why the country data is missing and correct it if possible. This will help provide a more accurate analysis.

Business Impact and Next Steps: The drop in active users and sessions is a cause for concern as it may indicate a decrease in customer engagement or potential issues with the website or app. The next steps should be to investigate the cause of the decrease, address any issues found, and monitor the metrics to see if they improve. Further, understanding why the United States and Sweden are top-performing countries may provide insights that can be applied to other markets.

Q: What are the primary traffic sources (e.g., organic search, direct, referral, paid search, social, email) contributing to total sessions, and what is their respective share?
A: The primary traffic sources contributing to total sessions are as follows:

Direct traffic is the highest source, contributing to 42.6% of total sessions.
Organic Search is the second highest, accounting for 38.3% of total sessions.
Organic Social is third, contributing to 12.8% of total sessions.
Cross-network, Referral, and Unassigned sources each contribute to 2.1% of total sessions.

Notably, there is a significant reliance on Direct and Organic Search traffic, which together account for over 80% of the total traffic. This may indicate a strong brand presence and effective SEO strategies. However, it also suggests a potential vulnerability, as the site's traffic is heavily dependent on these sources.

To improve traffic diversity, it is recommended to boost efforts in other channels. Organic Social, for instance, could be increased through a more robust social media strategy, while Referral traffic could be enhanced by partnering with other sites for link exchanges or guest posting. Cross-network traffic, which is currently the lowest, could be improved by leveraging different networks or platforms for advertising or content distribution.

Q: What is the average session duration and pages per session across all users?
A: The average session duration across all users is approximately 73.18 seconds, meaning that typical users spend just over a minute on the site once they log in. Meanwhile, they view about 2.22 pages (or screens) during that session. 

These figures imply that users are somewhat engaged, as they spend enough time to possibly read or interact with the content and visit more than one page per session.

However, if our site has a larger number of pages or complex content that may take longer to consume, this may suggest that users are not exploring the site thoroughly or that they're having a hard time finding what they need. The next step could be identifying ways to encourage longer sessions and more page views per session, such as improving site navigation, highlighting popular content, or introducing more engaging and relevant content.

Q: Which pages are the most visited, and how do they contribute to conversions (e.g., product pages, category pages, blog posts)?
A: The most visited page on the website is the homepage ("/"), attracting 39 visits but unfortunately, these visits didn't lead to any conversions. The second and third most visited pages are "/get-in-touch-with-us" and "/about-us", with 14 and 10 visits respectively but also with zero conversions.

The pages related to the business purpose like "/why-promotional-clothing" and "/why-green-promo-wear" have been visited lesser times, 4 and 5 times respectively and didn't contribute to conversions either. Meanwhile, the "/blog-posts-and-news" and individual blog post ("/post/the-truth-about-promotional-clothing-waste---why-sustainable-promo-wear-matters") have had very low visits and no conversions.

From our data, it seems none of the pages directly contributed to conversions. This may indicate that there is potential for improving the website's content strategy or the user experience to increase engagement and conversions. 

To derive some actionable insights, a more detailed analysis may be required to understand why people visit different pages and why they are not converting, perhaps using additional data like time spent on page and bounce rates. It might be beneficial to focus on improving the content on the more frequented pages, like your homepage and "/get-in-touch-with-us", to encourage more conversions. And paying attention to the lower-visited pages and blogs with zero conversions to understand what might be missing or causing users not to convert.

Q: Which pages or sections (e.g., blog, product pages, landing pages) drive the most engagement (e.g., time on page, low bounce rate)?
A: Based on the data from Google Analytics, the top three pages in terms of engagement on your website are:

Blog post titled "The Truth about Promotional Clothing Waste & Why Sustainable Promo Wear Matters"
The "Eco Certifications" page
The "About Us" page

These pages tend to retain users' attention for longer periods of time when compared to other parts of your website. The average session duration for these pages is 185.7 seconds, 90.4 seconds, and 76.2 seconds, respectively, which indicates that users tend to spend a lot more time on these pages. Furthermore, the bounce rates of these pages are all very low, standing at 0%, 0%, and 20%, respectively.

A noteworthy fact is that the homepage ("/") has a higher bounce rate (56.4%) and a lesser average session duration (39.7 seconds). This could mean that while the homepage is bringing visitors in, it is not as successful in keeping them engaged.

In light of these findings, I recommend focusing more on your blog and informational content—such as what you're doing with the "The Truth about Promotional Clothing Waste & Why Sustainable Promo Wear Matters" and "Eco Certifications" pages. Users seem to be interested in informative, value-added content, and this can be further leveraged to increase their engagement with your site. Additionally, it might be necessary to consider enhancing the homepage to make it more engaging.

Q: Are there underperforming pages with high traffic but low conversions?
A: Yes, there are underperforming pages on your site with high traffic but low conversions. 

The three pages with the most significant traffic yet no conversions are the Home page ("/"), "Get in Touch With Us" page, and the "About Us" page. Here are the numbers:

The Home page ("/") has had 39 sessions, but no conversions.
The "Get in Touch With Us" page has had 14 sessions, but again, no conversions.
The "About Us" page received 10 visits, but no conversions.

These pages are getting the attention of your visitors, but they're not enticing them to take the next step. This suggests that while these pages can generate interest, they might lack a clear call to action or don't meet user's needs or expectations.

You could improve the performance of these pages by reviewing their content and layout. Make sure the call to action is clear and appealing, and the information provided is useful and relevant. Test different versions of these pages to find what works best for your audience.

On the brighter side, pages like "Why Green Promo Wear" and "Why Promotional Clothing" are not underperforming, despite having lower sessions. So, these topics might be of interest to your audience and might be worth highlighting more on your high traffic pages. 

Remember, even small improvements in conversion rates can lead to significant increases in revenue, especially on high traffic pages. It's definitely worth the time and effort to optimize these pages for better performance.

Q: How do blog posts or content pages contribute to conversions (e.g., assisted conversions, last-click conversions)?
A: According to the Google Analytics data, blog posts or content pages currently do not appear to directly contribute to any conversions, as none were recorded during the period evaluated. However, that does not mean they are not playing a part in the user's journey before they decide to make a conversion.

There are a total of 31 records, indicating that various pages on your website are receiving traffic from various sources. The page that has the most traffic is '/get-in-touch-with-us' with a total of 15 sessions, followed by '/' (your homepage) and '/about-us' with a total of 39 and 10 sessions respectively. Most of the traffic to these pages comes from direct sources and organic searches.

However, it's interesting to note that your blog pages, such as '/blog-posts-and-news' and '/post/the-truth-about-promotional-clothing-waste---why-sustainable-promo-wear-matters', are attracting visitors not only through direct visits and organic searches but also through organic social means. This suggests that your content is being shared on social media platforms, which can play an important role in driving traffic to your site.

Despite the lack of recorded conversions from these pages, it's key to remember that blog posts and content pages can play a critical role in the customer's journey. They can educate readers about your products or services, build trust with potential customers, and encourage them to visit other pages on your website - all of which can eventually lead to conversions.

A recommendation to see the role of blog posts in the path to conversions would be to set up a goal in Google Analytics that treats a specific action as a 'Micro Conversion' e.g. clicking a 'learn more' link in your blog which can be tracked and may provide insight into the value of your blog content. 

Lastly, consider improving your blog content to make it more engaging, shareable, and incorporate a 'Micro Conversion' tracking to understand their role in the conversion journey.

Summary Recommendations:
Executive Summary:

The website performance over the last 30 days shows a downward trend in active users and sessions with a decrease of 32.7%, primarily from the US and Sweden. The main traffic sources are Direct (42.6%) and Organic Search (38.3%). The average session duration is 73.2 seconds, with users viewing approximately 2.2 pages per session. The most visited page is the homepage, but none of the pages have translated to conversions. The highest engagement is on informational pages and blogs, but these also have not lead to conversions. 

Actionable Recommendations:

Investigate the reasons behind the decrease in active users and sessions.
Amplify marketing efforts in the US and Sweden since they are the top-performing countries.
Resolve any technical issues that may be causing a drop in active users and sessions.
Conduct user surveys to understand user behavior and preferences.
Increase efforts in Organic Social, Cross-Network, and Referral channels to diversify traffic sources.
Improve site navigation and content to increase user engagement, time spent on site, and pages visited per session.
Enhance the homepage content and layout to make it more engaging and reduce its high bounce rate.
Review high traffic but low conversion pages (home, get in touch with us, about us) to ensure clear and appealing calls to action.
Highlight topics of interest to your audience on high traffic pages.
Improve blog content to make it more engaging, shareable, and incorporate a 'Micro Conversion' tracking to understand their role in the conversion journey.
```

### Prerequisites

Before testing, ensure you have:
- ✅ `GA4_PROPERTY_ID` configured
- ✅ `OPENAI_API_KEY` configured  
- ✅ `DISCORD_WEBHOOK_URL` configured
- ✅ Valid Google Analytics data for the last 30 days

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

1. **Edit `bigas/resources/marketing/template_service.py`:**
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
   - Call `service.template_service.run_template_query("my_new_question")` to get the data
   - Or create a dedicated method like `get_my_new_question_data()` in the template service

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