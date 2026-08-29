#!/usr/bin/env bash
# One-time (idempotent) setup so Bigas chat / GitHub can deploy this repo via
# workflow_dispatch on deploy.yml.
#
# Creates:
#   - GCP service account github-actions-deploy@…
#   - Workload Identity Federation bound to mckort/bigas
#   - GitHub Actions variables + env-file secret
#
# Usage (from repo root, already authenticated with gcloud and gh):
#   ./scripts/setup-github-actions-deploy.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PROJECT_ID="${GOOGLE_PROJECT_ID:-bigas-503008}"
GITHUB_REPO="${GITHUB_REPO:-mckort/bigas}"
SA_ID="github-actions-deploy"
SA_EMAIL="${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="${RUNTIME_SA:-bigas-run@${PROJECT_ID}.iam.gserviceaccount.com}"
POOL_ID="github"
PROVIDER_ID="github-oidc"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  PROJECT_ID="${GOOGLE_PROJECT_ID:-$PROJECT_ID}"
  RUNTIME_SA="${GOOGLE_SERVICE_ACCOUNT_EMAIL:-$RUNTIME_SA}"
  SA_EMAIL="${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
fi

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "❌ $1 is required" >&2
    exit 1
  fi
}

need_cmd gcloud
need_cmd gh

if [ ! -f "$ENV_FILE" ]; then
  echo "❌ Missing $ENV_FILE (copy from env.example). Needed for BIGAS_DEPLOY_ENV." >&2
  exit 1
fi

echo "🔧 Project: $PROJECT_ID"
echo "🔧 GitHub repo: $GITHUB_REPO"
echo "🔧 Deploy SA: $SA_EMAIL"
echo "🔧 Runtime SA: $RUNTIME_SA"

gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudresourcemanager.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  --project="$PROJECT_ID"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "⏭️  Service account already exists: $SA_EMAIL"
else
  echo "👤 Creating service account $SA_EMAIL"
  gcloud iam service-accounts create "$SA_ID" \
    --project="$PROJECT_ID" \
    --display-name="GitHub Actions deploy"
fi

grant_project_role() {
  local role="$1"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$role" \
    --condition=None \
    --quiet >/dev/null
}

echo "🔑 Granting deploy roles on $PROJECT_ID"
grant_project_role roles/run.admin
grant_project_role roles/artifactregistry.writer
grant_project_role roles/secretmanager.secretAccessor
grant_project_role roles/serviceusage.serviceUsageConsumer

echo "🔑 Allowing $SA_EMAIL to deploy as $RUNTIME_SA"
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountUser" \
  --quiet >/dev/null

if gcloud iam workload-identity-pools describe "$POOL_ID" \
  --location=global --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "⏭️  Workload identity pool already exists: $POOL_ID"
else
  echo "🆔 Creating workload identity pool $POOL_ID"
  gcloud iam workload-identity-pools create "$POOL_ID" \
    --project="$PROJECT_ID" \
    --location=global \
    --display-name="GitHub Actions"
fi

if gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
  --location=global --workload-identity-pool="$POOL_ID" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "⏭️  OIDC provider already exists: $PROVIDER_ID"
else
  echo "🆔 Creating GitHub OIDC provider $PROVIDER_ID"
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --project="$PROJECT_ID" \
    --location=global \
    --workload-identity-pool="$POOL_ID" \
    --display-name="GitHub OIDC" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition="assertion.repository=='${GITHUB_REPO}'"
fi

WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
MEMBER="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_REPO}"

echo "🔗 Binding GitHub repo ${GITHUB_REPO} to $SA_EMAIL via WIF"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="$MEMBER" \
  --quiet >/dev/null

set_github_variable() {
  local name="$1"
  local value="$2"
  if gh api "repos/${GITHUB_REPO}/actions/variables/${name}" >/dev/null 2>&1; then
    gh api --method PATCH "repos/${GITHUB_REPO}/actions/variables/${name}" -f value="$value" >/dev/null
    echo "✏️  Updated GitHub variable $name"
  else
    gh api --method POST "repos/${GITHUB_REPO}/actions/variables" -f name="$name" -f value="$value" >/dev/null
    echo "➕ Created GitHub variable $name"
  fi
}

echo "📦 Writing GitHub Actions variables and secrets (values not printed)"
set_github_variable GCP_WORKLOAD_IDENTITY_PROVIDER "$WIF_PROVIDER"
set_github_variable GCP_DEPLOY_SERVICE_ACCOUNT "$SA_EMAIL"

gh secret set BIGAS_DEPLOY_ENV --repo "$GITHUB_REPO" < "$ENV_FILE"
echo "✅ Stored secret BIGAS_DEPLOY_ENV"

echo ""
echo "Done. After deploy.yml is on the default branch, Bigas chat can dispatch it"
echo "(map: BIG:deploy.yml). Provider: $WIF_PROVIDER"
echo "Service account: $SA_EMAIL"
echo ""
echo "Still needed once: sync BIGAS_DEPLOY_WORKFLOW_MAP to Secret Manager and"
echo "redeploy so the running instance knows about BIG, e.g."
echo "  ENV_FILE=.env.bigas-503008 python scripts/sync_env_to_secret_manager.py"
echo "  ./deploy.sh"
