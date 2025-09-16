# 🚀 Bigas - AI-Powered Marketing Analytics Platform

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/Flask-3.0-green.svg)](https://flask.palletsprojects.com/)
[![Google Analytics 4](https://img.shields.io/badge/GA4-API-orange.svg)](https://developers.google.com/analytics/devguides/reporting/data/v1)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4-purple.svg)](https://platform.openai.com/docs)
[![MCP Compatible](https://img.shields.io/badge/MCP-2025%20Compliant-green.svg)](https://modelcontextprotocol.io/)

**Automated weekly analytics reports with AI-powered insights for solo founders**

## 📱 Stay Updated

Follow us on X for the latest updates, feature announcements, and marketing insights: **@bigasmyaiteam**

## 📊 Overview

**Bigas** is an AI team concept designed to provide virtual specialists for different business functions. We've started with our first virtual team member: **The Marketing Specialist**.

### 🎯 Our Goal

To build a comprehensive AI team that can handle various business functions, starting with marketing and expanding to other areas like sales, customer support, product development, and more.

### 🚀 Current Implementation: Virtual Marketing Specialist

Bigas is now an **AI-powered marketing analytics platform** that automatically generates comprehensive weekly reports with actionable insights. It combines Google Analytics 4 data with OpenAI's GPT-4 to provide intelligent marketing recommendations.

## 🔌 Model Context Protocol (MCP) Integration

**✅ Fully MCP 2025 Compliant Server** - Ready for AI client integration

Bigas Core implements the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) specification, making it compatible with MCP-enabled AI clients like Claude and other LLM applications.

### MCP Configuration

```json
{
    "manifestUrl": "https://mcp-marketing-919623369853.europe-north1.run.app/mcp/manifest",
    "openapiUrl": "https://mcp-marketing-919623369853.europe-north1.run.app/openapi.json"
}
```

### MCP Features
- **10 MCP Tools**: Complete analytics toolkit accessible via natural language
- **JSON-RPC 2.0**: Standard MCP transport protocol
- **Secure HTTPS**: Production-ready with proper authentication
- **Tool Categories**: Analytics queries, report generation, storage management, page optimization

### Quick MCP Integration
1. Add the above configuration to your MCP client
2. Access tools via natural language: *"Generate a weekly analytics report"*
3. Use storage tools: *"Get the latest stored report"* 
4. Analyze pages: *"Find underperforming pages and suggest improvements"*

> **Note**: Live analytics tools require application context. Storage and report analysis tools work directly via MCP.

## 🏗️ Architecture Overview

```mermaid
graph TB
    subgraph "External Services"
        GA4[Google Analytics 4]
        OpenAI[OpenAI GPT-4]
        GCS[Google Cloud Storage]
        Discord[Discord Webhooks]
    end
    
    subgraph "Bigas Core API"
        App[Flask App<br/>app.py]
        
        subgraph "Marketing Resource"
            ME[Marketing Endpoints<br/>endpoints.py]
            MS[Marketing Service<br/>service.py]
            
            subgraph "Core Services"
                GA4S[GA4 Service<br/>ga4_service.py]
                OAIS[OpenAI Service<br/>openai_service.py]
                TS[Template Service<br/>template_service.py]
                TAS[Trend Analysis Service<br/>trend_analysis_service.py]
                SS[Storage Service<br/>storage_service.py]
                EAS[Enhanced AI Service<br/>enhanced_ai_service.py]
            end
        end
        
        subgraph "Product Resource"
            PE[Product Endpoints<br/>endpoints.py]
        end
        
        subgraph "Utilities"
            Utils[Utils<br/>utils.py]
        end
    end
    
    subgraph "Data Flow"
        Query[Natural Language Query] --> ME
        ME --> MS
        MS --> GA4S
        GA4S --> GA4
        GA4 --> GA4S
        GA4S --> OAIS
        OAIS --> OpenAI
        OpenAI --> OAIS
        OAIS --> SS
        SS --> GCS
        ME --> Discord
    end
```

## 🚀 Core Features

### 📊 **Natural Language Analytics**
Transform questions like "What are my top traffic sources?" into structured GA4 queries and receive AI-powered insights.

### 📈 **Automated Trend Analysis**
Compare current and previous periods with intelligent trend detection and business impact analysis.

### 📄 **Weekly Reports**
Comprehensive automated reports with 7 predefined analytics questions, stored in Google Cloud Storage.

### 🔍 **Underperforming Page Analysis**
AI-powered deep dive into pages with high traffic but low conversions, including content scraping and optimization recommendations.

### ⚡ **Real-time Caching**
1-hour intelligent caching system to optimize GA4 API usage and response times.

### 🔒 **Enterprise Security**
Rate limiting, input validation, and secure credential management for production environments.

## 🎯 Enhanced Underperforming Page Analysis

Our most advanced feature provides **specific, actionable improvement suggestions** based on real page content rather than generic advice.

### How It Works

#### 1. Page Content Analysis

When analyzing underperforming pages, the system:

* **Scrapes the actual page content** using web scraping technology
* **Analyzes page structure** including titles, headings, CTAs, forms, and content
* **Identifies specific issues** based on the actual page content
* **Provides concrete suggestions** tailored to what's actually on the page

#### 2. Content Elements Analyzed

The web scraper extracts and analyzes:

* **Page Title & Meta Description**: SEO and messaging effectiveness
* **Headings Structure**: Content hierarchy and messaging flow
* **Call-to-Action Buttons**: Number, placement, and effectiveness
* **Contact Forms**: Form fields, complexity, and conversion barriers
* **Images & Media**: Visual content and alt text optimization
* **Contact Information**: Phone numbers, emails, and accessibility
* **Social Proof Elements**: Testimonials, reviews, trust signals
* **Page Structure**: Navigation, footer, responsiveness

#### 3. Specific Analysis Output

Instead of generic advice, you get specific recommendations like:

* **"Add a prominent CTA button after the 'About Our Services' heading"**
* **"Simplify the contact form by removing the 'Company Size' field"**
* **"Add customer testimonials after the pricing section"**
* **"Include a phone number in the header for immediate contact"**

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

## 📡 API Endpoints

### Marketing Analytics Endpoints

| Endpoint | Method | Description | Function |
|----------|--------|-------------|----------|
| `/mcp/tools/fetch_analytics_report` | POST | Standard GA4 report with basic metrics | `fetch_analytics_report()` |
| `/mcp/tools/fetch_custom_report` | POST | Custom GA4 report with specified dimensions/metrics | `fetch_custom_report()` |
| `/mcp/tools/ask_analytics_question` | POST | Natural language query processing | `ask_analytics_question()` |
| `/mcp/tools/analyze_trends` | POST | Trend analysis with AI insights | `analyze_trends()` |
| `/mcp/tools/weekly_analytics_report` | POST | Generate comprehensive weekly report | `weekly_analytics_report()` |
| `/mcp/tools/get_stored_reports` | GET | List all stored weekly reports | `get_stored_reports()` |
| `/mcp/tools/get_latest_report` | GET | Retrieve most recent weekly report | `get_latest_report()` |
| `/mcp/tools/analyze_underperforming_pages` | POST | Deep analysis of underperforming pages | `analyze_underperforming_pages()` |
| `/mcp/tools/cleanup_old_reports` | POST | Clean up old stored reports | `cleanup_old_reports()` |

### System Endpoints

| Endpoint | Method | Description | Function |
|----------|--------|-------------|----------|
| `/mcp/manifest` | GET | Combined MCP manifest | `combined_manifest()` |
| `/openapi.json` | GET | OpenAPI specification | `openapi_spec()` |

### Product Management Endpoints

| Endpoint | Method | Description | Function |
|----------|--------|-------------|----------|
| `/mcp/tools/product_resource_placeholder` | POST | Placeholder for future product management features | `product_placeholder()` |

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

### Running All Tests
```bash
# Core functionality tests
python tests/test_storage.py
python tests/test_domain_extraction.py

# Health check
python tests/health_check.py

# Integration tests
python tests/test_client.py
```

### Available Test Modules

- `test_storage.py`: Google Cloud Storage integration testing
- `test_domain_extraction.py`: Domain and URL processing validation
- `test_client.py`: API endpoint integration testing
- `health_check.py`: System health validation
- `auto_fix_test_runner.py`: Automated test execution with fixes

## 🔒 Security

### Critical Security Requirements

#### 1. Environment Variables

**NEVER commit sensitive data to version control!**

All sensitive information must be stored as environment variables:

* `GA4_PROPERTY_ID` - Google Analytics 4 Property ID
* `OPENAI_API_KEY` - OpenAI API key
* `DISCORD_WEBHOOK_URL` - Discord webhook URL (optional)
* `GOOGLE_PROJECT_ID` - Google Cloud Project ID
* `GOOGLE_SERVICE_ACCOUNT_EMAIL` - Service account email
* `STORAGE_BUCKET_NAME` - Google Cloud Storage bucket (optional)

#### 2. File Security

* ✅ `.env` files are in `.gitignore`
* ✅ No hardcoded secrets in scripts
* ✅ Service account JSON files are excluded
* ✅ API keys are never logged

#### 3. Access Control

* ✅ Service accounts have minimal required permissions
* ✅ API keys have appropriate scopes
* ✅ HTTPS for all external communications
* ✅ Regular key rotation

### Security Checklist

#### Before Committing Code

* No API keys in code
* No hardcoded credentials
* No sensitive URLs in comments
* `.env` file is not tracked
* Service account files are excluded

#### Before Deployment

* Environment variables are set
* Service account has correct permissions
* API keys are valid and active
* HTTPS is used for webhooks
* Logging excludes sensitive data

#### Regular Maintenance

* Rotate API keys quarterly
* Review service account permissions
* Monitor API usage and costs
* Update dependencies for security patches
* Audit access logs

### Security Features

#### Input Validation ✅

* All API endpoints validate input parameters
* Date ranges are validated for logical consistency (start before end, not in future, max 2 years)
* Metric/dimension combinations are verified for GA4 compatibility
* Request data size limits (max 10KB per request)
* URL format validation for web scraping
* Content size limits (max 5MB for page analysis)

#### Error Handling ✅

* Sensitive information is never exposed in error messages
* API keys and webhook URLs are automatically redacted
* Graceful degradation when services are unavailable
* Proper HTTP status codes for different error types (400, 404, 429, 500)

#### Rate Limiting ✅

* Simple in-memory rate limiting (100 requests per hour per endpoint)
* Automatic cleanup of old rate limit entries
* HTTP 429 status code for rate limit exceeded
* Per-endpoint rate limiting to prevent abuse

#### Request Validation ✅

* JSON request validation
* Required field checking
* Input sanitization
* Size and length limits
* URL security checks

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

* **GitGuardian** - Detect secrets in code
* **Snyk** - Dependency vulnerability scanning
* **Google Cloud Security Command Center** - Cloud security monitoring

#### Code Scanning

```bash
# Check for secrets in code
grep -r "sk-" . --exclude-dir=venv
grep -r "AIza" . --exclude-dir=venv
grep -r "discord.com/api/webhooks" . --exclude-dir=venv
```

## 🤝 Contributing

We welcome contributions! Please see our Contributing Guide for details.

### Development Setup

1. **Clone the repository**  
   ```bash
   git clone https://github.com/mckort/bigas.git  
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

1. Check the Issues page
2. Create a new issue with detailed information
3. Include your environment setup and error messages

### Security Issues

For security issues:

1. **Do not create public issues** for security problems
2. **Contact the maintainer directly** with security concerns
3. **Include detailed information** about the security issue
4. **Provide steps to reproduce** if applicable

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🔄 Changelog

### v1.2.0

* ✅ Added web scraping functionality for page content analysis
* ✅ Enhanced underperforming pages analysis with actual page content
* ✅ Improved Discord messaging with one message per page
* ✅ Added specific, actionable suggestions based on real page structure
* ✅ Implemented page content analysis (CTAs, forms, headings, etc.)

### v1.1.0

* ✅ Added Google Cloud Storage integration
* ✅ Implemented URL extraction from GA4 data
* ✅ Added underperforming pages analysis
* ✅ Enhanced AI-powered improvement suggestions
* ✅ Added automatic domain detection from GA4

### v1.0.0

* ✅ Initial release with weekly analytics reports
* ✅ Discord integration
* ✅ Google Analytics 4 integration
* ✅ OpenAI-powered insights

---

**Built with ❤️ for solo founders who need actionable marketing insights**

## About

Bigas means team in latin. The purpose of this open source project is to create a virtual team of resources to assist solo founders. The resources could be any, starting with marketing. The intent is to keep it simple, for non-professional developers to be able to contribute.

### Resources

- [Readme](README.md)
- [Contributing](CONTRIBUTING.md)

---

**Version**: 1.1  
**Last Updated**: September 2025  
**Compatibility**: Google Analytics 4, OpenAI GPT-4, Python 3.11+, MCP 2025