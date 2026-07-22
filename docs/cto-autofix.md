# CTO PR autofix (`autofix_pr`)

After Bigas posts a PR review comment, you can optionally launch a **Cursor cloud agent** to push fixes onto the same PR branch.

## Flow

```text
PR opened/push
  → review_and_comment_pr (Gemini) → GitHub comment + Discord
  → if repo var BIGAS_AUTO_FIX=true
      → autofix_pr
          → skip if review is LGTM / nits-only, or last commit is [bigas-autofix]
          → else launch Cursor cloud agent (workOnCurrentBranch)
```

No automerge in v1.

## Server config (Bigas)

1. `CURSOR_API_KEY` in `.env.bigas-*` and Secret Manager (`SECRET_MANAGER_SECRET_NAMES`).
2. Cursor GitHub app installed on the target repos (same as Cloud Agents in the IDE).
3. Existing `GITHUB_TOKEN` (used to read the Bigas review comment + PR head commit).

Optional: `BIGAS_CTO_AUTOFIX_MODEL` (Cursor model id). Omit to use Cursor’s default.

## Repo config

In the product repo (Actions variables/secrets):

| Name | Type | Value |
|---|---|---|
| `BIGAS_URL` | variable | Cloud Run URL |
| `BIGAS_API_KEY` | secret | Bigas access key |
| `BIGAS_AUTO_FIX` | variable | `true` to enable autofix step |

Copy the latest [pr-review.yml](../.github/workflows/pr-review.yml) so it includes the autofix step.

## API

`POST /mcp/tools/autofix_pr`

```json
{
  "repo": "owner/repo",
  "pr_number": 1,
  "force": false
}
```

- `force: true` bypasses clean-review and `[bigas-autofix]` loop guards (smoke/debug only).
- Success (launched): `{ "success": true, "launched": true, "agent_url": "...", "agent_id": "bc-..." }`
- Success (skipped): `{ "success": true, "skipped": true, "reason": "..." }`

## Guards

- Skip when review looks like LGTM / no actionable findings
- Skip when only non-blocking nits
- Skip when latest PR head commit message contains `[bigas-autofix]`
- Agent prompt requires that marker in any commits it creates
