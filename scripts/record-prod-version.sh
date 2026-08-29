#!/usr/bin/env bash
# Record a production deploy as a GitHub prerelease + git tag.
# Used by deploy.sh (local terminal and GitHub Actions) so Bigas can compare
# main against the last shipped Cloud Run revision.
#
# Usage: record-prod-version.sh [app]
set -euo pipefail

COMPONENT="${1:-app}"
if [ "$COMPONENT" != "app" ]; then
  echo "❌ Usage: $0 [app]" >&2
  exit 1
fi

if [ "${SKIP_RECORD_PROD_VERSION:-}" = "true" ]; then
  echo "⏭️  SKIP_RECORD_PROD_VERSION=true — skipping version marker"
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SHA="${DEPLOY_GIT_SHA:-${GITHUB_SHA:-}}"
if [ -z "$SHA" ]; then
  SHA="$(git rev-parse HEAD 2>/dev/null || true)"
fi
if [ -z "$SHA" ]; then
  echo "⚠️  Could not determine git SHA — skipping version marker" >&2
  exit 0
fi

SHORT="$(printf '%s' "$SHA" | cut -c1-7)"
TS="$(date -u +%Y%m%d-%H%M%S)"
TAG="deploy-${COMPONENT}-${TS}-${SHORT}"
TITLE="Deploy ${COMPONENT} ${TS} (${SHORT})"
NOTES="Production ${COMPONENT} deploy of ${SHA}."

echo "🏷️  Recording prod version: ${TAG} @ ${SHA}"

if command -v gh >/dev/null 2>&1; then
  if gh release create "$TAG" \
    --target "$SHA" \
    --title "$TITLE" \
    --notes "$NOTES" \
    --prerelease \
    --latest=false; then
    echo "✅ Prod version created: ${TAG}"
    exit 0
  fi
  echo "⚠️  gh release create failed — trying git tag + push" >&2
fi

if git rev-parse --git-dir >/dev/null 2>&1; then
  git tag -a "$TAG" -m "$TITLE" "$SHA" 2>/dev/null || git tag "$TAG" "$SHA" 2>/dev/null || true
  if git push origin "$TAG"; then
    echo "✅ Prod version tag pushed: ${TAG}"
    exit 0
  fi
fi

echo "⚠️  Could not record prod version ${TAG}. Deploy succeeded, but Bigas cannot compare against it." >&2
exit 0
