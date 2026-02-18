#!/bin/bash
set -e

# Load environment variables from .env file if it exists (avoid xargs for portability)
if [ -f .env ]; then
    echo "Loading environment variables from .env file..."
    set -a
    # shellcheck source=.env
    source ./.env
    set +a
fi

# Check required environment variables
if [ -z "$GA4_PROPERTY_ID" ]; then
    echo "❌ Error: GA4_PROPERTY_ID environment variable is not set"
    echo "Please set it in your .env file or export it in your shell"
    exit 1
fi

if [ -z "$OPENAI_API_KEY" ]; then
    echo "❌ Error: OPENAI_API_KEY environment variable is not set"
    echo "Please set it in your .env file or export it in your shell"
    exit 1
fi

if [ -z "$GOOGLE_PROJECT_ID" ]; then
    echo "❌ Error: GOOGLE_PROJECT_ID environment variable is not set"
    echo "Please set it in your .env file or export it in your shell"
    exit 1
fi

if [ -z "$GOOGLE_SERVICE_ACCOUNT_EMAIL" ]; then
    echo "❌ Error: GOOGLE_SERVICE_ACCOUNT_EMAIL environment variable is not set"
    echo "Please set it in your .env file or export it in your shell"
    exit 1
fi

# Check Docker-related environment variables
if [ -z "$DOCKER_REPO" ]; then
    echo "❌ Error: DOCKER_REPO environment variable is not set"
    echo "Please set it in your .env file or export it in your shell"
    exit 1
fi

if [ -z "$IMAGE_NAME" ]; then
    echo "❌ Error: IMAGE_NAME environment variable is not set"
    echo "Please set it in your .env file or export it in your shell"
    exit 1
fi

if [ -z "$IMAGE_TAG" ]; then
    echo "❌ Error: IMAGE_TAG environment variable is not set"
    echo "Please set it in your .env file or export it in your shell"
    exit 1
fi

# Optional Discord webhooks (marketing/product)
DISCORD_ENV_VAR=""
if [ ! -z "$DISCORD_WEBHOOK_URL_MARKETING" ]; then
    DISCORD_ENV_VAR="$DISCORD_ENV_VAR,DISCORD_WEBHOOK_URL_MARKETING=$DISCORD_WEBHOOK_URL_MARKETING"
fi
if [ ! -z "$DISCORD_WEBHOOK_URL_PRODUCT" ]; then
    DISCORD_ENV_VAR="$DISCORD_ENV_VAR,DISCORD_WEBHOOK_URL_PRODUCT=$DISCORD_WEBHOOK_URL_PRODUCT"
fi

# Optional Jira env vars (required for create_release_notes functionality)
JIRA_ENV_VAR=""
if [ ! -z "$JIRA_BASE_URL" ]; then
    JIRA_ENV_VAR="$JIRA_ENV_VAR,JIRA_BASE_URL=$JIRA_BASE_URL"
fi
if [ ! -z "$JIRA_EMAIL" ]; then
    JIRA_ENV_VAR="$JIRA_ENV_VAR,JIRA_EMAIL=$JIRA_EMAIL"
fi
if [ ! -z "$JIRA_API_TOKEN" ]; then
    JIRA_ENV_VAR="$JIRA_ENV_VAR,JIRA_API_TOKEN=$JIRA_API_TOKEN"
fi
if [ ! -z "$JIRA_PROJECT_KEY" ]; then
    JIRA_ENV_VAR="$JIRA_ENV_VAR,JIRA_PROJECT_KEY=$JIRA_PROJECT_KEY"
fi
if [ ! -z "$JIRA_JQL_EXTRA" ]; then
    JIRA_ENV_VAR="$JIRA_ENV_VAR,JIRA_JQL_EXTRA=$JIRA_JQL_EXTRA"
fi

# Optional Storage bucket
STORAGE_ENV_VAR=""
if [ ! -z "$STORAGE_BUCKET_NAME" ]; then
    STORAGE_ENV_VAR=",STORAGE_BUCKET_NAME=$STORAGE_BUCKET_NAME"
fi

