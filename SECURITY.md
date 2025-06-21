# 🔒 Security Guide

## Overview

This document outlines security best practices and requirements for the Bigas Marketing MCP Server.

## 🚨 Critical Security Requirements

### 1. Environment Variables
**NEVER commit sensitive data to version control!**

All sensitive information must be stored as environment variables:

- `GA4_PROPERTY_ID` - Google Analytics 4 Property ID
- `OPENAI_API_KEY` - OpenAI API key
- `DISCORD_WEBHOOK_URL` - Discord webhook URL (optional)
- `GOOGLE_PROJECT_ID` - Google Cloud Project ID
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` - Service account email

### 2. File Security
- ✅ `.env` files are in `.gitignore`
- ✅ No hardcoded secrets in scripts
- ✅ Service account JSON files are excluded
- ✅ API keys are never logged

### 3. Access Control
- ✅ Service accounts have minimal required permissions
- ✅ API keys have appropriate scopes
- ✅ HTTPS for all external communications
- ✅ Regular key rotation

## 🔐 Security Checklist

### Before Committing Code
- [ ] No API keys in code
- [ ] No hardcoded credentials
- [ ] No sensitive URLs in comments
- [ ] `.env` file is not tracked
- [ ] Service account files are excluded

### Before Deployment
- [ ] Environment variables are set
- [ ] Service account has correct permissions
- [ ] API keys are valid and active
- [ ] HTTPS is used for webhooks
- [ ] Logging excludes sensitive data

### Regular Maintenance
- [ ] Rotate API keys quarterly
- [ ] Review service account permissions
- [ ] Monitor API usage and costs
- [ ] Update dependencies for security patches
- [ ] Audit access logs

## 🛡️ Security Features

### Input Validation
- All API endpoints validate input parameters
- Date ranges are validated for logical consistency
- Metric/dimension combinations are verified for GA4 compatibility

### Error Handling
- Sensitive information is never exposed in error messages
- Graceful degradation when services are unavailable
- Proper HTTP status codes for different error types

### Rate Limiting
- Consider implementing rate limiting for API endpoints
- Monitor for unusual usage patterns
- Set appropriate timeouts for external API calls

## 🚨 Incident Response

### If API Keys are Compromised
1. **Immediately rotate the compromised key**
2. **Check for unauthorized usage**
3. **Review access logs**
4. **Update all environment variables**
5. **Notify relevant stakeholders**

### If Service Account is Compromised
1. **Disable the service account**
2. **Create a new service account**
3. **Update permissions and environment variables**
4. **Review all access logs**
5. **Audit all resources accessed**

## 📋 Security Documentation

### Environment Setup
See `README.md` for detailed environment setup instructions.

### Deployment Security
- Use `deploy.sh` script which validates environment variables
- Never hardcode secrets in deployment scripts
- Use Google Cloud IAM for service account management

### Monitoring
- Monitor API usage through Google Cloud Console
- Set up alerts for unusual activity
- Regular security audits of the codebase

## 🔍 Security Tools

### Recommended Tools
- **GitGuardian** - Detect secrets in code
- **Snyk** - Dependency vulnerability scanning
- **Google Cloud Security Command Center** - Cloud security monitoring

### Code Scanning
```bash
# Check for secrets in code
grep -r "sk-" . --exclude-dir=venv
grep -r "AIza" . --exclude-dir=venv
grep -r "discord.com/api/webhooks" . --exclude-dir=venv
```

## 📞 Security Contacts

For security issues:
1. **Do not create public issues** for security problems
2. **Contact the maintainer directly** with security concerns
3. **Include detailed information** about the security issue
4. **Provide steps to reproduce** if applicable

## 🔄 Security Updates

This security guide should be reviewed and updated:
- When new dependencies are added
- When new API integrations are implemented
- When deployment processes change
- At least quarterly for general review 