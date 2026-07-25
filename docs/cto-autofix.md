# CTO PR autofix (`autofix_pr`)

After Bigas posts a PR review comment, you can optionally launch a **Cursor cloud agent** to push fixes onto the same PR branch.

## Flow

```text
PR opened/push
  → review_and_comment_pr → GitHub comment + Discord
       → if LGTM: Discord "Ready to merge" + Jira Final approval (if issue key on PR)
  → if repo var BIGAS_AUTO_FIX=true (Actions loop, up to 5 rounds):
      → autofix_pr
          → skip if review is LGTM / nits-only
          → skip with loop protection if PR already has ≥5 [bigas-autofix] commits
          → else launch Cursor cloud agent (workOnCurrentBranch)
      → poll autofix_followup until agent terminal
          → Discord: autofix completed / failed / without commits
          → re-review updated diff
          → if LGTM: Discord "Ready to merge" + Jira Final approval
          → else if under 5 rounds: next autofix round with updated review
          → else: Discord + Jira comment — loop protection, manual handling
```

No automerge in v1.

## Server config (Bigas)

1. `CURSOR_API_KEY` in `.env.bigas-*` and Secret Manager (`SECRET_MANAGER_SECRET_NAMES`).
2. Cursor GitHub app installed on the target repos (same as Cloud Agents in the IDE).
3. Existing `GITHUB_TOKEN` (used to read the Bigas review comment + PR head commit).

Optional:

- `BIGAS_CTO_AUTOFIX_MODEL` (Cursor model id). Omit to use Cursor’s default.
- `BIGAS_CTO_AUTOFIX_MAX_ITERATIONS` (default `5`) — max `[bigas-autofix]` commits per PR before loop protection.

## Repo config

In the product repo (Actions variables/secrets):

| Name | Type | Value |
|---|---|---|
| `BIGAS_URL` | variable | Cloud Run URL |
| `BIGAS_API_KEY` | secret | Bigas access key |
| `BIGAS_AUTO_FIX` | variable | `true` to enable autofix step |

Copy the latest [pr-review.yml](../.github/workflows/pr-review.yml) so it includes the autofix loop.

## API

### `POST /mcp/tools/autofix_pr`

```json
{
  "repo": "owner/repo",
  "pr_number": 1,
  "force": false
}
```

- `force: true` bypasses clean-review and loop-protection guards (smoke/debug only).
- Success (launched): `{ "success": true, "launched": true, "autofix_round": 2, "max_iterations": 5, ... }`
- Success (skipped): `{ "success": true, "skipped": true, "reason": "..." }`
- Loop protection: `{ "skipped": true, "loop_protection": true, "autofix_count": 5, ... }`

### `POST /mcp/tools/autofix_followup`

Polled by GitHub Actions after launch:

```json
{
  "repo": "owner/repo",
  "pr_number": 1,
  "agent_id": "bc-...",
  "run_id": "run-..."
}
```

- Not done yet: `{ "done": false, "status": "RUNNING", ... }`
- Done + re-reviewed: `{ "done": true, "ok": true, "rereviewed": true, "ready_to_merge": true, "comment_url": "..." }`
- Done but no `[bigas-autofix]` commit (e.g. agent asked for confirmation): Discord warning, `{ "fixes_pushed": false, "rereviewed": false }` — no fake “completed” message
- After max rounds without LGTM: `{ "loop_protection": true }` + Discord/Jira manual-handling notice

The autofix prompt instructs the agent **not** to ask for confirmation and to push fixes immediately.

## Guards

- Skip when review looks like LGTM / no actionable findings
- Skip when only non-blocking nits
- Stop after `BIGAS_CTO_AUTOFIX_MAX_ITERATIONS` (default 5) commits containing `[bigas-autofix]`
- Agent prompt requires that marker in any commits it creates
