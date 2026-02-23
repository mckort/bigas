# CTO PR review (review_and_comment_pr)

The **review_and_comment_pr** tool reviews a pull request diff with AI (OpenAI Codex, default `gpt-5.2-codex`) and posts or updates a single comment on the GitHub PR. Repeated runs update the same comment (identified by a hidden marker) to avoid spam.

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

## Server-side configuration (Bigas)

1. **GitHub Personal Access Token**
   - Create a PAT with `repo` scope (or at least read/write for pull request comments).
   - Store it as `GITHUB_TOKEN` in your environment or in Google Secret Manager (add `GITHUB_TOKEN` to `SECRET_MANAGER_SECRET_NAMES` and sync with `scripts/sync_env_to_secret_manager.py`).

2. **OpenAI API**
   - `OPENAI_API_KEY` must be set (already required for other Bigas features).
   - Optional: `BIGAS_CTO_PR_REVIEW_MODEL` to override the model (default: `gpt-5.2-codex`).

## Request

- **POST** `/mcp/tools/review_and_comment_pr`
- **JSON body**
  - `repo` (required): `"owner/repo"`
  - `pr_number` (required): integer
  - `diff` (required): PR diff text (e.g. from `git diff main...HEAD` or GitHub API)
  - `instructions` (optional): extra instructions for the reviewer
  - `github_token` (optional): override GitHub PAT (if not using `GITHUB_TOKEN` env)
  - `llm_model` (optional): override model for this request

## Example: GitHub Action

```yaml
- name: Get PR diff
  id: diff
  run: |
    git fetch origin ${{ github.base_ref }}
    git diff origin/${{ github.base_ref }}...HEAD > diff.txt

- name: Trigger Bigas review + comment
  env:
    BIGAS_URL: ${{ vars.BIGAS_URL }}
    BIGAS_API_KEY: ${{ secrets.BIGAS_API_KEY }}
    GH_PAT: ${{ secrets.GH_PAT_FOR_BIGAS }}
  run: |
    python3 -c "
    import json, os
    with open('diff.txt') as f:
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

If `GITHUB_TOKEN` is configured in Bigas (e.g. via Secret Manager), you can omit `github_token` from the request.

## Response

- **Success**: `{ "success": true, "comment_url": "https://github.com/...", "review_posted": true, "used_model": "gpt-5.2-codex" }`
- **Error**: `{ "error": "..." }` with status 400 (validation), 401/403 (GitHub auth), 404 (repo/PR not found), 500 (OpenAI), 502 (GitHub API).

## Diff size

Diffs larger than 150,000 characters are truncated and a note is prepended to the review. You can change `MAX_DIFF_CHARS` in `bigas/resources/cto/pr_review/service.py` if needed.