# Optional Target keywords
KEYWORDS_ENV_VAR=""
if [ ! -z "$TARGET_KEYWORDS" ]; then
    KEYWORDS_ENV_VAR=",TARGET_KEYWORDS=$TARGET_KEYWORDS"
fi

# Optional LinkedIn Ads (Paid Ads reporting)
LINKEDIN_ENV_VAR=""
if [ ! -z "$LINKEDIN_CLIENT_ID" ]; then
    LINKEDIN_ENV_VAR="$LINKEDIN_ENV_VAR,LINKEDIN_CLIENT_ID=$LINKEDIN_CLIENT_ID"
fi
if [ ! -z "$LINKEDIN_CLIENT_SECRET" ]; then
    LINKEDIN_ENV_VAR="$LINKEDIN_ENV_VAR,LINKEDIN_CLIENT_SECRET=$LINKEDIN_CLIENT_SECRET"
fi
if [ ! -z "$LINKEDIN_ACCESS_TOKEN" ]; then
    LINKEDIN_ENV_VAR="$LINKEDIN_ENV_VAR,LINKEDIN_ACCESS_TOKEN=$LINKEDIN_ACCESS_TOKEN"
fi
if [ ! -z "$LINKEDIN_REFRESH_TOKEN" ]; then
    LINKEDIN_ENV_VAR="$LINKEDIN_ENV_VAR,LINKEDIN_REFRESH_TOKEN=$LINKEDIN_REFRESH_TOKEN"
fi
if [ ! -z "$LINKEDIN_AD_ACCOUNT_URN" ]; then
    LINKEDIN_ENV_VAR="$LINKEDIN_ENV_VAR,LINKEDIN_AD_ACCOUNT_URN=$LINKEDIN_AD_ACCOUNT_URN"
fi
if [ ! -z "$LINKEDIN_VERSION" ]; then
    LINKEDIN_ENV_VAR="$LINKEDIN_ENV_VAR,LINKEDIN_VERSION=$LINKEDIN_VERSION"
fi
if [ ! -z "$LINKEDIN_AD_ACCOUNT_URN" ]; then
    echo "📌 LinkedIn Account URN: set (will be sent to Cloud Run)"
else
    echo "⚠️  LinkedIn Account URN: not set in .env (run_linkedin_portfolio_report will require account_urn in body)"
fi

# Optional Reddit Ads
REDDIT_ENV_VAR=""
if [ ! -z "$REDDIT_CLIENT_ID" ]; then
    REDDIT_ENV_VAR="$REDDIT_ENV_VAR,REDDIT_CLIENT_ID=$REDDIT_CLIENT_ID"
fi
if [ ! -z "$REDDIT_CLIENT_SECRET" ]; then
    REDDIT_ENV_VAR="$REDDIT_ENV_VAR,REDDIT_CLIENT_SECRET=$REDDIT_CLIENT_SECRET"
fi
if [ ! -z "$REDDIT_REFRESH_TOKEN" ]; then
    REDDIT_ENV_VAR="$REDDIT_ENV_VAR,REDDIT_REFRESH_TOKEN=$REDDIT_REFRESH_TOKEN"
fi
if [ ! -z "$REDDIT_AD_ACCOUNT_ID" ]; then
    REDDIT_ENV_VAR="$REDDIT_ENV_VAR,REDDIT_AD_ACCOUNT_ID=$REDDIT_AD_ACCOUNT_ID"
fi
if [ ! -z "$REDDIT_AD_ACCOUNT_ID" ]; then
    echo "📌 Reddit Ad Account ID: set (will be sent to Cloud Run)"
else
    echo "⚠️  Reddit Ad Account ID: not set in .env (run_reddit_portfolio_report will require account_id in body)"
fi

# Optional Google Ads
GOOGLE_ADS_ENV_VAR=""
if [ ! -z "$GOOGLE_ADS_DEVELOPER_TOKEN" ]; then
    GOOGLE_ADS_ENV_VAR="$GOOGLE_ADS_ENV_VAR,GOOGLE_ADS_DEVELOPER_TOKEN=$GOOGLE_ADS_DEVELOPER_TOKEN"
