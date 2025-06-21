# Bigas - Your Virtual AI Team for Solo Entrepreneurs

Welcome to Bigas, an open-source project hosted on GitHub, designed to empower solo entrepreneurs by providing a virtual team of AI-powered resources. The name Bigas is derived from Latin, meaning "team," and reflects our mission to create a collaborative ecosystem of AI tools to support entrepreneurs in building and scaling their ventures. Our vision is to make advanced AI capabilities accessible to individuals who are navigating the entrepreneurial journey solo, enabling them to achieve more with less.

## Project Overview

Bigas (available at bigas.me, meaning "my team") is a modular, open-source platform that leverages cutting-edge AI tools to simulate the expertise of a dedicated team. By integrating with coding tools like Cursor, Bigas enables solo entrepreneurs to streamline workflows, automate tasks, and gain actionable insights without the need for extensive technical expertise or a large team.

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

## 📡 API Examples

The Bigas Marketing Analytics Server provides a RESTful API for accessing Google Analytics data. Here are some examples of how to use the key endpoints:

### Natural Language Analytics Questions

Ask questions about your analytics data in plain English:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/ask_analytics_question \
  -H "Content-Type: application/json" \
  -d '{"question": "Which country had the most active users last week?"}'
```

This endpoint uses AI to interpret your question and return relevant analytics data in a natural, conversational format.

### Weekly Analytics Reports

Generate comprehensive weekly reports and post them to Discord:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report \
  -H "Content-Type: application/json"
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
  }'
```

### Standard Analytics Reports

Get predefined analytics reports:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/fetch_analytics_report \
  -H "Content-Type: application/json" \
  -d '{"date_range": "last_7_days"}'
```

### Trend Analysis

Analyze data trends over time:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_trends \
  -H "Content-Type: application/json" \
  -d '{"metric": "active_users", "date_range": "last_30_days"}'
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

## 🛠️ Installation

To get started with Bigas, visit our GitHub repository for installation instructions, documentation, and contribution guidelines. Explore the Marketing Analytics Resource to see how it can transform your approach to data-driven decision-making. Join our community to collaborate, share ideas, and help shape the future of Bigas.

Together, let's build a virtual team that empowers solo entrepreneurs to achieve their biggest goals. Welcome to Bigas—your team, your success!

## 📄 License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

### What the MIT License Allows You to Do:

✅ **Commercial Use**: Use this software for commercial purposes  
✅ **Modification**: Modify the source code to suit your needs  
✅ **Distribution**: Distribute the original or modified code  
✅ **Private Use**: Use the software privately  
✅ **Sublicensing**: Include the software in your own projects  

### MIT License Requirements:

📋 **Attribution**: You must include the original copyright notice and license text in any copy or substantial portion of the software.

### What This Means for Bigas:

- **Freedom to Deploy**: Deploy Bigas on any platform (Google Cloud Run, AWS, Azure, your own servers, etc.)
- **Freedom to Modify**: Customize the code to add new features or integrate with other services
- **Freedom to Distribute**: Include Bigas in your own projects or distribute modified versions
- **Commercial Freedom**: Use Bigas in commercial applications without restrictions
- **Attribution Required**: Include the MIT license text and copyright notice when redistributing

### Example Attribution:

If you distribute or modify Bigas, include this in your project:

```
Bigas Marketing Analytics Server
Copyright (c) 2025 [Your Name/Organization]

This software is based on Bigas Marketing Analytics Server
Copyright (c) 2025 Marcus Friman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details on how to submit pull requests, report issues, and contribute to the project.

---

# Google Analytics MCP Server (Bigas Marketing)

A comprehensive Flask-based Google Analytics 4 (GA4) MCP server with natural language analytics capabilities, Discord integration, and advanced reporting features. Deployed on Google Cloud Run with full API documentation.

## 🚀 Features

