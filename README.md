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

Bigas is now a **comprehensive AI-powered marketing analytics platform** that automatically generates detailed weekly reports from your Google Analytics 4 data and posts them to Discord. It features advanced web scraping capabilities, comprehensive event analysis, and provides concrete, actionable improvement suggestions based on actual page content rather than generic advice.

**What our Senior Marketing Analyst does:**
- 📈 **Weekly Analytics Reports**: Automated GA4 analysis with AI-powered insights and comprehensive marketing analysis
- 🔍 **Advanced Page Performance Analysis**: Identifies underperforming pages considering both conversions AND events, with web scraping for concrete recommendations
- 🎯 **Expert Marketing Recommendations**: Provides specific, actionable improvement suggestions based on actual page content and scraped data
- 📊 **Discord Integration**: Posts detailed reports with executive summaries and expert recommendations directly to your Discord channel
- 🗄️ **Data Storage**: Stores reports in Google Cloud Storage for historical analysis
- 🌐 **Enhanced Web Scraping**: Analyzes actual page content (titles, CTAs, H1 tags) for page-specific optimization suggestions
- 📊 **Event Analysis**: Captures and analyzes website events for comprehensive performance insights
- 🎯 **Structured Recommendations**: Generates exactly 8 AI-powered recommendations with facts, actions, categories, priorities, and impact scores

### Key Features
- 🤖 **AI-Powered Marketing Analysis**: Get intelligent insights from senior marketing analyst AI, not just raw data
- 📅 **Automated Weekly Reports**: Set up once, get comprehensive reports every week or month with executive summaries
- 🔗 **Advanced URL Extraction**: Automatically extracts actual page URLs from GA4 data and scrapes content for analysis
- 💾 **Smart Storage**: Stores reports in Google Cloud Storage for analysis with enhanced metadata
- 🎯 **Concrete Actionable Insights**: Specific improvement suggestions with priority rankings, under 50 words each
- 🌐 **Enhanced Page Content Analysis**: Advanced web scraping to analyze actual page content (titles, CTAs, H1 tags) and provide page-specific optimization suggestions
- 📊 **Event Performance Analysis**: Comprehensive analysis of website events and their impact on conversions
- 🎯 **Business-Focused Recommendations**: Focus on quick wins that can be implemented within 2 weeks
- 🎯 **Structured AI Recommendations**: Exactly 8 AI-generated recommendations with facts, actions, categories, priorities, and impact scores
- 📊 **Event-Driven Engagement Analysis**: Considers events (page_view, scroll, clicks) as positive engagement signals, not just conversions
- 💰 **Cost-Effective**: Minimal storage and API costs (<$0.10/month)

## 🚀 Quick Start & Deployment

### Environment Configuration

Bigas-core supports two deployment modes with automatic credential handling:

#### 1. **SaaS Deployment (Recommended for most users)**
```bash
# Copy the example environment file
cp env.example .env

# Deploy with dummy placeholders (will be overridden by SaaS UI)
./deploy.sh
```
- ✅ **No configuration needed** - dummy values work for deployment
- ✅ **SaaS UI handles real credentials** at runtime
- ✅ **Perfect for SaaS integration**

#### 2. **Standalone Deployment**
```bash
# Copy the example environment file
cp env.example .env

# Set deployment mode to standalone
echo "DEPLOYMENT_MODE=standalone" >> .env

# Edit .env and replace ALL dummy values with real credentials
nano .env

# Deploy with real credentials
./deploy.sh
```
- ⚠️ **Requires real credentials** in .env file
- ✅ **Works independently** without SaaS UI
- 🔧 **Full control** over all settings
- 🎯 **Uses environment variables** for all company-specific settings

### Deployment Modes

Bigas-core automatically adapts its behavior based on the `DEPLOYMENT_MODE` environment variable:

#### **SaaS Mode (`DEPLOYMENT_MODE=saas`)**
- **Default mode** - works out of the box
- **Company-specific credentials** come from SaaS UI at runtime
- **Core infrastructure** (OpenAI, Google Cloud) comes from environment
- **Dummy placeholders** are acceptable for company-specific values
- **Perfect for** SaaS integration and multi-tenant deployments

