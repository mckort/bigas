# LinkedIn portfolio report: no report in Discord

## What the logs mean

1. **"LinkedIn GCS token cache miss or incomplete payload at secrets/linkedin/access_token.json"**  
   Normal when there is no cached token in GCS (e.g. first run or bucket not used for tokens). The service then **mints a new access token** using `LINKEDIN_REFRESH_TOKEN`. As long as that succeeds, reports can run.

2. **"Handling signal: term" / "Worker exiting" / "Shutting down: Master"**  
   Cloud Run is **stopping the request** when the **request timeout** (default 5 minutes) is reached. If the LinkedIn + Reddit + summarization flow takes longer than that, the process is killed before anything is posted to Discord.

## Fixes

### 1. Ensure LinkedIn env vars are set in Cloud Run

The service needs these in the running container (from `.env` at deploy time):

- `LINKEDIN_CLIENT_ID`
- `LINKEDIN_CLIENT_SECRET`
- `LINKEDIN_REFRESH_TOKEN`
- `LINKEDIN_AD_ACCOUNT_URN` (for portfolio report)

**Check in Google Cloud Console:**  
Cloud Run → **mcp-marketing** → **Edit & deploy new revision** → **Variables & secrets** → confirm all four are present.

If any are missing, add them and redeploy (or run `./deploy.sh` again with a `.env` that contains them).

### 2. Increase the request timeout (required for cross-platform / long runs)

Default is 5 minutes; portfolio + cross-platform can take 10–30+ minutes.

```bash
gcloud run services update mcp-marketing --timeout=3600 --region=europe-north1 --project=bigas-462807
```

(3600 seconds = 1 hour.)

### 3. Optional: cache the access token in GCS

To avoid minting on every cold start, you can run the OAuth exchange once (e.g. locally or from a one-off job), then upload the token to GCS so the service finds it at `secrets/linkedin/access_token.json`. Structure:

```json
{"access_token": "…", "expires_at": 1234567890}
```

If that blob exists and is valid, the service uses it and only refreshes when needed.

## Summary

- **No first portfolio report** is usually either: (a) LinkedIn token not available (missing or invalid refresh token / client id / secret in Cloud Run), or (b) request timeout killing the process before “summarize + post to Discord”.
- Do **1** and **2** above; then trigger the report again and check Discord and logs.