- **Natural Language Analytics**: Ask questions in plain English and get AI-powered insights
- **Weekly Analytics Reports**: Automated Q&A reports posted to Discord
- **Advanced GA4 Integration**: Comprehensive metric and dimension mapping
- **OpenAPI Documentation**: Full API specification at `/openapi.json`
- **Discord Integration**: Automated posting of analytics insights
- **Smart Caching**: Optimized performance with intelligent caching
- **Error Handling**: Robust error handling and fallback mechanisms
- **GA4 Compatibility**: Handles GA4's metric/dimension compatibility restrictions

## 🏗️ Architecture Overview

The solution consists of:
- **Flask Application** with MCP framework integration
- **Analytics Agent** with OpenAI integration for natural language processing
- **Google Analytics Data API** integration with comprehensive field mapping
- **Discord Webhook** integration for automated reporting
- **Docker containerization** for easy deployment
- **Google Cloud Run** deployment with optimized configuration

## 📋 Prerequisites

1. **Google Cloud Platform (GCP)** project
2. **Google Analytics 4** property with data
3. **Service account** with access to the GA4 property
4. **OpenAI API key** for natural language processing
5. **Discord webhook URL** (optional, for automated reporting)
6. **Docker** installed locally
7. **Google Cloud SDK (gcloud)** installed locally

## 🔐 Security & Environment Setup

### ⚠️ IMPORTANT: Security First!

**NEVER commit sensitive information to version control!** This includes:
- API keys
- Service account credentials
- Property IDs
- Webhook URLs
- Any other secrets

### 1. Environment Variables Setup

1. **Copy the example environment file:**
   ```bash
   cp env.example .env
   ```

2. **Edit `.env` with your actual values:**
   ```bash
   # Google Analytics 4 Configuration
   GA4_PROPERTY_ID=your_actual_ga4_property_id
   
   # OpenAI Configuration
   OPENAI_API_KEY=your_actual_openai_api_key
   
   # Discord Integration (Optional)
   DISCORD_WEBHOOK_URL=your_actual_discord_webhook_url
   
   # Google Cloud Configuration
   GOOGLE_PROJECT_ID=your_actual_google_cloud_project_id
   GOOGLE_SERVICE_ACCOUNT_EMAIL=your_actual_service_account_email
   
   # Application Configuration
   FLASK_ENV=production
   FLASK_DEBUG=false
   PORT=5000
   ```

3. **Verify `.env` is in `.gitignore`:**
   ```bash
   # Check that .env is listed in .gitignore
   grep -n "\.env" .gitignore
   ```

### 2. Required Environment Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| `GA4_PROPERTY_ID` | Your Google Analytics 4 Property ID | ✅ | `473559548` |
| `OPENAI_API_KEY` | Your OpenAI API key | ✅ | `sk-proj-...` |
| `GOOGLE_PROJECT_ID` | Your Google Cloud Project ID | ✅ | `my-project-123` |
| `GOOGLE_SERVICE_ACCOUNT_EMAIL` | Service account email | ✅ | `analytics@project.iam.gserviceaccount.com` |
| `DISCORD_WEBHOOK_URL` | Discord webhook for reports | ❌ | `https://discord.com/api/webhooks/...` |

### 3. Service Account Setup

1. Create a service account in your GCP project
2. Download the service account key (JSON file)
3. Grant the service account access to your GA4 property:
   - Go to GA4 Admin
   - Navigate to Property Access Management
   - Add the service account email
   - Grant at least "Viewer" permissions

### 4. Local Development

1. **Set up your environment:**
   ```bash
   # Copy example file
   cp env.example .env
   
   # Edit with your values
   nano .env
   
   # Install dependencies
   pip install -r requirements.txt
   ```

2. **Run the application:**
   ```bash
   python bigas_marketing.py
   ```

### 5. Deployment

**Using the deployment script:**
```bash
# Make sure .env is set up
./deploy.sh
```

**Manual deployment:**
```bash
# Set environment variables
export GA4_PROPERTY_ID=your_property_id
export OPENAI_API_KEY=your_api_key
export GOOGLE_PROJECT_ID=your_project_id
export GOOGLE_SERVICE_ACCOUNT_EMAIL=your_service_account

# Deploy
gcloud run deploy bigas-marketing \
  --image europe-north1-docker.pkg.dev/your-project-id/your-docker-repo/your-image-name:latest \
  --platform managed \
  --region europe-north1 \
  --memory 2Gi \
  --timeout 300 \
  --concurrency 1 \
  --set-env-vars GA4_PROPERTY_ID=$GA4_PROPERTY_ID,OPENAI_API_KEY=$OPENAI_API_KEY,DISCORD_WEBHOOK_URL=$DISCORD_WEBHOOK_URL
```

