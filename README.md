# 🚀 Bigas - AI-Powered Marketing Analytics Platform

<div align="center">
  <img src="assets/images/bigas-ready-to-serve.png" alt="Bigas Logo" width="200"/>
  <br/>
  <strong>Automated weekly analytics reports with AI-powered insights for solo founders</strong>
</div>

## 📱 Stay Updated

Follow us on X for the latest updates, feature announcements, and marketing insights:
**[@bigasmyaiteam](https://x.com/bigasmyaiteam)**

## 📊 Overview

**Bigas** is an AI team concept designed to provide virtual specialists for different business functions. We've started with our first virtual team member: **The Senior Marketing Analyst**.

### 🎯 Our Goal
To build a comprehensive AI team that can handle various business functions, starting with marketing and expanding to other areas like sales, customer support, product development, and more.

### 🚀 Current Implementation: Senior Marketing Analyst

Bigas is a **comprehensive AI-powered marketing analytics platform** that automatically generates detailed weekly reports from your Google Analytics 4 data and posts them to Discord. It features advanced web scraping capabilities, comprehensive event analysis, and provides concrete, actionable improvement suggestions based on actual page content rather than generic advice.

**What our Senior Marketing Analyst does:**
- 📈 **Weekly Analytics Reports**: Automated GA4 analysis with AI-powered insights and comprehensive marketing analysis
- 🔍 **Advanced Page Performance Analysis**: Identifies underperforming pages considering both conversions AND events, with web scraping for concrete recommendations
- 🎯 **Expert Marketing Recommendations**: Provides specific, actionable improvement suggestions based on actual page content and scraped data
- 📊 **Discord Integration**: Posts detailed reports with executive summaries and expert recommendations directly to your Discord channel
- 🗄️ **Data Storage**: Stores reports in Google Cloud Storage for historical analysis
- 🌐 **Enhanced Web Scraping**: Analyzes actual page content (titles, CTAs, H1 tags) for page-specific optimization suggestions
- 📊 **Event Analysis**: Captures and analyzes website events for comprehensive performance insights
- 🎯 **Structured Recommendations**: Generates exactly 7 AI-powered recommendations (one per question) with facts, actions, categories, priorities, and impact scores

### 🌟 Key Features

#### 🤖 AI-Powered Analysis
- **Senior Marketing Analyst AI**: Get intelligent insights from expert AI, not just raw data
- **Executive Summaries**: Business-focused analysis with key performance trends and business impact
- **Concrete Recommendations**: Specific improvement suggestions with priority rankings, under 80 words each
- **Page-Content-Aware**: Uses actual scraped content (titles, CTAs, H1 tags) for specific optimization suggestions

#### 📊 Comprehensive Analytics
- **Automated Weekly Reports**: Set up once, get comprehensive reports every week or month
- **7 Core Questions**: Traffic sources, engagement, conversions, underperforming pages, and more
- **Event Performance Analysis**: Comprehensive analysis of website events and their impact on conversions
- **Event-Driven Engagement**: Considers events (page_view, scroll, clicks) as positive engagement signals, not just conversions

#### 🌐 Advanced Web Scraping
- **Real Page Content**: Scrapes actual website content for concrete recommendations
- **Element Analysis**: Analyzes titles, CTAs, forms, testimonials, and social proof
- **CTA Identification**: Finds and evaluates call-to-action elements
- **Content Optimization**: Specific content improvement suggestions based on scraped data

#### 🎯 Structured Recommendations
- **7 Focused Recommendations**: One specific recommendation per analytics question
- **Data-Driven Facts**: Each recommendation includes specific metrics from Google Analytics
- **Concrete Actions**: Specific instructions (e.g., "Add 'Contact Us' button in hero section")
- **Priority Ranking**: High/medium/low priority with impact scores
- **No Hallucinations**: All recommendations based on actual data and scraped content

#### 💾 Smart Storage & Integration
- **Google Cloud Storage**: Stores reports with enhanced metadata for historical analysis
- **Discord Integration**: Posts structured reports with summaries and recommendations
- **URL Extraction**: Automatically extracts actual page URLs from GA4 data
- **Cost-Effective**: Minimal storage and API costs (<$0.10/month)

## 🏗️ Architecture

Bigas is built as a **Modular Monolith** with a service-oriented architecture:

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
| |  OpenAI, Discord,    | |  Jira, Figma, etc) | |
| |  Google Cloud Storage)| |                    | |
| +----------------------+ +--------------------+ |
|                                                 |
+-------------------------------------------------+
```

### Service Layer

The marketing analytics functionality is organized into focused services:

- **`GA4Service`**: Handles Google Analytics 4 API interactions and data extraction
- **`OpenAIService`**: Manages OpenAI API calls for comprehensive marketing analysis and executive summary generation
- **`TemplateService`**: Provides template-driven analytics queries including event analysis templates
- **`TrendAnalysisService`**: Orchestrates trend analysis workflows and underperforming page identification
- **`StorageService`**: Manages weekly report storage in Google Cloud Storage with enhanced metadata
- **`WebScrapingService`**: Analyzes actual page content for concrete, page-specific recommendations

### Data Flow

1. **Weekly Reports**: Generated with comprehensive analytics data and stored in Google Cloud Storage
2. **Page Performance Analysis**: Identifies underperforming pages considering both conversions and events
3. **Web Scraping**: Analyzes actual page content (titles, CTAs, H1 tags) for concrete recommendations
4. **AI Marketing Analysis**: Generates executive summary and 7 focused recommendations (one per question) using OpenAI
5. **Discord Integration**: Posts structured reports with executive summaries and expert recommendations
6. **Enhanced Metadata**: Stores comprehensive timestamps and report structure for better organization
7. **Storage Management**: Automatic cleanup of old reports to manage costs

## 🚀 Quick Start

### Prerequisites

Before deploying, you need:
- **Google Cloud CLI** (`gcloud`) installed and authenticated
- **Google Cloud Project** created
- **Cloud Run and Analytics APIs** enabled
- **Service Account** created for Google Analytics 4 access

See the [Google Cloud documentation](https://cloud.google.com/docs) for detailed setup instructions.

### Environment Configuration

Configure your environment variables before deploying:

```bash
# Copy the example environment file
cp env.example .env