#### **Standalone Mode (`DEPLOYMENT_MODE=standalone`)**
- **Company-specific credentials** come from environment variables
- **Core infrastructure** (OpenAI, Google Cloud) comes from environment
- **Real credentials required** for all company-specific values
- **Perfect for** solo usage, custom integrations, or single-tenant deployments

### Environment Variables

The `env.example` file contains dummy placeholder values that work for SaaS deployment. For standalone usage, you must replace them with real credentials:

```bash
# Required for standalone usage:
GA4_PROPERTY_ID=your_actual_ga4_property_id
OPENAI_API_KEY=your_actual_openai_api_key
GOOGLE_PROJECT_ID=your_actual_google_project_id
GOOGLE_SERVICE_ACCOUNT_EMAIL=your_actual_service_account_email

# Optional:
DISCORD_WEBHOOK_URL=your_actual_discord_webhook
STORAGE_BUCKET_NAME=your_actual_storage_bucket
TARGET_KEYWORDS=your_actual_keywords
```

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
- **`TemplateService`**: Provides template-driven analytics queries including new event analysis templates
- **`TrendAnalysisService`**: Orchestrates trend analysis workflows and underperforming page identification
- **`StorageService`**: Manages weekly report storage in Google Cloud Storage with enhanced metadata
- **`WebScrapingService`**: Analyzes actual page content for concrete, page-specific recommendations

### Data Flow

1. **Weekly Reports**: Generated with comprehensive analytics data and stored in Google Cloud Storage
2. **Page Performance Analysis**: Identifies underperforming pages considering both conversions and events
3. **Web Scraping**: Analyzes actual page content (titles, CTAs, H1 tags) for concrete recommendations
4. **AI Marketing Analysis**: Generates executive summary and 5 prioritized, actionable recommendations using OpenAI
5. **Discord Integration**: Posts structured reports with executive summaries and expert recommendations
6. **Enhanced Metadata**: Stores comprehensive timestamps and report structure for better organization
7. **Storage Management**: Automatic cleanup of old reports to manage costs

## 🌐 HTTP API Usage

The core functionality is available via HTTP API, making it easy to integrate with web applications or trigger reports remotely.

### Start the API Server
```bash
# Option 1: Direct Python execution
python run_core.py

# Option 2: Using Flask directly
python app.py

# Option 3: Using gunicorn for production
gunicorn -w 4 -b 0.0.0.0:8080 app:create_app()
```

### API Endpoints

#### Generate Weekly Report
```bash
curl -X POST http://localhost:8080/mcp/tools/weekly_analytics_report \
  -H "Content-Type: application/json" \
  -d '{}'
```

#### Get Stored Reports
```bash
curl http://localhost:8080/mcp/tools/get_stored_reports
```

#### Get Latest Report
```bash
curl http://localhost:8080/mcp/tools/get_latest_report
```

#### API Documentation
- OpenAPI spec: http://localhost:8080/openapi.json
- Manifest: http://localhost:8080/mcp/manifest

## 🚀 Quick Start

### 1. Prerequisites Setup

#### Google Cloud Setup
Before deploying, you need to set up Google Cloud:

- Install and authenticate with Google Cloud CLI (`gcloud`)
- Create a Google Cloud project
- Enable Cloud Run and Analytics APIs
- Create a service account for Google Analytics 4 access