## 🔒 Security Best Practices

> 📖 **For detailed security information, see [SECURITY.md](SECURITY.md)**

### ✅ Do's
- ✅ Use `.env` files for local development
- ✅ Set environment variables in production
- ✅ Use service accounts with minimal permissions
- ✅ Rotate API keys regularly
- ✅ Monitor API usage and costs
- ✅ Use HTTPS for all webhook URLs

### ❌ Don'ts
- ❌ Never commit `.env` files to git
- ❌ Never hardcode secrets in scripts
- ❌ Never share API keys publicly
- ❌ Never use admin/service accounts with excessive permissions
- ❌ Never log sensitive information

### 🔍 Security Checklist
- [ ] `.env` file exists and contains all required variables
- [ ] `.env` is listed in `.gitignore`
- [ ] No secrets in `deploy.sh` or other scripts
- [ ] Service account has minimal required permissions
- [ ] API keys are valid and have appropriate scopes
- [ ] Webhook URLs use HTTPS
- [ ] Environment variables are set in production

### 🚨 Security Documentation
- **[SECURITY.md](SECURITY.md)** - Detailed security guide with incident response procedures
- **Environment Setup** - See section above for environment variable configuration
- **Deployment Security** - Use `deploy.sh` script which validates environment variables

## 📊 API Endpoints

### 1. Fetch Analytics Report
**Endpoint:** `POST /mcp/tools/fetch_analytics_report`

Fetches a Google Analytics report with intelligent field mapping and compatibility handling.

**Parameters:**
- `start_date`: (string, optional, default: 30 days ago)
- `end_date`: (string, optional, default: today)
- `metrics`: (list of strings, optional, default: `["activeUsers"]`)
- `dimensions`: (list of strings, optional, default: `["country"]`)

### 2. Fetch Custom Report
**Endpoint:** `POST /mcp/tools/fetch_custom_report`

Fetches custom reports with multiple date ranges for comparison.

**Parameters:**
- `dimensions`: (list of dimension names)
- `metrics`: (list of metric names)
- `date_ranges`: (list of dicts with `start_date`, `end_date`, and optional `name`)

### 3. Ask Analytics Question ⭐
**Endpoint:** `POST /mcp/tools/ask_analytics_question`

**NEW**: Natural language analytics with AI-powered insights and recommendations.

**Parameters:**
- `question`: (string, required) - Ask any analytics question in plain English

**Example Questions:**
- "What are the primary traffic sources contributing to total sessions?"
- "Which landing pages have the highest bounce rates?"
- "How do blog posts contribute to conversions?"
- "Are there pages with high traffic but low conversions?"

**Features:**
- Intelligent metric/dimension selection
- GA4 compatibility handling
- Human-readable analysis
- Actionable recommendations

### 4. Analyze Trends
**Endpoint:** `POST /mcp/tools/analyze_trends`

Analyzes trends across multiple time frames with period-over-period comparisons.

**Parameters:**
- `metrics`: (list of strings, optional)
- `dimensions`: (list of strings, optional)
- `time_frames`: (list of objects, optional)

### 5. Weekly Analytics Report ⭐
**Endpoint:** `POST /mcp/tools/weekly_analytics_report`

**NEW**: Automated weekly analytics Q&A that posts results to Discord and returns a comprehensive summary.

**Features:**
- Runs 8 predefined analytics questions
- Posts each Q&A to Discord
- Generates AI-powered summary recommendations
- Handles empty results gracefully
- Fallback mechanisms for reliability

**Required Environment Variables:**
- `DISCORD_WEBHOOK_URL`
- `OPENAI_API_KEY`

### 6. OpenAPI Specification
**Endpoint:** `GET /openapi.json`

