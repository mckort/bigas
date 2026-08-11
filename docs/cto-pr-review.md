# CTO PR review (review_and_comment_pr)

The **review_and_comment_pr** tool reviews a pull request diff with AI (default: Gemini via Google AI API key) and posts or updates a single comment on the GitHub PR. Repeated runs update the same comment (identified by a hidden marker) to avoid spam.

## Add to your repo (repo you want reviewed)

Do this in the **repository where you open pull requests** (not the Bigas repo).

**1. Add the workflow file**  
Create `.github/workflows/pr-review.yml` in your repo. You can copy the contents from this repo’s [.github/workflows/pr-review.yml](../.github/workflows/pr-review.yml).

**2. Configure in GitHub**  
In your repo: **Settings → Secrets and variables → Actions**.

- **Variable** `BIGAS_URL`: your Bigas Cloud Run URL (e.g. `https://bigas-xxx.run.app`). **Required** – the workflow fails with a clear error if this is missing.
- **Secret** `BIGAS_API_KEY`: one of your Bigas access keys (same as `BIGAS_ACCESS_KEYS`).
- **Secret** `GH_PAT_FOR_BIGAS`: GitHub PAT with repo scope *(optional if `GITHUB_TOKEN` is set in Bigas Secret Manager)*.
- Discord: Bigas posts to the CTO channel using **DISCORD_WEBHOOK_URL_CTO** from **GCP Secret Manager** (add to `SECRET_MANAGER_SECRET_NAMES`). No GitHub secret needed.

**3. Commit and push**  
`git add .github/workflows/pr-review.yml && git commit -m "Add Bigas PR review workflow" && git push`

On the next PR (open or push), the workflow runs and Bigas posts or updates the review comment.

Optional autofix (Cursor cloud agent): set repository variable `BIGAS_AUTO_FIX=true` and ensure Bigas has `CURSOR_API_KEY`. See [cto-autofix.md](./cto-autofix.md).

Optional auto-merge: set Bigas env `BIGAS_CTO_AUTO_MERGE=true` to squash-merge when the review has no Blockers/Important (Discord **PR auto-merged**). Default is off.

## Server-side configuration (Bigas)

1. **GitHub Personal Access Token**
   - Create a PAT with `repo` scope (or at least read/write for pull request comments).
   - For auto-merge (`BIGAS_CTO_AUTO_MERGE=true`), the token must also be allowed to merge PRs (Contents + Pull requests write; branch protection must permit the token).
   - Store it as `GITHUB_TOKEN` in your environment or in Google Secret Manager (add `GITHUB_TOKEN` to `SECRET_MANAGER_SECRET_NAMES` and sync with `scripts/sync_env_to_secret_manager.py`).

2. **OpenAI API**
   - `OPENAI_API_KEY` must be set (already required for other Bigas features).
   - Optional: `BIGAS_CTO_PR_REVIEW_MODEL` to override the model (default: `gemini-3.1-pro-preview`; recommended: `gemini-pro-latest`).
   - Optional: `BIGAS_CTO_PR_REVIEW_MAX_TOKENS` (default `8000`, max `65536`) for longer reviews.
   - Optional: `BIGAS_CTO_PR_REVIEW_THINKING_BUDGET` (default `8192`) and `BIGAS_CTO_PR_REVIEW_MAX_CONTINUATIONS` (default `3`) to avoid mid-review cutoffs on Gemini thinking models.

## Request

- **POST** `/mcp/tools/review_and_comment_pr`
- **JSON body**
  - `repo` (required): `"owner/repo"`
  - `pr_number` (required): integer
  - `diff` (optional): PR diff text. If omitted or empty, Bigas fetches the PR diff from the GitHub API.
  - `instructions` (optional): extra instructions for the reviewer
  - `phase` (optional): `"initial"` (default) or `"post_autofix"`. Initial reviews use an exhaustive checklist prompt; post-autofix reviews verify the previous Bigas comment and only raise new blockers/important issues.
  - `github_token` (optional): override GitHub PAT (if not using `GITHUB_TOKEN` env)
  - `llm_model` (optional): override model for this request

## Example: GitHub Action

