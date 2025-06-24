<div align="center">
  <img src="assets/images/bigas-ready-to-serve.png" alt="Bigas Logo" width="200">
</div>

# Bigas - AI Team for Solo Founders, Built for Vibe Coding

Welcome to Bigas, an open-source project designed to empower solo entrepreneurs with AI-powered analytics. The name Bigas is derived from Latin, meaning "team," and reflects our mission to create a collaborative ecosystem of AI tools to support entrepreneurs in building and scaling their ventures.

## 🎯 Key Feature: Weekly Analytics Reports

**The main function of Bigas is simple: Get weekly Google Analytics analysis and AI-powered recommendations automatically posted to Discord.**

### One Command, Complete Analysis

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report \
  -H "Content-Type: application/json"
```

This single endpoint automatically:
- 📊 Fetches the last 30 days of Google Analytics data
- 🤖 Generates comprehensive AI-powered analysis
- 💬 Posts detailed insights and recommendations to Discord
- 📈 Provides actionable recommendations for improvement

### What You Get in Discord

Every week, you'll receive a comprehensive report like this:

```
📊 Weekly Analytics Report on its way...

**Q: What are the key trends in our website performance over the last 30 days?**
A: 1. Key Trends and Patterns: Over the last 30 days, we've seen a significant decrease in both active users and sessions, with a total drop of 32.7%. The top-performing countries were the United States and Sweden, although both saw a decrease in active users during this period.

Significant Changes: The total number of active users decreased from 52 to 35, a drop of nearly one-third. This is a negative change that suggests a decline in user engagement.

Actionable Recommendations: 
• Investigate the cause of the drop in active users and sessions. Look into any changes in marketing strategies, competitor activities, or external factors that may have caused the decrease.
• Analyse the performance of the United States and Sweden more deeply to understand why they're top-performing countries and why they've seen a decrease in active users.

**Q: What are the primary traffic sources contributing to total sessions?**
A: The primary traffic sources contributing to total sessions are as follows:

Direct traffic is the highest source, contributing to 42.6% of total sessions.
Organic Search is the second highest, accounting for 38.3% of total sessions.
Organic Social is third, contributing to 12.8% of total sessions.

Notably, there is a significant reliance on Direct and Organic Search traffic, which together account for over 80% of the total traffic. This may indicate a strong brand presence and effective SEO strategies. However, it also suggests a potential vulnerability, as the site's traffic is heavily dependent on these sources.

To improve traffic diversity, it is recommended to boost efforts in other channels. Organic Social, for instance, could be increased through a more robust social media strategy, while Referral traffic could be enhanced by partnering with other sites for link exchanges or guest posting.

**Q: What is the average session duration and pages per session across all users?**
A: The average session duration across all users is approximately 73.18 seconds, meaning that typical users spend just over a minute on the site once they log in. Meanwhile, they view about 2.22 pages (or screens) during that session.

These figures imply that users are somewhat engaged, as they spend enough time to possibly read or interact with the content and visit more than one page per session.

However, if our site has a larger number of pages or complex content that may take longer to consume, this may suggest that users are not exploring the site thoroughly or that they're having a hard time finding what they need. The next step could be identifying ways to encourage longer sessions and more page views per session, such as improving site navigation, highlighting popular content, or introducing more engaging and relevant content.

**Q: Which pages are the most visited, and how do they contribute to conversions?**
A: The most visited page on the website is the homepage ("/"), attracting 39 visits but unfortunately, these visits didn't lead to any conversions. The second and third most visited pages are "/get-in-touch-with-us" and "/about-us", with 14 and 10 visits respectively but also with zero conversions.

The pages related to the business purpose like "/why-promotional-clothing" and "/why-green-promo-wear" have been visited lesser times, 4 and 5 times respectively and didn't contribute to conversions either. Meanwhile, the "/blog-posts-and-news" and individual blog post ("/post/the-truth-about-promotional-clothing-waste---why-sustainable-promo-wear-matters") have had very low visits and no conversions.

From our data, it seems none of the pages directly contributed to conversions. This may indicate that there is potential for improving the website's content strategy or the user experience to increase engagement and conversions.

To derive some actionable insights, a more detailed analysis may be required to understand why people visit different pages and why they are not converting, perhaps using additional data like time spent on page and bounce rates. It might be beneficial to focus on improving the content on the more frequented pages, like your homepage and "/get-in-touch-with-us", to encourage more conversions. And paying attention to the lower-visited pages and blogs with zero conversions to understand what might be missing or causing users not to convert.

**Q: Which pages or sections drive the most engagement?**
A: Based on the data from Google Analytics, the top three pages in terms of engagement on your website are:

Blog post titled "The Truth about Promotional Clothing Waste & Why Sustainable Promo Wear Matters"
The "Eco Certifications" page
The "About Us" page