**NEW**: Complete OpenAPI 3.0 specification for all endpoints.

### 7. MCP Manifest
**Endpoint:** `GET /mcp/manifest`

Returns the MCP tool manifest for Cursor CLI integration.

## 🧠 Analytics Agent Features

### Natural Language Processing
- **Smart Query Parsing**: Converts natural language to GA4 API parameters
- **Field Mapping**: Automatically maps deprecated GA3 fields to GA4 equivalents
- **Compatibility Handling**: Ensures metric/dimension combinations are valid
- **Error Recovery**: Fallback mechanisms for failed queries

### Supported Analysis Types

#### Traffic Source Analysis
- **Metrics**: `["sessions", "totalUsers"]`
- **Dimensions**: `["sessionDefaultChannelGroup", "source", "medium"]`
- **Use Case**: Understanding which channels drive traffic

#### Bounce Rate Analysis
- **Metrics**: `["bounceRate", "sessions", "totalUsers"]`
- **Dimensions**: `["landingPage", "pageTitle"]`
- **Use Case**: Identifying problematic landing pages

#### Exit Rate Analysis
- **Metrics**: `["screenPageViews", "sessions", "totalUsers", "userEngagementDuration"]`
- **Dimensions**: `["pagePath", "pageTitle"]`
- **Use Case**: Finding pages where users leave

#### Content Performance Analysis
- **Metrics**: `["conversions", "sessions", "totalUsers"]`
- **Dimensions**: `["pagePath", "pageTitle", "sessionDefaultChannelGroup"]`
- **Use Case**: Understanding content contribution to conversions

### Field Mapping
The system automatically maps deprecated or invalid fields:

**Metrics:**
- `pageViews` → `screenPageViews`
- `exitRate` → `bounceRate`
- `averageUserEngagementDuration` → `userEngagementDuration`

**Dimensions:**
- `landingPagePath` → `landingPage`
- `pageCategory` → `itemCategory`
- `attributionModel` → `sessionDefaultChannelGroup`

## 🔄 Weekly Analytics Report

The weekly report runs 8 predefined questions:

1. **Traffic Sources**: Primary traffic sources and their share
2. **Bounce Rate**: Overall bounce rate and landing page performance
3. **Session Duration**: Average session duration and pages per session
4. **Page Performance**: Most visited pages and conversion contribution
5. **Exit Rates**: Pages with high exit rates in conversion funnel
6. **Engagement**: Pages driving most engagement
7. **Underperforming Pages**: High traffic, low conversion pages
8. **Content Contribution**: How blog posts contribute to conversions

**Output:**
- Individual Q&A posts to Discord
- AI-generated summary recommendations
- JSON response with all results

## 🛠️ Technical Features

### Caching
- **In-Memory Caching**: 1-hour cache duration for API responses
- **Smart Cache Keys**: Based on query parameters and date ranges
- **Performance Optimization**: Reduces redundant GA4 API calls

### Error Handling
- **Graceful Degradation**: Fallback mechanisms for failed queries
- **Detailed Logging**: Comprehensive error tracking and debugging
- **User-Friendly Messages**: Clear error explanations
- **Retry Logic**: Automatic retry for transient failures

### Discord Integration
- **Webhook Support**: Automated posting to Discord channels
- **Message Truncation**: Handles Discord's 2000 character limit
- **Error Handling**: Graceful handling of Discord API failures
- **Formatting**: Rich text formatting for better readability

### GA4 Compatibility
- **Metric/Dimension Validation**: Ensures compatible combinations
- **Field Mapping**: Handles GA4 vs GA3 field differences
- **API Limits**: Respects GA4 API quotas and limits
- **Data Validation**: Validates responses before processing

## 📈 Performance & Monitoring

### Logging
- **Structured Logging**: JSON-formatted logs for easy parsing
- **Debug Information**: Detailed query parameter logging
- **Error Tracking**: Comprehensive error logging with stack traces
- **Performance Metrics**: Query execution time tracking

### Monitoring
- **Health Checks**: Endpoint availability monitoring
- **Error Rates**: Track and alert on error rates
- **Response Times**: Monitor API performance
- **Cache Hit Rates**: Track caching effectiveness