See the [Google Cloud documentation](https://cloud.google.com/docs) for detailed setup instructions.

#### Google Analytics Authentication
You need to authenticate with Google Analytics 4:

- Create a service account in your Google Cloud project
- Grant the service account "Viewer" permissions to your GA4 property
- Download the service account key file

### 1. Set Up Google Cloud Project

1. **Create a Google Cloud Project** (if you don't have one)
2. **Enable required APIs**:
   - Google Analytics Data API
   - Google Cloud Storage API
3. **Create a service account** with the following roles:
   - `roles/analyticsdata.reader` - Read GA4 data
   - `roles/storage.objectAdmin` - Manage storage objects
4. **Download the service account key file**

### 2. Set Up Google Analytics 4 Property Access

**⚠️ CRITICAL**: You must grant your service account access to your GA4 property for bigas to fetch data.

#### **Step 1: Get Your GA4 Property ID**
1. Go to [Google Analytics](https://analytics.google.com/)
2. Select your GA4 property
3. Go to **Admin** → **Property details**
4. Copy the **Property ID** (format: `123456789`)

#### **Step 2: Grant Service Account Access**
1. In Google Analytics, go to **Admin** → **Property Access Management**
2. Click **+** to add a new user
3. **Email**: Enter your service account email (if using Google Cloud)
4. **Role**: Select **"Marketer"** (minimum required role for full data access)
5. Click **Add**

**Troubleshooting**: If you get a 403 error, wait longer for permissions to propagate or verify the service account email is correct.

### 3. Set Up Google Cloud Storage

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

### 4. Configure Environment Variables

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

### 5. Enhanced AI Capabilities

Bigas now features a **senior marketing analyst AI** that provides comprehensive, actionable insights:

#### **Executive Summary Generation**
- **Business-Focused Analysis**: Clear, structured summaries highlighting key performance trends and business impact
- **Critical Insights**: Identifies top performing areas, biggest opportunities, and key bottlenecks
- **Immediate Actions**: Prioritized action items for the current week

#### **Concrete Marketing Recommendations**
- **Page-Specific Actions**: Each recommendation references exact page URLs and elements to change
- **Actionable Steps**: Specific instructions for what to change (button text, H1 tags, CTA placement)
- **Quick Wins Focus**: Prioritized recommendations that can be implemented within 2 weeks
- **50-Word Limit**: Concise, focused recommendations under 50 words each

#### **Advanced Web Scraping & Content Analysis**
- **Real Page Content**: Scrapes actual website content (titles, CTAs, H1 tags, main content)
- **Page-Specific Optimization**: Provides concrete suggestions based on actual page elements
- **CTA Analysis**: Identifies and analyzes call-to-action elements for improvement
- **Content Optimization**: Specific content improvement suggestions based on scraped data

#### **Enhanced Event Analysis**
- **Website Events**: Captures and analyzes website events (clicks, form submissions, etc.)
- **Performance Metrics**: Tracks event trends and identifies optimization opportunities
- **Conversion Correlation**: Analyzes relationship between events and conversions

#### **Structured AI Recommendations System**
- **8 Recommendations**: AI generates precisely 8 actionable recommendations per report
- **Data-Driven Facts**: Each recommendation includes specific metrics from Google Analytics (no hallucinations)
- **Structured Format**: Consistent structure with fact, recommendation, category, priority, and impact score
- **Event-Aware Analysis**: Considers events as positive engagement signals, not just e-commerce conversions
- **Page-Specific Actions**: Uses web scraping data to provide concrete, actionable page improvements

### 6. Deploy to Google Cloud Run

```bash
# Clone the repository
git clone https://github.com/your-username/bigas.git
cd bigas

# ⚠️ IMPORTANT: Configure environment variables first!
# Make sure you've set up your .env file with all required variables
# See step 2 above for details on required environment variables

# Deploy using the provided script
./deploy.sh
```

### 7. Get Your First Weekly Report

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report
```

That's it! You'll get a comprehensive AI-powered analysis posted to your Discord channel.

### 8. Automate Weekly Reports (Recommended)

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

### 9. Structured Recommendations Format

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

### 10. Analyze Underperforming Pages (New!)

The weekly reports are now automatically stored in Google Cloud Storage, enabling you to analyze underperforming pages and get AI-powered improvement suggestions:

#### Set up a second Cloud Scheduler job for page analysis:

1. **Create another Cloud Scheduler job**
   - **Name**: `analyze-underperforming-pages`
   - **Frequency**: `0 10 * * 2` (Every Tuesday at 10 AM, after the weekly report)
   - **URL**: `https://your-deployment-url.com/mcp/tools/analyze_underperforming_pages`
   - **HTTP method**: POST

This will automatically analyze the latest weekly report, identify underperforming pages, and generate specific improvement suggestions.

#### Manual analysis:

```bash
# Get a list of all stored reports
curl -X GET https://your-deployment-url.com/mcp/tools/get_stored_reports

# Get the latest report with summary
curl -X GET https://your-deployment-url.com/mcp/tools/get_latest_report

# Analyze underperforming pages from the latest report
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_underperforming_pages

# Analyze underperforming pages from a specific date
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_underperforming_pages \
  -H "Content-Type: application/json" \
  -d '{"report_date": "2024-01-15"}'

# Clean up old reports (keep last 30 days)
curl -X POST https://your-deployment-url.com/mcp/tools/cleanup_old_reports \
  -H "Content-Type: application/json" \
  -d '{"keep_days": 30}'
```

The analysis will provide:
- **Priority-ranked improvements** (High/Medium/Low)
- **Effort estimates** (Quick/Easy/Complex)
- **Expected impact** for each suggestion
- **Specific action items** you can implement immediately
- **Actual page URLs** for direct access to underperforming pages

## 🔗 URL Extraction & Domain Detection

### Overview

The enhanced storage system automatically extracts actual page URLs from Google Analytics data, making underperforming pages analysis much more actionable.

### How It Works

#### 1. Weekly Report Generation
When the weekly report runs, it now stores:
- **AI-generated answers** (human-readable insights)
- **Raw GA4 data** (structured data with URLs)

#### 2. URL Extraction Process
The system automatically:
1. **Identifies** the underperforming pages question
2. **Extracts** page paths from the raw GA4 data
3. **Converts** paths to full URLs using actual domain from GA4
4. **Calculates** conversion rates and metrics
5. **Flags** underperforming pages

#### 3. Analysis Enhancement
The analysis endpoint now receives:
- **Specific page URLs** instead of just page names
- **Detailed metrics** for each page
- **Conversion rates** for context

### Example Output

#### Before (Page Names Only)
```
"Are there underperforming pages with high traffic but low conversions?"
Answer: "Yes, the Home page and About Us page have high traffic but no conversions."
```

#### After (With URLs and Metrics)
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
    },
    {
      "page_path": "/about-us",
      "hostname": "bigas.com",
      "page_url": "https://bigas.com/about-us",
      "sessions": 10,
      "conversions": 0,
      "conversion_rate": 0.0,
      "is_underperforming": true
    }
  ]
}
```

### Enhanced Report Structure

Bigas now generates comprehensive, structured reports with multiple components:

#### **Executive Summary**
- **Business-Focused Overview**: Clear summary of key performance trends and business impact
- **Critical Insights**: Top performing areas, biggest opportunities, and key bottlenecks
- **Immediate Actions**: Prioritized action items for the current week
- **Structured Format**: Bullet points and clear language for easy reading

#### **Detailed Q&A Analysis**
- **Comprehensive Coverage**: Answers to 8 key marketing questions
- **Data-Driven Insights**: Specific metrics and trends analysis
- **Traffic Source Analysis**: Organic search, social, referral, and direct traffic insights
- **Page Performance**: Conversion rates, engagement metrics, and underperforming pages
- **Event Analysis**: Website events, trends, and optimization opportunities

#### **Concrete Marketing Recommendations**
- **5 Prioritized Actions**: High, medium, and low priority recommendations
- **Page-Specific**: Each recommendation references exact page URLs and elements
- **Actionable Steps**: Specific instructions for what to change
- **Quick Wins**: Focus on actions that can be implemented within 2 weeks

### AI Analysis Enhancement

With URLs, the AI can now provide much more specific suggestions:

#### Before
```
"Improve the homepage to increase conversions."
```

#### After
```
"**Homepage (https://bigas.com/) - 39 sessions, 0 conversions**
- Priority: High
- Effort: Quick
- Impact: High
- Actions:
  1. Add prominent CTA button above the fold
  2. Include customer testimonials
  3. Add trust signals (certifications, reviews)
  4. Optimize page load speed
  5. A/B test different headlines