# Edit .env and replace ALL dummy values with real credentials
nano .env
```

⚠️ **IMPORTANT**: You must add your actual API keys and values to the `.env` file. The `env.example` file only contains placeholder values.

### Environment Variables

Configure these environment variables for your deployment:

```bash
# Required:
GA4_PROPERTY_ID=your_actual_ga4_property_id
OPENAI_API_KEY=your_actual_openai_api_key
GOOGLE_PROJECT_ID=your_actual_google_project_id
GOOGLE_SERVICE_ACCOUNT_EMAIL=your_actual_service_account_email

# Optional:
DISCORD_WEBHOOK_URL=your_actual_discord_webhook
STORAGE_BUCKET_NAME=your_actual_storage_bucket
TARGET_KEYWORDS=your_actual_keywords
```

### Step-by-Step Setup

#### 1. Set Up Google Cloud Project

1. **Create a Google Cloud Project** (if you don't have one)
2. **Enable required APIs**:
   - Google Analytics Data API
   - Google Cloud Storage API
3. **Create a service account** with the following roles:
   - `roles/analyticsdata.reader` - Read GA4 data
   - `roles/storage.objectAdmin` - Manage storage objects
4. **Download the service account key file**

#### 2. Set Up Google Analytics 4 Property Access

**⚠️ CRITICAL**: You must grant your service account access to your GA4 property for bigas to fetch data.

**Step 1: Get Your GA4 Property ID**
1. Go to [Google Analytics](https://analytics.google.com/)
2. Select your GA4 property
3. Go to **Admin** → **Property details**
4. Copy the **Property ID** (format: `123456789`)

**Step 2: Grant Service Account Access**
1. In Google Analytics, go to **Admin** → **Property Access Management**
2. Click **+** to add a new user
3. **Email**: Enter your service account email
4. **Role**: Select **"Marketer"** (minimum required role for full data access)
5. Click **Add**

**Troubleshooting**: If you get a 403 error, wait longer for permissions to propagate or verify the service account email is correct.

#### 3. Set Up Google Cloud Storage

**⚠️ IMPORTANT**: Google Cloud Storage must be activated and a bucket must be created for report storage.

1. **Activate Google Cloud Storage API** (if not already done in step 1)
2. **Create a storage bucket**:
   ```bash
   # Create a bucket (replace with your preferred name)
   gsutil mb gs://your-bucket-name
   
   # Or use the default name that will be created automatically
   gsutil mb gs://bigas-analytics-reports
   ```
3. **Note the bucket name** - you'll need it for the `STORAGE_BUCKET_NAME` environment variable

#### 4. Configure Environment Variables

Copy the example environment file and configure your variables:

```bash
# Copy the example environment file
cp env.example .env

# Edit the file with your actual values
nano .env
```

**Required environment variables** (see `env.example` for details):
- `GA4_PROPERTY_ID` - Your Google Analytics 4 property ID (found in GA4 Admin → Property Settings)
- `OPENAI_API_KEY` - Your OpenAI API key
- `DISCORD_WEBHOOK_URL` - Your Discord webhook URL
- `STORAGE_BUCKET_NAME` - Your Google Cloud Storage bucket (optional, defaults to 'bigas-analytics-reports')
- `TARGET_KEYWORDS` - Colon-separated list of target keywords for SEO analysis (optional, e.g., "sustainable_swag:eco_friendly_clothing:green_promos")

**⚠️ IMPORTANT**: You must add your actual API keys and values to the `.env` file. The `env.example` file only contains placeholder values.

#### 5. Deploy to Google Cloud Run

```bash
# Clone the repository
git clone https://github.com/your-username/bigas.git
cd bigas