## 🔒 Security

- **Service Account Authentication**: Secure GA4 access
- **Environment Variables**: Sensitive data stored securely
- **API Key Management**: Secure OpenAI API key handling
- **Input Validation**: Sanitized user inputs
- **Rate Limiting**: Protection against abuse

## 🚀 Deployment

### Cloud Run Configuration
```bash
gcloud run deploy bigas-marketing \
  --image europe-north1-docker.pkg.dev/your-project-id/your-docker-repo/your-image-name:tag \
  --platform managed \
  --region europe-north1 \
  --memory 2Gi \
  --timeout 300 \
  --concurrency 1 \
  --set-env-vars GA4_PROPERTY_ID=your_property_id,OPENAI_API_KEY=your_api_key,DISCORD_WEBHOOK_URL=your_discord_webhook_url
```

### Environment Variables
```bash
GA4_PROPERTY_ID=your_ga4_property_id
OPENAI_API_KEY=your_openai_api_key
DISCORD_WEBHOOK_URL=your_discord_webhook_url
```

## 📝 Deployment Script (`deploy.sh`)

This project includes a `deploy.sh` script to automate the build and deployment process to Google Cloud Run.

**What the script does:**
- Builds the Docker image for the MCP server.
- Pushes the image to Google Container Registry.
- Deploys the service to Google Cloud Run with the required environment variables.

**How to use:**
1. **Set the required environment variables** (do not hardcode secrets in the script!):
   - `GA4_PROPERTY_ID` – Your GA4 property ID.
   - `OPENAI_API_KEY` – Your OpenAI API key.
   - `DISCORD_WEBHOOK_URL` – (Optional) Discord webhook for report posting.
   - `GOOGLE_PROJECT_ID` – Your Google Cloud project ID.
   - `REGION` – The GCP region for deployment (e.g., `europe-north1`).

2. **Run the script:**
   ```bash
   ./deploy.sh
   ```

**Note:**
- The script expects you to be authenticated with `gcloud` and have Docker installed.
- Never commit real secrets or keys to version control.

## 🧪 Automated Testing & Debugging

This project includes advanced automated testing and debugging tools that can automatically identify and fix issues in your MCP server.

### Dynamic Test Client (`dynamic_test_client.py`)

A comprehensive MCP client that automatically discovers and tests all available endpoints.

**Features:**
- **Manifest Discovery**: Automatically fetches the MCP manifest to discover all tools
- **Dynamic Test Generation**: Creates appropriate test data for each parameter
- **Comprehensive Coverage**: Tests all endpoints with proper HTTP methods
- **Smart Data Generation**: Intelligently generates test data based on parameter names and descriptions
- **Detailed Reporting**: Real-time feedback with success rates and error details

**Usage:**
```bash
# Basic usage (assumes server at localhost:5000)
python dynamic_test_client.py

# Custom server URL
python dynamic_test_client.py --url http://your-server:8080

# Custom timeout
python dynamic_test_client.py --timeout 60
```

**Example Output:**
```
🚀 Dynamic MCP Test Client
==================================================
Fetching MCP manifest from http://localhost:5000/mcp/manifest...
✓ Successfully fetched manifest with 5 tools

📋 Found 5 tools to test:
   • fetch_analytics_report (POST /mcp/tools/fetch_analytics_report)
   • fetch_custom_report (POST /mcp/tools/fetch_custom_report)
   • ask_analytics_question (POST /mcp/tools/ask_analytics_question)
   • analyze_trends (POST /mcp/tools/analyze_trends)
   • weekly_analytics_report (POST /mcp/tools/weekly_analytics_report)

🧪 Running tests...
   ✅ fetch_analytics_report: SUCCESS
   ✅ ask_analytics_question: SUCCESS
   ❌ weekly_analytics_report: ERROR

📊 TEST SUMMARY
==================================================
Total Tools Tested: 5
Overall Success Rate: 80.0%
```

### Auto-Fix Test Runner (`auto_fix_test_runner.py`) ⭐