fi
if [ ! -z "$GOOGLE_ADS_LOGIN_CUSTOMER_ID" ]; then
    GOOGLE_ADS_ENV_VAR="$GOOGLE_ADS_ENV_VAR,GOOGLE_ADS_LOGIN_CUSTOMER_ID=$GOOGLE_ADS_LOGIN_CUSTOMER_ID"
fi
if [ ! -z "$GOOGLE_ADS_CUSTOMER_ID" ]; then
    GOOGLE_ADS_ENV_VAR="$GOOGLE_ADS_ENV_VAR,GOOGLE_ADS_CUSTOMER_ID=$GOOGLE_ADS_CUSTOMER_ID"
fi
if [ ! -z "$GOOGLE_ADS_DEVELOPER_TOKEN" ]; then
    echo "📌 Google Ads: developer token + customer IDs will be sent to Cloud Run"
else
    echo "⚠️  Google Ads: not set in .env (run_google_ads_portfolio_report will require customer_id in body)"
fi

# Optional Meta (Facebook/Instagram) Ads
META_ENV_VAR=""
if [ ! -z "$META_ACCESS_TOKEN" ]; then
    META_ENV_VAR="$META_ENV_VAR,META_ACCESS_TOKEN=$META_ACCESS_TOKEN"
fi
if [ ! -z "$META_AD_ACCOUNT_ID" ]; then
    META_ENV_VAR="$META_ENV_VAR,META_AD_ACCOUNT_ID=$META_AD_ACCOUNT_ID"
fi
if [ ! -z "$META_ACCESS_TOKEN" ]; then
    echo "📌 Meta Ads: access token + account ID will be sent to Cloud Run"
else
    echo "⚠️  Meta Ads: not set in .env (run_meta_portfolio_report will require account_id in body)"
fi

echo "✅ All required environment variables are set"
echo "📊 GA4 Property ID: $GA4_PROPERTY_ID"
echo "🤖 OpenAI API Key: ${OPENAI_API_KEY:0:20}..."
echo "☁️  Google Project ID: $GOOGLE_PROJECT_ID"
echo "🔐 Service Account: $GOOGLE_SERVICE_ACCOUNT_EMAIL"
echo "🐳 Docker Repo: $DOCKER_REPO"
echo "📦 Image Name: $IMAGE_NAME"
echo "🏷️  Image Tag: $IMAGE_TAG"
if [ ! -z "$STORAGE_BUCKET_NAME" ]; then
    echo "🗄️  Storage Bucket: $STORAGE_BUCKET_NAME"
fi
if [ ! -z "$TARGET_KEYWORDS" ]; then
    echo "🎯 Target Keywords: $TARGET_KEYWORDS"
fi

IMAGE="europe-north1-docker.pkg.dev/$GOOGLE_PROJECT_ID/$DOCKER_REPO/$IMAGE_NAME:$IMAGE_TAG"

echo "🔨 Building Docker image..."
docker build -t $IMAGE_NAME:$IMAGE_TAG .
docker tag $IMAGE_NAME:$IMAGE_TAG $IMAGE

echo "📤 Pushing image to Google Container Registry..."
docker push $IMAGE

echo "🚀 Deploying to Google Cloud Run..."
gcloud run deploy mcp-marketing \
    --image $IMAGE \
    --platform managed \
    --region europe-north1 \
    --allow-unauthenticated \
    --service-account=$GOOGLE_SERVICE_ACCOUNT_EMAIL \
    --set-env-vars DEPLOYMENT_MODE=$DEPLOYMENT_MODE,GA4_PROPERTY_ID=$GA4_PROPERTY_ID,OPENAI_API_KEY=$OPENAI_API_KEY$DISCORD_ENV_VAR$JIRA_ENV_VAR$STORAGE_ENV_VAR$KEYWORDS_ENV_VAR$LINKEDIN_ENV_VAR$REDDIT_ENV_VAR$GOOGLE_ADS_ENV_VAR$META_ENV_VAR

echo "✅ Deployment completed successfully!" 