```

### Technical Implementation

#### Data Flow
1. **GA4 Query** → Gets page paths and hostname from GA4
2. **Storage** → Stores raw data alongside AI answers
3. **Extraction** → Converts paths to URLs using actual domain
4. **Analysis** → Uses URLs in AI prompts for specific suggestions
5. **Output** → Provides clickable URLs in results

#### URL Construction
- **Automatic Domain Detection**: Extracts hostname from GA4 `hostName` dimension
- **Relative paths** (e.g., `/about-us`) → `https://actualdomain.com/about-us`
- **Absolute URLs** → Used as-is
- **Fallback** → Uses path if hostname not available

## 📊 Storage Features

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

### API Endpoints

#### 1. Enhanced Weekly Analytics Report
**Endpoint**: `POST /mcp/tools/weekly_analytics_report`

**New Features**:
- **Comprehensive AI Analysis**: Senior marketing analyst AI generates executive summary and 5 prioritized recommendations
- **Advanced Web Scraping**: Analyzes actual page content for concrete, page-specific suggestions
- **Event Analysis**: Captures and analyzes website events for performance insights
- **Enhanced Metadata**: Comprehensive timestamps and report structure
- **Structured Output**: Separate fields for summary, recommendations, and detailed analysis
- **Discord Integration**: Posts structured reports with executive summaries and expert recommendations