**NEW**: An intelligent automated debugging system that can automatically fix issues and re-run tests until everything works.

**Features:**
- **Automated Error Detection**: Captures and analyzes test failures
- **Cursor CLI Integration**: Uses Cursor CLI to automatically apply fixes
- **Iterative Fix Process**: Runs tests → Analyzes failures → Applies fixes → Re-runs tests
- **Intelligent Prompt Generation**: Creates detailed prompts for Cursor based on actual errors
- **Comprehensive Logging**: Complete audit trail of all fixes applied
- **Success Tracking**: Continues until 95%+ success rate or max attempts reached

**Prerequisites:**
1. **Cursor CLI**: Must be installed and available in PATH
   ```bash
   # Install Cursor from https://cursor.sh/
   # Verify installation
   cursor --version
   ```
2. **Server Running**: Your Flask server must be running
3. **Files Present**: Both `dynamic_test_client.py` and `bigas_marketing.py` must exist

**Usage:**
```bash
# Basic usage (5 attempts, localhost:5000)
python auto_fix_test_runner.py

# Custom configuration
python auto_fix_test_runner.py \
  --url http://your-server:8080 \
  --max-attempts 10 \
  --wait-time 15
```

**How It Works:**
1. **Health Check**: Verifies server is running
2. **Test Run**: Executes dynamic test client
3. **Analysis**: Parses output for errors and success rates
4. **Success Check**: If ≥95% success, stops
5. **Fix Generation**: Creates detailed prompt for Cursor CLI
6. **Auto-Fix**: Applies fixes using Cursor CLI
7. **Restart Wait**: Waits for server to restart
8. **Repeat**: Goes back to step 2 until success or max attempts

**Example Output:**
```
🤖 Automated Test Runner with Auto-Fix
============================================================
Target Server: http://localhost:5000
Max Attempts: 5
============================================================

🔄 Attempt 1/5
============================================================
📊 Analysis:
   Success Rate: 60.0%
   Failed Tests: 2

🔧 Attempting to fix 2 failures...

🔧 Applying automatic fixes with Cursor CLI...
Running: cursor chat --file bigas_marketing.py --prompt-file fix_prompt_20241201_143022.txt
✅ Cursor CLI fix applied successfully

⏳ Waiting 10 seconds for server to restart...

🔄 Attempt 2/5
============================================================
...
🎉 SUCCESS! All tests are passing!
```

**Benefits:**
- **Zero Manual Intervention**: Fully automated debugging
- **Intelligent Fixes**: Context-aware error resolution
- **Comprehensive Coverage**: Tests all endpoints automatically
- **Detailed Logging**: Complete audit trail of fixes applied
- **Flexible Configuration**: Customizable timeouts and attempts

**Output Files:**
- **JSON Results**: Detailed results saved to `auto_fix_results_YYYYMMDD_HHMMSS.json`
- **Fix History**: Complete log of all fixes applied
- **Performance Metrics**: Timing and success rate tracking

### Testing Workflow

**Recommended Testing Workflow:**
1. **Manual Testing**: Use `test_client.py` for quick manual tests
2. **Comprehensive Testing**: Use `dynamic_test_client.py` for full endpoint coverage
3. **Automated Debugging**: Use `auto_fix_test_runner.py` for automated issue resolution

**Integration with Development:**
```bash
# After making changes to bigas_marketing.py
python auto_fix_test_runner.py --max-attempts 3

# For continuous integration
python dynamic_test_client.py --url $PRODUCTION_URL
```

## 🔮 Future Improvements

- **Persistent Caching**: Redis integration for distributed caching
- **Advanced Analytics**: Machine learning insights and predictions
- **Custom Dashboards**: Web-based analytics dashboard
- **Real-time Monitoring**: Live analytics streaming
- **Multi-Property Support**: Support for multiple GA4 properties
- **Advanced Filtering**: Complex filter combinations
- **Export Features**: CSV/Excel export capabilities
- **Scheduled Reports**: Automated report generation and delivery

## 📚 API Documentation

For complete API documentation, visit `/openapi.json` after deployment.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details. 