These pages tend to retain users' attention for longer periods of time when compared to other parts of your website. The average session duration for these pages is 185.7 seconds, 90.4 seconds, and 76.2 seconds, respectively, which indicates that users tend to spend a lot more time on these pages. Furthermore, the bounce rates of these pages are all very low, standing at 0%, 0%, and 20%, respectively.

A noteworthy fact is that the homepage ("/") has a higher bounce rate (56.4%) and a lesser average session duration (39.7 seconds). This could mean that while the homepage is bringing visitors in, it is not as successful in keeping them engaged.

In light of these findings, I recommend focusing more on your blog and informational content—such as what you're doing with the "The Truth about Promotional Clothing Waste & Why Sustainable Promo Wear Matters" and "Eco Certifications" pages. Users seem to be interested in informative, value-added content, and this can be further leveraged to increase their engagement with your site. Additionally, it might be necessary to consider enhancing the homepage to make it more engaging.

**Q: Are there underperforming pages with high traffic but low conversions?**
A: Yes, there are underperforming pages on your site with high traffic but low conversions. 

The three pages with the most significant traffic yet no conversions are the Home page ("/"), "Get in Touch With Us" page, and the "About Us" page. Here are the numbers:

The Home page ("/") has had 39 sessions, but no conversions.
The "Get in Touch With Us" page has had 14 sessions, but again, no conversions.
The "About Us" page received 10 visits, but no conversions.

These pages are getting the attention of your visitors, but they're not enticing them to take the next step. This suggests that while these pages can generate interest, they might lack a clear call to action or don't meet user's needs or expectations.

You could improve the performance of these pages by reviewing their content and layout. Make sure the call to action is clear and appealing, and the information provided is useful and relevant. Test different versions of these pages to find what works best for your audience.

On the brighter side, pages like "Why Green Promo Wear" and "Why Promotional Clothing" are not underperforming, despite having lower sessions. So, these topics might be of interest to your audience and might be worth highlighting more on your high traffic pages.

Remember, even small improvements in conversion rates can lead to significant increases in revenue, especially on high traffic pages. It's definitely worth the time and effort to optimize these pages for better performance.

**Q: How do blog posts or content pages contribute to conversions?**
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

• Investigate the reasons behind the decrease in active users and sessions.
• Amplify marketing efforts in the US and Sweden since they are the top-performing countries.
• Resolve any technical issues that may be causing a drop in active users and sessions.
• Conduct user surveys to understand user behavior and preferences.
• Increase efforts in Organic Social, Cross-Network, and Referral channels to diversify traffic sources.
• Improve site navigation and content to increase user engagement, time spent on site, and pages visited per session.
• Enhance the homepage content and layout to make it more engaging and reduce its high bounce rate.
• Review high traffic but low conversion pages (home, get in touch with us, about us) to ensure clear and appealing calls to action.
• Highlight topics of interest to your audience on high traffic pages.
• Improve blog content to make it more engaging, shareable, and incorporate a 'Micro Conversion' tracking to understand their role in the conversion journey.
```

### Perfect for Solo Founders

- **No Technical Knowledge Required**: Just run one command and get comprehensive insights
- **AI-Powered Analysis**: Get intelligent recommendations, not just raw data
- **Automated Workflow**: Set up once, get weekly reports automatically
- **Actionable Insights**: Clear recommendations you can implement immediately

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
   - **Headers**: Add `Content-Type: application/json`

5. **Save the job**

This will automatically post weekly analytics reports to your Discord channel every Monday at 9 AM.

**Schedule Examples:**
- `0 9 * * 1` - Every Monday at 9 AM
- `0 9 * * 1,4` - Every Monday and Thursday at 9 AM  
- `0 9 1 * *` - First day of every month at 9 AM
- `0 */6 * * *` - Every 6 hours

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
| |  OpenAI, Discord)    | |  Jira, Figma, etc) | |
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
- **`MarketingAnalyticsService`**: Main orchestrator coordinating all services

## 🛠️ Installation & Deployment

### Prerequisites

Before testing, ensure you have:
- ✅ `GA4_PROPERTY_ID` configured
- ✅ `OPENAI_API_KEY` configured  
- ✅ `DISCORD_WEBHOOK_URL` configured
- ✅ Valid Google Analytics data for the last 30 days

### Automated Deployment

The easiest way to deploy is using the provided `deploy.sh` script:

```bash
./deploy.sh
```

This script automates the entire deployment process to Google Cloud Run.

### Manual Deployment

If you prefer to deploy manually or to a different platform:

1. **Build the Docker image**:
   ```bash
   docker build -t bigas-marketing .
   ```

2. **Deploy to your preferred platform** (AWS, Azure, DigitalOcean, etc.)

3. **Set environment variables** in your deployment platform

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details on how to submit pull requests, report issues, and contribute to the project.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔒 Security

For security concerns, please see our [Security Policy](SECURITY.md).

---

**Built with ❤️ for solo founders who want to scale like they have a team.**