**Response**:
```json
{
  "status": "Weekly report process completed and sent to Discord.",
  "stored": true,
  "storage_path": "weekly_reports/2024-01-29/report.json"
}
```

#### 2. Get Stored Reports
**Endpoint**: `GET /mcp/tools/get_stored_reports`

**Purpose**: List all available weekly reports

**Response**:
```json
{
  "status": "success",
  "reports": [
    {
      "date": "2024-01-29",
      "blob_name": "weekly_reports/2024-01-29/report.json",
      "size": 15420,
      "updated": "2024-01-29T10:00:00Z"
    }
  ],
  "total_reports": 1
}
```

#### 3. Get Latest Report
**Endpoint**: `GET /mcp/tools/get_latest_report`

**Purpose**: Retrieve the most recent weekly report with summary

#### 4. Analyze Underperforming Pages
**Endpoint**: `POST /mcp/tools/analyze_underperforming_pages`

**Purpose**: AI-powered analysis of underperforming pages with improvement suggestions

#### 5. Cleanup Old Reports
**Endpoint**: `POST /mcp/tools/cleanup_old_reports`

**Purpose**: Manage storage costs by deleting old reports

### AI-Powered Analysis Features

#### Underperforming Pages Analysis
The system can analyze underperforming pages from weekly reports and provide expert-level recommendations for improvement.

### Features:
- **Expert Digital Marketing Analysis**: Uses an expert Digital Marketing Strategist specializing in CRO, SEO, and UX
- **Comprehensive Page Analysis**: Analyzes actual page content, SEO elements, UX components, and performance indicators
- **Actionable Recommendations**: Provides specific, implementable improvements across three key areas:
  - **Conversion Rate Optimization (CRO)**: Critical issues, CTA optimization, trust building, value proposition, user journey
  - **Search Engine Optimization (SEO)**: On-page SEO, keyword strategy, technical SEO, content quality, internal linking
  - **User Experience (UX)**: Visual hierarchy, mobile experience, page speed, accessibility, user intent alignment
- **Priority Action Plan**: Categorized by High/Medium/Low priority with expected impact and timeline
- **Discord Integration**: Posts detailed analysis to Discord with comprehensive page metrics
- **Error Handling**: Clear guidance when page analysis fails due to technical issues
- **Report Date Tracking**: Shows when the analyzed report was generated, not when analysis runs

### Analysis Includes:
- **Page Content**: Title, meta description, headings, CTAs, forms, text content
- **SEO Elements**: Title/meta length, heading structure, internal/external links, canonical URLs, Open Graph, schema markup
- **UX Elements**: Hero sections, testimonials, pricing, FAQ, newsletter signup, live chat
- **Performance**: Image optimization, link structure, inline styles, external scripts
- **Page Structure**: Navigation, footer, responsiveness, paragraphs, lists, breadcrumbs, search functionality

### Important Notes:
- **Data-Driven Recommendations**: Analysis is only provided when page content can be successfully scraped
- **No Generic Advice**: If page scraping fails, the system provides error guidance instead of generic recommendations
- **Quality Over Quantity**: Ensures all recommendations are based on actual page content for maximum relevance
- **Target Keywords**: Optional parameter for specific SEO optimization based on your target search terms

### Target Keywords Feature:
When you configure target keywords in the `TARGET_KEYWORDS` environment variable, the analysis includes:
- **Keyword Presence Analysis**: Checks if keywords appear in title, meta description, and content
- **Keyword Position Analysis**: Identifies where keywords appear in title and meta description
- **Specific SEO Recommendations**: Tailored suggestions for optimizing for your target keywords
- **Content Gap Analysis**: Identifies missing content opportunities for your keywords
- **Competitive Insights**: How well your page targets specific search terms vs. competitors

**Configuration**: Add to your `.env` file:
```bash
TARGET_KEYWORDS=sustainable_swag:eco_friendly_clothing:green_promos
```

### Usage:
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

**Note**: Target keywords are configured via the `TARGET_KEYWORDS` environment variable and will be automatically included in all analyses.