# ⚠️ IMPORTANT: Configure environment variables first!
# Make sure you've set up your .env file with all required variables

# Deploy using the provided script
./deploy.sh
```

#### 6. Get Your First Weekly Report

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report
```

That's it! You'll get a comprehensive AI-powered analysis posted to your Discord channel.

#### 7. Automate Weekly Reports (Recommended)

For automated weekly reports, set up Google Cloud Scheduler:

1. **Go to Google Cloud Console**
   - Navigate to [Cloud Scheduler](https://console.cloud.google.com/cloudscheduler)
   - Select your project

2. **Create a new job**
   - Click "Create Job"
   - **Name**: `weekly-analytics-report`
   - **Region**: Choose your preferred region
   - **Description**: `Automated weekly analytics reports for Bigas`

3. **Configure the schedule**
   - **Frequency**: `0 9 * * 1` (Every Monday at 9 AM)
   - **Timezone**: Choose your timezone (e.g., America/New_York)

4. **Configure the target**
   - **Target type**: HTTP
   - **URL**: `https://your-deployment-url.com/mcp/tools/weekly_analytics_report`
   - **HTTP method**: POST

5. **Save the job**

This will automatically post weekly analytics reports to your Discord channel every Monday at 9 AM.

**Schedule Examples:**
- `0 9 * * 1` - Every Monday at 9 AM
- `0 9 * * 1,4` - Every Monday and Thursday at 9 AM  
- `0 9 1 * *` - First day of every month at 9 AM
- `0 */6 * * *` - Every 6 hours

## 🌐 HTTP API Usage

The core functionality is available via HTTP API, making it easy to integrate with web applications or trigger reports remotely.

### API Endpoints

After deploying to Google Cloud Run, your API will be available at:
```
https://your-service-name-<hash>.a.run.app
```

You can find your service URL by running:
```bash
gcloud run services describe bigas-core --region=your-region --format='value(status.url)'
```

### API Examples

**Note**: Replace `https://your-deployment-url.com` with your actual Cloud Run service URL in the examples below.

#### Generate Weekly Report
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report \
  -H "Content-Type: application/json" \
  -d '{}'
```

#### Get Stored Reports
```bash
curl https://your-deployment-url.com/mcp/tools/get_stored_reports
```

#### Get Latest Report
```bash
curl https://your-deployment-url.com/mcp/tools/get_latest_report
```

#### Analyze Underperforming Pages
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_underperforming_pages \
  -H "Content-Type: application/json" \
  -d '{"max_pages": 3}'
```

#### Cleanup Old Reports
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/cleanup_old_reports \
  -H "Content-Type: application/json" \
  -d '{"keep_days": 30}'
```

#### Ask Analytics Question
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/ask_analytics_question \
  -H "Content-Type: application/json" \
  -d '{"question": "Which country had the most active users last week?"}'
```

#### Analyze Trends
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_trends \
  -H "Content-Type: application/json" \
  -d '{"metric": "active_users", "date_range": "last_30_days"}'
```

#### Custom Reports
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/fetch_custom_report \
  -H "Content-Type: application/json" \
  -d '{
    "dimensions": ["country", "device_category"],
    "metrics": ["active_users", "sessions"],
    "date_ranges": [{"start_date": "2024-01-01", "end_date": "2024-01-31"}]
  }'
```

### API Documentation

Once deployed, access your API documentation at:
- **OpenAPI spec**: `https://your-deployment-url.com/openapi.json`
- **Manifest**: `https://your-deployment-url.com/mcp/manifest`

### Local Development

For local testing, you can run the API server locally:
```bash
# Option 1: Direct Python execution
python run_core.py

# Option 2: Using Flask directly
python app.py

# Option 3: Using gunicorn
gunicorn -w 4 -b 0.0.0.0:8080 app:create_app()
```

Then use `http://localhost:8080` in place of your Cloud Run URL.

## 📊 Storage & Reports

### Storage Architecture

#### Google Cloud Storage Integration
- **Automatic Storage**: Weekly reports are automatically stored in Google Cloud Storage
- **Cost-Effective**: Only stores one report per week, overwriting previous reports
- **Organized Structure**: Reports are stored with date-based organization
- **Metadata Tracking**: Each report includes metadata for easy retrieval and analysis

#### Storage Structure
```
bigas-analytics-reports/
└── weekly_reports/
    ├── 2024-01-15/
    │   └── report.json
    ├── 2024-01-22/
    │   └── report.json
    └── 2024-01-29/
        └── report.json
```

### Enhanced Report Structure

Bigas generates comprehensive, structured reports with multiple components:

#### **Executive Summary**
- **Business-Focused Overview**: Clear summary of key performance trends and business impact
- **Critical Insights**: Top performing areas, biggest opportunities, and key bottlenecks
- **Immediate Actions**: Prioritized action items for the current week
- **Structured Format**: Bullet points and clear language for easy reading

#### **Detailed Q&A Analysis**
- **Comprehensive Coverage**: Answers to 7 key marketing questions
- **Data-Driven Insights**: Specific metrics and trends analysis
- **Traffic Source Analysis**: Organic search, social, referral, and direct traffic insights
- **Page Performance**: Conversion rates, engagement metrics, and underperforming pages
- **Event Analysis**: Website events, trends, and optimization opportunities

#### **Structured Recommendations**
- **7 Focused Recommendations**: One specific recommendation per analytics question
- **Data-Driven Facts**: Each includes specific metrics from Google Analytics
- **Page-Specific Actions**: References exact page URLs and elements
- **Concrete Steps**: Specific instructions for what to change
- **Priority Ranking**: High, medium, and low priority with impact scores

### Structured Recommendations Format

The weekly report generates **one focused recommendation per question** (7 questions = up to 7 recommendations):

```json
{
  "recommendations": [
    {
      "fact": "Direct traffic is 62.5% while organic search is only 25% of total sessions",
      "recommendation": "Create 5 SEO-optimized blog posts targeting key product keywords",
      "category": "seo",
      "priority": "high",
      "impact_score": 8
    },
    {
      "fact": "Homepage has 0 CTA buttons and 48 sessions but 0% conversion rate",
      "recommendation": "Add 'Contact Us' button in hero section and contact form",
      "category": "conversion",
      "priority": "high",
      "impact_score": 9
    }
  ]
}
```

**Recommendation Features:**
- ✅ **One per question**: Each of the 7 analytics questions generates one focused recommendation
- ✅ **Specific numbers**: Every fact includes actual metrics (e.g., "62.5%", "48 sessions", "0 conversions")
- ✅ **Concrete actions**: Recommendations are specific (e.g., "Create 5 SEO-optimized blog posts", not "Improve SEO")
- ✅ **Page-content-aware**: Underperforming pages get recommendations based on actual scraped content

**Page Scraping for Underperforming Pages:**
When the weekly report detects underperforming pages (high traffic, low conversions):
1. **Automatically scrapes** the most visited underperforming page
2. **Analyzes content**: CTAs, forms, testimonials, social proof, H1 tags
3. **Generates specific recommendation**: Based on what's actually missing (e.g., "Homepage has 0 CTA buttons")

**Fact Requirements:**
- ✅ **GOOD**: "Direct traffic is 62.5% while organic search is only 25% of total sessions"
- ✅ **GOOD**: "Homepage has 48 sessions but 0 conversions (0% conversion rate)"
- ✅ **GOOD**: "Homepage has 0 CTA buttons and no testimonials found"
- ❌ **BAD**: "Traffic has been declining" (no specific numbers)
- ❌ **BAD**: "Homepage needs improvement" (too generic)

**Event-Aware Analysis:**
- Events (page_view, scroll, user_engagement, click) are considered **positive engagement signals**
- A page with "zero conversions" but high events may actually have good engagement
- The AI highlights event numbers to show the complete engagement picture

### Automated Workflow Setup

Set up automated jobs for complete weekly analytics workflow:

**Weekly Report Job (Monday 9 AM)**
```bash
# Cloud Scheduler Configuration
Name: weekly-analytics-report
Frequency: 0 9 * * 1
URL: https://your-deployment-url.com/mcp/tools/weekly_analytics_report
Method: POST
```

**Page Analysis Job (Tuesday 10 AM)**
```bash
# Cloud Scheduler Configuration
Name: analyze-underperforming-pages
Frequency: 0 10 * * 2
URL: https://your-deployment-url.com/mcp/tools/analyze_underperforming_pages
Method: POST
```

**Cleanup Job (Monthly)**
```bash
# Cloud Scheduler Configuration
Name: cleanup-old-reports
Frequency: 0 2 1 * *
URL: https://your-deployment-url.com/mcp/tools/cleanup_old_reports
Method: POST
Body: {"keep_days": 30}
```

### Cost Management

#### Storage Costs
- **Google Cloud Storage**: ~$0.02 per GB per month
- **Typical Report Size**: ~15KB per report
- **Monthly Cost**: ~$0.0003 for 30 reports

#### API Costs
- **OpenAI API**: ~$0.03 per analysis (GPT-4)
- **Monthly Cost**: ~$0.06 for 2 analyses per month

#### Total Estimated Cost
- **Storage**: <$0.01/month
- **Analysis**: ~$0.06/month
- **Total**: <$0.10/month

## 🔍 Advanced Features

### Web Scraping & Page Analysis

Bigas includes sophisticated web scraping capabilities for concrete, page-specific recommendations:

#### **Page Content Analysis**
- **Real-Time Scraping**: Analyzes actual website content during report generation
- **Element Extraction**: Captures titles, H1 tags, CTA buttons, and main content
- **Content Optimization**: Provides specific suggestions based on actual page elements
- **Performance Correlation**: Links page content to conversion performance

#### **Scraping Features**
- **Automatic URL Construction**: Builds full URLs from GA4 page paths
- **Content Preview**: Extracts first 500 characters of main content for analysis
- **CTA Identification**: Finds and analyzes call-to-action elements
- **Error Handling**: Graceful fallback if scraping fails

#### **Content Elements Analyzed**
The web scraper extracts and analyzes:
- **Page Title & Meta Description**: SEO and messaging effectiveness
- **Headings Structure**: Content hierarchy and messaging flow
- **Call-to-Action Buttons**: Number, placement, and effectiveness
- **Contact Forms**: Form fields, complexity, and conversion barriers
- **Images & Media**: Visual content and alt text optimization
- **Contact Information**: Phone numbers, emails, and accessibility
- **Social Proof Elements**: Testimonials, reviews, trust signals
- **Page Structure**: Navigation, footer, responsiveness

#### **Use Cases**
- **Button Optimization**: Identify weak CTAs and suggest improvements
- **Content Gaps**: Find missing elements that could improve conversions
- **Page Structure**: Analyze H1 tags, titles, and content hierarchy
- **A/B Test Ideas**: Generate specific test hypotheses based on page content

### URL Extraction & Domain Detection

#### **How It Works**

**1. Weekly Report Generation**
When the weekly report runs, it stores:
- **AI-generated answers** (human-readable insights)
- **Raw GA4 data** (structured data with URLs)

**2. URL Extraction Process**
The system automatically:
1. **Identifies** the underperforming pages question
2. **Extracts** page paths from the raw GA4 data
3. **Converts** paths to full URLs using actual domain from GA4
4. **Calculates** conversion rates and metrics
5. **Flags** underperforming pages

**3. Analysis Enhancement**
The analysis endpoint receives:
- **Specific page URLs** instead of just page names
- **Detailed metrics** for each page
- **Conversion rates** for context

#### **Example Output**

**Before (Page Names Only)**
```
"Are there underperforming pages with high traffic but low conversions?"
Answer: "Yes, the Home page and About Us page have high traffic but no conversions."
```

**After (With URLs and Metrics)**
```json
{
  "underperforming_pages": [
    {
      "question": "Are there underperforming pages with high traffic but low conversions?",
      "answer": "Yes, the Home page and About Us page have high traffic but no conversions."
    }
  ],
  "page_urls": [
    {
      "page_path": "/",
      "hostname": "bigas.com",
      "page_url": "https://bigas.com/",
      "sessions": 39,
      "conversions": 0,
      "conversion_rate": 0.0,
      "is_underperforming": true
    }
  ]
}
```

### Event Analysis

Bigas includes comprehensive website event analysis for better performance insights:

#### **Event Tracking & Analysis**
- **Website Events**: Captures clicks, form submissions, video plays, and custom interactions
- **Performance Trends**: Identifies increasing, decreasing, or stable event patterns
- **Conversion Correlation**: Analyzes relationship between events and conversions
- **Optimization Opportunities**: Flags events that need attention or improvement

#### **Event Metrics**
- **Event Count**: Total number of times each event occurred
- **Unique Users**: Number of distinct users who triggered each event
- **Trend Analysis**: Whether events are increasing, decreasing, or stable
- **Performance Classification**: High, medium, or low priority for optimization

#### **Use Cases**
- **Button Performance**: Track CTA button clicks and identify underperforming elements
- **Form Analytics**: Monitor form submissions and identify drop-off points
- **Content Engagement**: Measure video plays, downloads, and content interactions
- **User Journey**: Understand how users interact with your website

### Underperforming Pages Analysis

The system can analyze underperforming pages from weekly reports and provide expert-level recommendations for improvement.

#### **Features**
- **Expert Digital Marketing Analysis**: Uses an expert Digital Marketing Strategist specializing in CRO, SEO, and UX
- **Comprehensive Page Analysis**: Analyzes actual page content, SEO elements, UX components, and performance indicators
- **Actionable Recommendations**: Provides specific, implementable improvements across three key areas:
  - **Conversion Rate Optimization (CRO)**: Critical issues, CTA optimization, trust building, value proposition, user journey
  - **Search Engine Optimization (SEO)**: On-page SEO, keyword strategy, technical SEO, content quality, internal linking
  - **User Experience (UX)**: Visual hierarchy, mobile experience, page speed, accessibility, user intent alignment
- **Priority Action Plan**: Categorized by High/Medium/Low priority with expected impact and timeline
- **Discord Integration**: Posts detailed analysis to Discord with comprehensive page metrics
- **Error Handling**: Clear guidance when page analysis fails due to technical issues
- **Report Date Tracking**: Shows when the analyzed report was generated

#### **Analysis Includes**
- **Page Content**: Title, meta description, headings, CTAs, forms, text content
- **SEO Elements**: Title/meta length, heading structure, internal/external links, canonical URLs, Open Graph, schema markup
- **UX Elements**: Hero sections, testimonials, pricing, FAQ, newsletter signup, live chat
- **Performance**: Image optimization, link structure, inline styles, external scripts
- **Page Structure**: Navigation, footer, responsiveness, paragraphs, lists, breadcrumbs, search functionality

#### **Target Keywords Feature**
When you configure target keywords in the `TARGET_KEYWORDS` environment variable, the analysis includes:
- **Keyword Presence Analysis**: Checks if keywords appear in title, meta description, and content
- **Keyword Position Analysis**: Identifies where keywords appear in title and meta description
- **Specific SEO Recommendations**: Tailored suggestions for optimizing for your target keywords
- **Content Gap Analysis**: Identifies missing content opportunities for your keywords
- **Competitive Insights**: How well your page targets specific search terms

**Configuration**: Add to your `.env` file:
```bash
TARGET_KEYWORDS=sustainable_swag:eco_friendly_clothing:green_promos
```

#### **Usage**
```bash
# Analyze underperforming pages from the latest report
curl -X POST https://your-server.com/mcp/tools/analyze_underperforming_pages \
  -H "Content-Type: application/json" \
  -d '{"max_pages": 3}'

# Analyze pages from a specific report date
curl -X POST https://your-server.com/mcp/tools/analyze_underperforming_pages \
  -H "Content-Type: application/json" \
  -d '{"report_date": "2024-01-15", "max_pages": 5}'
```

#### **Response**
```json
{
  "status": "success",
  "report_date": "2024-01-15",
  "pages_analyzed": 3,
  "max_pages_limit": 3,
  "discord_posted": true,
  "discord_messages_sent": 4
}
```

#### **Important Notes**
- **Data-Driven Recommendations**: Analysis is only provided when page content can be successfully scraped
- **No Generic Advice**: If page scraping fails, the system provides error guidance instead of generic recommendations
- **Quality Over Quantity**: Ensures all recommendations are based on actual page content for maximum relevance

## 🧪 Testing

### Test Storage Functionality
```bash
python tests/test_storage.py
```

### Test Domain Extraction
```bash
python tests/test_domain_extraction.py
```

### Test Health Check
```bash
python tests/health_check.py
```

## 🔒 Security

### Critical Security Requirements

#### 1. Environment Variables
**NEVER commit sensitive data to version control!**

All sensitive information must be stored as environment variables:

- `GA4_PROPERTY_ID` - Google Analytics 4 Property ID
- `OPENAI_API_KEY` - OpenAI API key
- `DISCORD_WEBHOOK_URL` - Discord webhook URL (optional)
- `GOOGLE_PROJECT_ID` - Google Cloud Project ID
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` - Service account email
- `STORAGE_BUCKET_NAME` - Google Cloud Storage bucket (optional)

#### 2. File Security
- ✅ `.env` files are in `.gitignore`
- ✅ No hardcoded secrets in scripts
- ✅ Service account JSON files are excluded
- ✅ API keys are never logged

#### 3. Access Control
- ✅ Service accounts have minimal required permissions
- ✅ API keys have appropriate scopes
- ✅ HTTPS for all external communications
- ✅ Regular key rotation

### Security Features

#### Input Validation ✅
- All API endpoints validate input parameters
- Date ranges are validated for logical consistency (start before end, not in future, max 2 years)
- Metric/dimension combinations are verified for GA4 compatibility
- Request data size limits (max 10KB per request)
- URL format validation for web scraping
- Content size limits (max 5MB for page analysis)

#### Error Handling ✅
- Sensitive information is never exposed in error messages
- API keys and webhook URLs are automatically redacted
- Graceful degradation when services are unavailable
- Proper HTTP status codes for different error types (400, 404, 429, 500)

#### Rate Limiting ✅
- Simple in-memory rate limiting (100 requests per hour per endpoint)
- Automatic cleanup of old rate limit entries
- HTTP 429 status code for rate limit exceeded
- Per-endpoint rate limiting to prevent abuse

#### Request Validation ✅
- JSON request validation
- Required field checking
- Input sanitization
- Size and length limits
- URL security checks

### Security Checklist

#### Before Committing Code
- [ ] No API keys in code
- [ ] No hardcoded credentials
- [ ] No sensitive URLs in comments
- [ ] `.env` file is not tracked
- [ ] Service account files are excluded

#### Before Deployment
- [ ] Environment variables are set
- [ ] Service account has correct permissions
- [ ] API keys are valid and active
- [ ] HTTPS is used for webhooks
- [ ] Logging excludes sensitive data

#### Regular Maintenance
- [ ] Rotate API keys quarterly
- [ ] Review service account permissions
- [ ] Monitor API usage and costs
- [ ] Update dependencies for security patches
- [ ] Audit access logs

### Incident Response

#### If API Keys are Compromised
1. **Immediately rotate the compromised key**
2. **Check for unauthorized usage**
3. **Review access logs**
4. **Update all environment variables**
5. **Notify relevant stakeholders**

#### If Service Account is Compromised
1. **Disable the service account**
2. **Create a new service account**
3. **Update permissions and environment variables**
4. **Review all access logs**
5. **Audit all resources accessed**

### Security Tools

#### Recommended Tools
- **GitGuardian** - Detect secrets in code
- **Snyk** - Dependency vulnerability scanning
- **Google Cloud Security Command Center** - Cloud security monitoring

#### Code Scanning
```bash
# Check for secrets in code
grep -r "sk-" . --exclude-dir=venv
grep -r "AIza" . --exclude-dir=venv
grep -r "discord.com/api/webhooks" . --exclude-dir=venv
```

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

### Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-username/bigas.git
   cd bigas
   ```

2. **Set up virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

3. **Configure environment**
   ```bash
   cp env.example .env
   # Edit .env with your actual values
   ```

4. **Run tests**
   ```bash
   python tests/test_storage.py
   python tests/test_domain_extraction.py
   ```

## 📞 Support

For support:
1. Check the [Issues](https://github.com/your-username/bigas/issues) page
2. Create a new issue with detailed information
3. Include your environment setup and error messages

### Security Issues

For security issues:
1. **Do not create public issues** for security problems
2. **Contact the maintainer directly** with security concerns
3. **Include detailed information** about the security issue
4. **Provide steps to reproduce** if applicable

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔄 Changelog

### v1.2.0
- ✅ Added web scraping functionality for page content analysis
- ✅ Enhanced underperforming pages analysis with actual page content
- ✅ Improved Discord messaging with one message per page
- ✅ Added specific, actionable suggestions based on real page structure
- ✅ Implemented page content analysis (CTAs, forms, headings, etc.)

### v1.1.0
- ✅ Added Google Cloud Storage integration
- ✅ Implemented URL extraction from GA4 data
- ✅ Added underperforming pages analysis
- ✅ Enhanced AI-powered improvement suggestions
- ✅ Added automatic domain detection from GA4

### v1.0.0
- ✅ Initial release with weekly analytics reports
- ✅ Discord integration
- ✅ Google Analytics 4 integration
- ✅ OpenAI-powered insights

## 🤖 AI Prompts Reference

This section documents all the AI prompts used in Bigas for generating analytics insights and recommendations.

### 📊 Initial Analytics Questions

The weekly report asks these 7 core questions to generate comprehensive insights:

```python
questions = [
  "What are the key trends in our website performance over the last 30 days, including user growth, session patterns, and any significant changes?",
  "What are the primary traffic sources (e.g., organic search, direct, referral, paid search, social, email) contributing to total sessions, and what is their respective share?",
  "What is the average session duration and pages per session across all users?",
  "Which pages are the most visited, and how do they contribute to conversions (e.g., product pages, category pages, blog posts)?",
  "Which pages or sections (e.g., blog, product pages, landing pages) drive the most engagement (e.g., time on page, low bounce rate)?",
  "Are there underperforming pages with high traffic but low conversions?",
  "How do blog posts or content pages contribute to conversions (e.g., assisted conversions, last-click conversions)?"
]
```

### 🎯 Recommendation Generation Prompts

#### 1. Page-Content-Aware Recommendation Prompt

Used when analyzing underperforming pages with actual scraped content:

```python
page_analysis_prompt = f"""
You are an expert Digital Marketing Strategist. Analyze this underperforming page and provide ONE specific recommendation.

Page: {page_path} ({page_name})
Full URL: {underperforming_page_url}
Analytics: {max_sessions} sessions, 0 conversions (0% conversion rate)

Page Content Analysis:
- Title: {page_content_analysis.get('title', 'No title')}
- Meta Description: {page_content_analysis.get('meta_description', 'None')}
- H1 Tags: {page_content_analysis.get('seo_elements', {}).get('h1_count', 0)}
- Forms: {len(page_content_analysis.get('forms', []))}
- Contact Info: {page_content_analysis.get('has_contact_info', False)}
- Testimonials: {page_content_analysis.get('ux_elements', {}).get('has_testimonials', False)}
- Social Proof: {page_content_analysis.get('has_social_proof', False)}

Generate ONE recommendation in this EXACT JSON format:
{{
  "fact": "Page path/name + what is wrong (include numbers: sessions, CTAs, forms, etc.)",
  "recommendation": "Concrete action to fix it (specific and implementable)",
  "category": "conversion",
  "priority": "high"
}}

CRITICAL: The fact MUST start with the page identifier (e.g., "Homepage" or the page path)

Return ONLY the JSON, no explanation.
"""
```

#### 2. General Recommendation Prompt

Used for questions that don't involve specific page analysis:

```python
recommendation_prompt = f"""
You are a marketing analyst. Based on this analytics question and answer, generate ONE specific, actionable recommendation.

Question: {q}

Answer: {answer[:500]}

Raw Data Summary: {str(raw_data)[:300] if raw_data else 'No raw data'}

Generate ONE recommendation in this EXACT JSON format:
{{
  "fact": "Specific finding from the data with actual numbers",
  "recommendation": "Concrete, implementable action (max 80 chars)",
  "category": "traffic|content|conversion|seo|engagement",
  "priority": "high|medium|low"
}}

CRITICAL REQUIREMENTS:
- Include SPECIFIC NUMBERS from the data in the fact (percentages, counts, durations)
- If discussing specific pages, ALWAYS include the page name/path at the start of the fact
- Make recommendation ACTIONABLE and CONCRETE (not generic)
- Keep recommendation under 80 characters

Return ONLY the JSON, no explanation.
"""
```

### 🔍 Comprehensive Page Analysis Prompt

Used for detailed underperforming page analysis with full content scraping:

```python
page_analysis_prompt = f"""
You are an expert Digital Marketing Strategist specializing in Conversion Rate Optimization (CRO), SEO, and User Experience (UX). Your goal is to analyze the provided webpage data and generate actionable recommendations to significantly increase both conversion rates and organic page visits.

Page Details:
- URL: {page.get('page_url', 'Unknown')}
- Sessions: {page.get('sessions', 0)}
- Conversions: {page.get('conversions', 0)}
- Conversion Rate: {page.get('conversion_rate', 0):.1%}
{f"- Target Keywords: {', '.join(target_keywords)}" if target_keywords else ""}

[Full page content analysis data...]

Based on this comprehensive analysis of the actual page content, please provide:

## 🎯 CONVERSION RATE OPTIMIZATION (CRO)
1. **Critical Issues**: What are the top 3-5 conversion killers on this page?
2. **CTA Optimization**: Specific improvements for calls-to-action (placement, copy, design)
3. **Trust & Credibility**: How to build trust and reduce friction
4. **Value Proposition**: How to make the value clearer and more compelling
5. **User Journey**: Optimize the path from visitor to conversion

## 🔍 SEARCH ENGINE OPTIMIZATION (SEO)
1. **On-Page SEO**: Title, meta description, headings, and content optimization
2. **Keyword Strategy**: Target keywords and content gaps
3. **Technical SEO**: Page speed, mobile-friendliness, and technical issues
4. **Content Quality**: How to improve content depth and relevance
5. **Internal Linking**: Opportunities for better site structure

## 👥 USER EXPERIENCE (UX)
1. **Visual Hierarchy**: How to improve content flow and readability
2. **Mobile Experience**: Mobile-specific optimizations
3. **Page Speed**: Performance improvements
4. **Accessibility**: Making the page more accessible
5. **User Intent**: Aligning content with user expectations

## 📊 PRIORITY ACTION PLAN
- **High Priority** (Quick wins with high impact)
- **Medium Priority** (Moderate effort, good impact)
- **Low Priority** (Long-term improvements)

## 🎯 EXPECTED IMPACT
- Specific metrics improvements to expect
- Timeline for implementation
- Resource requirements

Focus on practical, implementable recommendations based on the actual page content.
"""
```

### 🎯 Prompt Design Principles

All prompts follow these key principles:

1. **Specificity**: Always include actual numbers and metrics from the data
2. **Actionability**: Recommendations must be concrete and implementable
3. **Page Context**: When discussing pages, always include the page name/path
4. **Structured Output**: Consistent JSON format for recommendations
5. **Character Limits**: Recommendations kept under 80 characters for conciseness
6. **Category Classification**: Clear categorization (traffic, content, conversion, seo, engagement)
7. **Priority Ranking**: High/medium/low priority classification
8. **Real Content Analysis**: Based on actual scraped page content, not assumptions

### 🔧 Customization

These prompts can be customized by:

- **Target Keywords**: Add specific keywords for SEO analysis
- **Industry Context**: Modify prompts for specific industries
- **Company Size**: Adjust recommendations for solo founders vs. teams
- **Timeline**: Modify implementation timelines based on resources
- **Metrics Focus**: Emphasize different KPIs based on business goals

---

<div align="center">
  <strong>Built with ❤️ for solo founders who need actionable marketing insights</strong>
</div>