```yaml
- name: Get PR diff via GitHub API
  env:
    GH_TOKEN: ${{ secrets.GH_PAT_FOR_BIGAS || github.token }}
    PR_NUMBER: ${{ github.event.pull_request.number }}
  run: |
    set -euo pipefail
    HTTP_CODE=$(curl -sS -w "%{http_code}" -o diff.txt \
      -H "Accept: application/vnd.github.diff" \
      -H "Authorization: Bearer $GH_TOKEN" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "https://api.github.com/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}")
    if [ "$HTTP_CODE" != "200" ]; then
      echo "Failed to fetch PR diff from GitHub API (HTTP $HTTP_CODE)"
      cat diff.txt || true
      exit 1
    fi

- name: Trigger Bigas review + comment
  env:
    BIGAS_URL: ${{ vars.BIGAS_URL }}
    BIGAS_API_KEY: ${{ secrets.BIGAS_API_KEY }}
    GH_PAT: ${{ secrets.GH_PAT_FOR_BIGAS }}
  run: |
    python3 -c "
    import json, os
    with open('diff.txt', encoding='utf-8', errors='replace') as f:
        diff = f.read()
    payload = {
        'repo': os.environ['GITHUB_REPOSITORY'],
        'pr_number': ${{ github.event.pull_request.number }},
        'diff': diff,
        'github_token': os.environ.get('GH_PAT', '')
    }
    with open('payload.json', 'w') as f:
        json.dump(payload, f)
    "
    curl -sS -X POST "$BIGAS_URL/mcp/tools/review_and_comment_pr" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $BIGAS_API_KEY" \
      -d @payload.json
```

Prefer the GitHub API diff over local `git diff`: if the PR is merged into the base branch before the job runs, a local three-dot diff can be empty while the PR diff endpoint still returns the changes. Bigas also fetches the PR diff server-side when `diff` is omitted or blank.
If `GITHUB_TOKEN` is configured in Bigas (e.g. via Secret Manager), you can omit `github_token` from the request.

## Response

- **Success**: `{ "success": true, "comment_url": "https://github.com/...", "review_posted": true, "used_model": "gemini-pro-latest", "usage": { "model": "...", "attempts": 1, "prompt_tokens": 42000, "candidates_tokens": 3100, "thoughts_tokens": 7200, "total_tokens": 52300, "est_cost_usd": 0.18, "cost_estimate": true } }`
- **Error**: `{ "error": "..." }` with status 400 (validation), 401/403 (GitHub auth), 404 (repo/PR not found), 500 (OpenAI), 502 (GitHub API).

`usage.est_cost_usd` is a **list-price estimate**. Thinking tokens are billed as output, but Gemini `candidates_token_count` / OpenAI `completion_tokens` already include them — estimates do **not** add `thoughts_tokens` on top. Usage is also logged as a JSON line with `"event": "llm_usage"` / `"feature": "cto_pr_review"` in Cloud Run logs for each attempt and once as a review total. The same estimate is appended to the Discord **done** notification (not the started message), e.g. `Estimated LLM cost: ~$0.1800 (gemini-pro-latest, 1 attempt)`.

## Diff size

Diffs larger than 150,000 characters are truncated and a note is prepended to the review. You can change `MAX_DIFF_CHARS` in `bigas/resources/cto/pr_review/service.py` if needed.

## Review length / tokens

The length of the generated review is controlled by these environment variables:

- `BIGAS_CTO_PR_REVIEW_MAX_TOKENS` — max output tokens per generation call (default `8000`, min `1000`, max `65536`).
- `BIGAS_CTO_PR_REVIEW_THINKING_BUDGET` — Gemini thinking budget (default `8192`; set `0`/`off` to omit). Thinking shares the output token budget on Gemini thinking models, so capping it leaves room for the visible review.
- `BIGAS_CTO_PR_REVIEW_MAX_CONTINUATIONS` — if the model hits `MAX_TOKENS` (or the text looks mid-cut), Bigas continues generation up to this many extra calls (default `3`).

If reviews still end mid‑sentence, raise `BIGAS_CTO_PR_REVIEW_MAX_TOKENS` and/or lower the thinking budget.