### Response:
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

### Setup Instructions

#### 1. Google Cloud Permissions
Ensure your service account has:
- `storage.objects.create` - Create storage objects
- `storage.objects.get` - Read storage objects
- `storage.objects.list` - List storage objects
- `storage.objects.delete` - Delete storage objects (for cleanup)

#### 2. Automated Workflow Setup

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

## 🔧 Additional Features

While the weekly report is the main feature, Bigas also provides:

### Advanced Web Scraping & Content Analysis

Bigas now includes sophisticated web scraping capabilities for concrete, page-specific recommendations:

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

#### **Use Cases**
- **Button Optimization**: Identify weak CTAs and suggest improvements
- **Content Gaps**: Find missing elements that could improve conversions
- **Page Structure**: Analyze H1 tags, titles, and content hierarchy
- **A/B Test Ideas**: Generate specific test hypotheses based on page content

### Natural Language Analytics Questions

Ask specific questions about your data:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/ask_analytics_question \
  -H "Content-Type: application/json" \
  -d '{"question": "Which country had the most active users last week?"}'
```

### Trend Analysis

Analyze data trends over time:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_trends \
  -H "Content-Type: application/json" \
  -d '{"metric": "active_users", "date_range": "last_30_days"}'
```

### Custom Reports

Create tailored reports with specific metrics:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/fetch_custom_report \
  -H "Content-Type: application/json" \
  -d '{
    "dimensions": ["country", "device_category"],
    "metrics": ["active_users", "sessions"],
    "date_ranges": [{"start_date": "2024-01-01", "end_date": "2024-01-31"}]
  }'
```

### Enhanced Event Analysis

Bigas now includes comprehensive website event analysis for better performance insights:

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

## 🌐 Enhanced Page Analysis with Web Scraping

### Overview

Bigas now includes advanced web scraping capabilities to analyze the actual content of underperforming pages and provide specific, actionable improvement suggestions based on real page content rather than generic advice.

### How It Works

#### 1. Page Content Analysis
When analyzing underperforming pages, the system:
- **Scrapes the actual page content** using web scraping technology
- **Analyzes page structure** including titles, headings, CTAs, forms, and content
- **Identifies specific issues** based on the actual page content
- **Provides concrete suggestions** tailored to what's actually on the page

#### 2. Content Elements Analyzed
The web scraper extracts and analyzes:
- **Page Title & Meta Description**: SEO and messaging effectiveness
- **Headings Structure**: Content hierarchy and messaging flow
- **Call-to-Action Buttons**: Number, placement, and effectiveness
- **Contact Forms**: Form fields, complexity, and conversion barriers
- **Images & Media**: Visual content and alt text optimization
- **Contact Information**: Phone numbers, emails, and accessibility
- **Social Proof Elements**: Testimonials, reviews, trust signals
- **Page Structure**: Navigation, footer, responsiveness

#### 3. Specific Analysis Output
Instead of generic advice, you get specific recommendations like:
- **"Add a prominent CTA button after the 'About Our Services' heading"**
- **"Simplify the contact form by removing the 'Company Size' field"**
- **"Add customer testimonials after the pricing section"**
- **"Include a phone number in the header for immediate contact"**

### Example Enhanced Analysis

#### Before (Generic Advice)
```
"Improve the homepage to increase conversions."
```

#### After (Content-Specific)
```
**Page Analysis: https://bigas.com/**

**Issues Found:**
- Only 2 CTA buttons found (both in footer)
- No contact information visible above the fold
- Missing customer testimonials or social proof
- Contact form has 6 fields (too many for conversion)

**Specific Action Items:**
1. Add "Get Started" CTA button after the main headline
2. Move phone number from footer to header
3. Add customer testimonials after the "Why Choose Us" section
4. Reduce contact form to 3 essential fields
5. Add trust badges near the pricing section
```

### Benefits

1. **Actionable Insights**: Specific suggestions based on actual page content
2. **No Generic Advice**: Every recommendation is tailored to your specific pages
3. **Implementation Ready**: Clear, specific action items you can implement immediately
4. **Content-Aware**: Understands your actual messaging and page structure
5. **Conversion-Focused**: Recommendations based on proven conversion optimization principles

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

---

<div align="center">
  <strong>Built with ❤️ for solo founders who need actionable marketing insights</strong>
</div>