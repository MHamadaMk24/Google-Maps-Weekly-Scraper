# GitHub Actions + cron-job.org Setup

This project can run `last_7_days_batch_to_clickup.py` in GitHub Actions and be triggered from cron-job.org.

## 1) Keep config file in repo, keep secrets in GitHub

- Commit `last_7_days_batch_config.json` with locations and non-secret settings.
- Do **not** commit a real ClickUp token.
- The script now supports environment variable overrides:
  - `CLICKUP_API_TOKEN`
  - `CLICKUP_DEFAULT_LIST_ID` (optional)
  - `CLICKUP_WORKSPACE_NAME` (optional)
  - `CLICKUP_SPACE_NAME` (optional)
  - `CLICKUP_MAKAN_LIST_NAME` (optional)
  - `CLICKUP_COMPETITOR_LIST_NAME` (optional)
  - `CLICKUP_STATUS` (optional)

## 2) Add GitHub secrets

In repository settings -> Secrets and variables -> Actions, add:

- `CLICKUP_API_TOKEN` (required for real upload mode)
- `CLICKUP_WORKSPACE_NAME` (recommended)
- `CLICKUP_SPACE_NAME` (recommended)
- `CLICKUP_MAKAN_LIST_NAME` (recommended)
- `CLICKUP_COMPETITOR_LIST_NAME` (recommended)
- `CLICKUP_STATUS` (optional, e.g. `to do`)

## 3) Trigger options

Workflow file: `.github/workflows/last-7-days-batch.yml`

Supported triggers:

1. `workflow_dispatch` (manual run from GitHub UI)
2. `repository_dispatch` with event type `run-last-7-days-batch`

## 4) Trigger from cron-job.org

Use an HTTP POST job in cron-job.org:

- URL: `https://api.github.com/repos/<owner>/<repo>/dispatches`
- Method: `POST`
- Headers:
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer <github_pat_with_repo_scope>`
  - `X-GitHub-Api-Version: 2022-11-28`
- Body (JSON):

```json
{
  "event_type": "run-last-7-days-batch",
  "client_payload": {
    "test_mode": "false",
    "makan_limit": "1",
    "competitor_limit": "1"
  }
}
```

Notes:
- Set `"test_mode": "true"` to dry-run (no ClickUp uploads).
- Omit limits to run all locations.

## 5) First validation run

Before production schedule, run:

1. Manual `workflow_dispatch`
2. `test_mode = true`
3. `makan_limit = 1`
4. `competitor_limit = 1`

Then check Action logs and confirm payload preview output.
