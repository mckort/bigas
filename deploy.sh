#!/bin/bash
set -e

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    echo "Loading environment variables from .env file..."
    export $(cat .env | grep -v '^#' | xargs)
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

# Optional Discord webhook
DISCORD_ENV_VAR=""
if [ ! -z "$DISCORD_WEBHOOK_URL" ]; then
    DISCORD_ENV_VAR=",DISCORD_WEBHOOK_URL=$DISCORD_WEBHOOK_URL"
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
    --set-env-vars GA4_PROPERTY_ID=$GA4_PROPERTY_ID,OPENAI_API_KEY=$OPENAI_API_KEY$DISCORD_ENV_VAR$STORAGE_ENV_VAR$KEYWORDS_ENV_VAR

echo "✅ Deployment completed successfully!" 