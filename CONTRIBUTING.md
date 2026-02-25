# Contributing to Bigas

First off, thank you for considering contributing to Bigas! It's people like you that make open source such a great community. We welcome any and all contributions.

## How Can I Contribute?

There are many ways to contribute, from writing tutorials or blog posts, improving the documentation, submitting bug reports and feature requests or writing code which can be incorporated into Bigas itself.

A great place to start is our [GitHub Project Board](https://github.com/users/mckort/projects/1). You can find issues that are ready for development, planned features, and bugs that need fixing. Look for issues tagged with `help wanted` or `good first issue`.

### Reporting Bugs

If you find a bug, please open an issue on GitHub. Please include as much information as possible, including:
- A clear and descriptive title
- A detailed description of the bug
- Steps to reproduce the bug
- Any relevant error messages or logs

### Suggesting Enhancements

If you have an idea for a new feature or an enhancement to an existing one, please open an issue on GitHub. Please include:
- A clear and descriptive title
- A detailed description of the enhancement
- Any mockups or examples that might help illustrate your idea

### Submitting Pull Requests

1. Fork the repository and create your branch from `main`.
2. If you've added code that should be tested, add tests.
3. If you've changed APIs, update the documentation.
4. Ensure the test suite passes.
5. Make sure your code lints.
6. Issue that pull request!

## Adding New Data Providers

Bigas now supports a **provider-based architecture** so you can add new finance, ads, analytics, or notification sources by dropping a single file into `bigas/providers/...` without changing existing code.

### Provider basics

- **Finance providers**: implement `FinanceProvider` in `bigas/providers/finance/base.py`
- **Ads providers**: implement `AdsProvider` in `bigas/providers/ads/base.py`
- **Analytics providers**: implement `AnalyticsProvider` in `bigas/providers/analytics/base.py`
- **Notification channels**: implement `NotificationChannel` in `bigas/providers/notifications/base.py`

Each concrete provider:

- Declares a short `name` (e.g. `quickbooks`, `tiktok`)
- Implements `display_name`
- Implements `is_configured() @classmethod` to check required env vars
- Implements the domain methods (e.g. `get_revenue`, `get_campaign_performance`, `send`)

The global registry in `bigas/registry.py`:

- Discovers providers under `bigas/providers/**` at startup
- Instantiates only those where `is_configured()` returns `True`
- Exposes active providers via `GET /mcp/providers`

### Example: QuickBooks finance provider

To add QuickBooks as a finance provider (see `DESIGN_SPEC.md` for the full example):

1. **Create the provider file**

   Add `bigas/providers/finance/quickbooks.py`:

   ```python
   import os
   import requests
   from typing import List
   from bigas.providers.finance.base import FinanceProvider, PeriodSummary, Transaction


   class QuickBooksProvider(FinanceProvider):
       name = "quickbooks"
       display_name = "QuickBooks Online"

       @classmethod
       def is_configured(cls) -> bool:
           return all(
               [
                   os.getenv("QUICKBOOKS_CLIENT_ID"),
                   os.getenv("QUICKBOOKS_CLIENT_SECRET"),
                   os.getenv("QUICKBOOKS_REFRESH_TOKEN"),
                   os.getenv("QUICKBOOKS_REALM_ID"),
               ]
           )

       # Implement get_revenue, get_expenses, get_transactions
       # using the QuickBooks API and return PeriodSummary/Transaction objects.
   ```

2. **Configure environment variables**

   In your `.env` or secret manager:

   ```bash
   QUICKBOOKS_CLIENT_ID=your_client_id
   QUICKBOOKS_CLIENT_SECRET=your_client_secret
   QUICKBOOKS_REFRESH_TOKEN=your_refresh_token
   QUICKBOOKS_REALM_ID=your_realm_id
   ```

3. **Restart Bigas**

- On startup, `registry.discover()` finds `QuickBooksProvider`
- `is_configured()` gates activation on the four env vars
- `GET /mcp/providers` will now include:

  ```json
  {
    "finance": ["quickbooks"],
    "ads": [...],
    "analytics": [...],
    "notifications": [...]
  }
  ```

If you remove or unset the QuickBooks env vars and restart, the provider silently disappears from `/mcp/providers` without affecting any other tools.

## Code of Conduct

### Our Pledge

In the interest of fostering an open and welcoming environment, we as contributors and maintainers pledge to making participation in our project and our community a harassment-free experience for everyone, regardless of age, body size, disability, ethnicity, sex characteristics, gender identity and expression, level of experience, education, socio-economic status, nationality, personal appearance, race, religion, or sexual identity and orientation.

### Our Standards

Examples of behavior that contributes to creating a positive environment include:
- Using welcoming and inclusive language
- Being respectful of differing viewpoints and experiences
- Gracefully accepting constructive criticism
- Focusing on what is best for the community
- Showing empathy towards other community members

Examples of unacceptable behavior by participants include:
- The use of sexualized language or imagery and unwelcome sexual attention or advances
- Trolling, insulting/derogatory comments, and personal or political attacks
- Public or private harassment
- Publishing others' private information, such as a physical or electronic address, without explicit permission
- Other conduct which could reasonably be considered inappropriate in a professional setting

### Our Responsibilities

Project maintainers are responsible for clarifying the standards of acceptable behavior and are expected to take appropriate and fair corrective action in response to any instances of unacceptable behavior.

Project maintainers have the right and responsibility to remove, edit, or reject comments, commits, code, wiki edits, issues, and other contributions that are not aligned to this Code of Conduct, or to ban temporarily or permanently any contributor for other behaviors that they deem inappropriate, threatening, offensive, or harmful.

### Scope

This Code of Conduct applies both within project spaces and in public spaces when an individual is representing the project or its community.

### Enforcement

Instances of abusive, harassing, or otherwise unacceptable behavior may be reported by contacting the project team at [INSERT CONTACT METHOD]. All complaints will be reviewed and investigated and will result in a response that is deemed necessary and appropriate to the circumstances. The project team is obligated to maintain confidentiality with regard to the reporter of an incident.

### Attribution

This Code of Conduct is adapted from the [Contributor Covenant](https://www.contributor-covenant.org), version 2.0, available at https://www.contributor-covenant.org/version/2/0/code_of_conduct.html. 