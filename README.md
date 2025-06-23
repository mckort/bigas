<div align="center">
  <img src="assets/images/bigas-ready-to-serve.png" alt="Bigas Logo" width="200">
</div>

# Bigas - AI Team for Solo Founders, Built for Vibe Coding

Welcome to Bigas, an open-source project hosted on GitHub, designed to empower solo entrepreneurs by providing a virtual team of AI-powered resources. The name Bigas is derived from Latin, meaning "team," and reflects our mission to create a collaborative ecosystem of AI tools to support entrepreneurs in building and scaling their ventures. Our vision is to make advanced AI capabilities accessible to individuals who are navigating the entrepreneurial journey solo, enabling them to achieve more with less.

## Project Overview

Bigas (meaning "my team") is a modular, open-source platform that leverages cutting-edge AI tools to simulate the expertise of a dedicated team. By integrating with coding tools like Cursor, Bigas enables solo entrepreneurs to streamline workflows, automate tasks, and gain actionable insights without the need for extensive technical expertise or a large team.

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

**Integration with Coding Tools**: Utilizes tools like Cursor to enable rapid development, customization, and automation for users who want to extend or tailor the platform.

**Open-Source Collaboration**: Hosted on GitHub, Bigas invites developers, data scientists, and entrepreneurs to contribute to its growth, ensuring a community-driven approach to innovation.

## 🚀 Features

- **Analytics Reporting**: Get comprehensive Google Analytics reports with customizable date ranges
- **Custom Reports**: Create tailored reports with specific dimensions and metrics
- **Natural Language Queries**: Ask analytics questions in plain English
- **Trend Analysis**: Analyze data trends over time with detailed insights
- **Weekly Reports**: Automated weekly analytics reports posted to Discord
- **Q&A Summaries**: AI-powered summaries of weekly report Q&A sections
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

## 🛠️ Installation & Deployment

Getting started with Bigas is straightforward. The primary method for deployment is using the provided `deploy.sh` script, which automates the process of building and deploying the application to Google Cloud Run.

### Quick Start with `