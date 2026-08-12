# CTO PR autofix (`autofix_pr`)

After Bigas posts a PR review comment, you can optionally launch a **Cursor cloud agent** to push fixes onto the same PR branch.

## Flow

```text
PR opened/push
  → review_and_comment_pr → GitHub comment + Discord
       → if LGTM: Discord "Ready to merge" + Jira Final approval (if issue key on PR)
         → if BIGAS_CTO_AUTO_MERGE=true: squash-merge PR (or enable GitHub auto-merge if checks pending) + Discord
  → if repo var BIGAS_AUTO_FIX=true (Actions loop, up to 5 rounds):
      → autofix_pr
          → skip if review is LGTM / nits-only
          → skip with loop protection if PR already has ≥5 [bigas-autofix] commits
          → if cooldown (fresh [bigas-autofix] head): Discord + PR notice, wait, retry
          → else launch Cursor cloud agent (workOnCurrentBranch)
      → poll autofix_followup until agent terminal
          → Discord: autofix completed / failed / without commits
          → re-review updated diff
          → if LGTM: Discord "Ready to merge" + Jira Final approval
            → if BIGAS_CTO_AUTO_MERGE=true: squash-merge or enable GitHub auto-merge + Discord
          → else if under 5 rounds: next autofix round with updated review
          → else: Discord + Jira comment — loop protection, manual handling
```

Optional auto-merge is off by default (`BIGAS_CTO_AUTO_MERGE=false`). When enabled, Bigas tries an immediate squash-merge once the review has no Blockers/Important; if required checks block it, Bigas enables GitHub native auto-merge instead. Jira still moves to Final approval.

After each autofix round finalizes, Discord includes Cursor token usage + a list-price estimate when available. For weekly rollups across Cursor autofix and LLM review logs, see [cto-ai-usage.md](./cto-ai-usage.md).

## Server config (Bigas)

1. `CURSOR_API_KEY` in `.env.bigas-*` and Secret Manager (`SECRET_MANAGER_SECRET_NAMES`).
2. Cursor GitHub app installed on the target repos (same as Cloud Agents in the IDE).
3. Existing `GITHUB_TOKEN` (used to read the Bigas review comment + PR head commit).

Optional:

- `BIGAS_CTO_AUTOFIX_MODEL` (Cursor model id). Prefer `composer-2.5` (standard tier; much cheaper than `composer-2.5-fast`). Omit to use Cursor’s default (often fast).
- `BIGAS_CTO_AUTOFIX_MAX_ITERATIONS` (default `5`) — max `[bigas-autofix]` commits per PR before loop protection.
- `BIGAS_CTO_AUTOFIX_COOLDOWN_SECONDS` (default `120`) — skip launching another autofix while the PR head is still a fresh `[bigas-autofix]` commit (reduces overlapping agents). Cooldown is **skipped** when a newer Bigas review comment already exists after that head commit (typical after an autofix push cancels/restarts Actions). The Actions loop waits/retries in short slices until the window expires instead of stopping early. Bigas also posts/updates a visible PR comment (`<!-- bigas-autofix-cooldown-marker -->`) so cooldown is not mistaken for a hang.
- `BIGAS_CTO_AUTO_MERGE` (default `false`) — when `true`, squash-merge the PR after a clean review (no Blockers/Important) and post **PR auto-merged** to Discord. If required checks block an immediate merge, Bigas enables GitHub native auto-merge and posts **PR auto-merge enabled** instead. Requires `GITHUB_TOKEN` with merge permission and repo setting **Allow auto-merge**. Jira Final approval still runs.

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
- Done + re-reviewed: `{ "done": true, "ok": true, "rereviewed": true, "ready_to_merge": true, "comment_url": "...", "auto_merge": { ... } }`
- Done but no **new** `[bigas-autofix]` commit since launch (pass `baseline_head_sha` from `autofix_pr.head_sha`): Discord warning, `{ "fixes_pushed": false, "rereviewed": false }` — does **not** rewrite the PR review comment
- After max rounds without LGTM: `{ "loop_protection": true }` + Discord/Jira manual-handling notice

Discord “round N/M” uses the number of `[bigas-autofix]` commits on the PR after a successful push (completed round), not Actions loop attempts that finished without commits.

The autofix prompt instructs the agent **not** to ask for confirmation and to push fixes immediately.

## Guards

- Skip when review looks like LGTM / no actionable findings
- Skip when only non-blocking nits (including structured `### Minor` with empty Blockers/Important)
- Skip soft-only language (`consider`, `TODO`, `optional`) unless Blockers/Important are present
- When autofix *does* run (Blockers/Important present), the agent also fixes Minor items from the same review
- Stop after `BIGAS_CTO_AUTOFIX_MAX_ITERATIONS` (default 5) commits containing `[bigas-autofix]`
- Cooldown when head is a fresh `[bigas-autofix]` commit *and* no newer Bigas review exists yet: Actions waits/retries; Bigas posts a PR cooldown notice. If review is already newer than the autofix head, the next agent launches immediately.

**Actions note:** `pr-review.yml` skips workflow runs whose head commit contains `[bigas-autofix]` (gate job). That prevents the autofix push from cancelling the in-flight job or starting a second full review/merge cycle. Bigas also skips review / final-approval Discord / auto-merge quietly when the PR is already merged.
- Agent prompt requires that marker in any commits it creates
- Re-review after autofix uses a verification prompt and the previous Bigas comment, so it should not invent a fresh set of nits each round
