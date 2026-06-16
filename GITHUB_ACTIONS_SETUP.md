# Google Maps Weekly Scraper — GitHub Actions Setup

Production workflow for scraping last-7-days Google Maps reviews and uploading them to ClickUp.

## Repository secrets (required)

You already configured these in GitHub:

| Secret | Purpose |
|---|---|
| `CLICKUP_API_TOKEN` | ClickUp API authentication |
| `CLICKUP_WORKSPACE_NAME` | Workspace name (e.g. `Makan`) |
| `CLICKUP_SPACE_NAME` | Space name |
| `CLICKUP_MAKAN_LIST_NAME` | List for MAKAN locations |
| `CLICKUP_COMPETITOR_LIST_NAME` | List for competitor locations |
| `CLICKUP_STATUS` | Task status (e.g. `to do`) |

Secrets override empty values in `last_7_days_batch_config.json` at runtime.

## What runs in production

Workflow: `.github/workflows/last-7-days-batch.yml`

- Scrapes **all** locations from `last_7_days_batch_config.json`
- Uploads reviews from the **last 7 days** (with text) to ClickUp
- Runs headless Chrome on `ubuntu-latest`

## Trigger options

### 1) Manual run (GitHub UI)

`Actions` → `Google Maps Weekly Scraper` → `Run workflow`

### 2) cron-job.org webhook

- **URL:** `https://api.github.com/repos/MHamadaMk24/Google-Maps-Weekly-Scraper/dispatches`
- **Method:** `POST`
- **Headers:**
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer <github_pat_with_repo_scope>`
  - `X-GitHub-Api-Version: 2022-11-28`
- **Body:**

```json
{
  "event_type": "run-weekly-scraper"
}
```

## Local development

Testing is done locally on your machine only. The GitHub repo contains production code only.
