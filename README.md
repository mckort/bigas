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

Bigas is an AI-powered marketing analytics platform that automatically generates comprehensive weekly reports from your Google Analytics 4 data and posts them to Discord. It identifies underperforming pages and provides specific, actionable improvement suggestions.

### Key Features
- 🤖 **AI-Powered Analysis**: Get intelligent insights, not just raw data
- 📅 **Automated Weekly Reports**: Set up once, get reports every Monday
- 🔗 **URL Extraction**: Automatically extracts actual page URLs from GA4 data
- 💾 **Smart Storage**: Stores reports in Google Cloud Storage for analysis
- 🎯 **Actionable Insights**: Specific improvement suggestions with priority rankings
- 🌐 **Page Content Analysis**: Web scraping to analyze actual page content and provide concrete suggestions
- 💰 **Cost-Effective**: Minimal storage and API costs (<$0.10/month)

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

- **`GA4Service`**: Handles Google Analytics 4 API interactions
- **`OpenAIService`**: Manages OpenAI API calls for analysis and summarization
- **`TemplateService`**: Provides template-driven analytics queries
- **`TrendAnalysisService`**: Orchestrates trend analysis workflows
- **`StorageService`**: Manages weekly report storage in Google Cloud Storage

### Data Flow

1. **Weekly Reports**: Generated and stored in Google Cloud Storage
2. **Analysis Jobs**: Retrieve stored reports to analyze underperforming pages
3. **AI Insights**: Generate improvement suggestions using OpenAI
4. **Discord Integration**: Post results to Discord for easy access
5. **Storage Management**: Automatic cleanup of old reports to manage costs

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

### 2. Configure Environment Variables

Copy the example environment file and configure your variables:

```bash
# Copy the example environment file
cp env.example .env

# Edit the file with your actual values
nano .env
```

Required environment variables (see `env.example` for details):
- `GA4_PROPERTY_ID` - Your Google Analytics 4 property ID
- `OPENAI_API_KEY` - Your OpenAI API key
- `DISCORD_WEBHOOK_URL` - Your Discord webhook URL
- `STORAGE_BUCKET_NAME` - Your Google Cloud Storage bucket (optional, defaults to 'bigas-analytics-reports')

### 3. Deploy to Google Cloud Run

```bash
# Clone the repository
git clone https://github.com/your-username/bigas-marketing.git
cd bigas-marketing

# Deploy using the provided script
./deploy.sh
```

### 4. Get Your First Weekly Report

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report
```

That's it! You'll get a comprehensive AI-powered analysis posted to your Discord channel.

### 5. Automate Weekly Reports (Recommended)

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

### 6. Analyze Underperforming Pages (New!)

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

**Changes**:
- Now stores complete report data in Google Cloud Storage
- Returns storage confirmation and path
- Maintains Discord integration

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
   git clone https://github.com/your-username/bigas-marketing.git
   cd bigas-marketing
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
1. Check the [Issues](https://github.com/your-username/bigas-marketing/issues) page
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