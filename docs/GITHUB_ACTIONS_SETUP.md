# Weekly Scrape → Classify → ClickUp Upload

Production workflow for Sunday runs:
1. Scrape last-7-days Google Maps reviews (CSV only)
2. Merge MAKAN + competitors (add `pipeline_category` identifier)
3. Classify parking-related reviews in **one** pass (models load 3× total)
4. Split by category and drop the identifier
5. Upload classified rows into ClickUp custom fields

## Workflow

File: `.github/workflows/weekly-pipeline.yml`  
Name: **Weekly Scrape Classify Upload**

## Repository secrets (required)

| Secret | Purpose |
|---|---|
| `CLICKUP_API_TOKEN` | ClickUp API authentication |
| `CLICKUP_WORKSPACE_NAME` | Workspace name (e.g. `MAKAN`) |
| `CLICKUP_SPACE_NAME` | Space name (e.g. `Data Analysis Section`) |
| `CLICKUP_MAKAN_LIST_NAME` | List for MAKAN locations |
| `CLICKUP_COMPETITOR_LIST_NAME` | List for competitor locations |
| `CLICKUP_STATUS` | Task status (e.g. `to do`) |

Secrets override empty values in `config/last_7_days_batch_config.json` at runtime.

## Triggers

### 1) Cron (Sunday)

Runs every Sunday at **06:00 UTC** (= **09:00 Asia/Riyadh**):

```yaml
cron: "0 6 * * 0"
```

Change the hour in `.github/workflows/weekly-pipeline.yml` if needed.

### 2) Manual run (GitHub UI)

`Actions` → `Weekly Scrape Classify Upload` → `Run workflow`

### 3) External cron webhook (optional)

- **URL:** `https://api.github.com/repos/<owner>/<repo>/dispatches`
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

## Output files (per run)

Saved under `data/weekly/` and uploaded as a workflow artifact:

| File | Content |
|---|---|
| `makan_last_week_reviews.csv` | Raw scraped MAKAN reviews |
| `competitors_last_week_reviews.csv` | Raw scraped competitor reviews |
| `all_last_week_reviews.csv` | Merged raw (with `pipeline_category` for routing) |
| `all_last_week_reviews_classified.csv` | Single classify pass output (still has `pipeline_category`) |
| `makan_last_week_reviews_classified.csv` | Final classified MAKAN parking rows (no category column) |
| `competitors_last_week_reviews_classified.csv` | Final classified competitor parking rows (no category column) |

`pipeline_category` is merge/split only and is **not** uploaded to ClickUp.

## ClickUp behavior

- ClickUp is **storage only**
- Uploader writes the 14 classified CSV columns into matched custom fields
- MAKAN rows → MAKAN list
- Competitor rows → Competitors list
- Always uploads (no duplicate skip)

## Local development

```powershell
# 1) Scrape only
python scraper/last_7_days_batch_to_clickup.py --headless --scrape-only --output-dir data/weekly

# 2) Merge -> classify once -> split (drops pipeline_category)
python scraper/merge_split_weekly_reviews.py merge --makan data/weekly/makan_last_week_reviews.csv --competitors data/weekly/competitors_last_week_reviews.csv --output data/weekly/all_last_week_reviews.csv
python classifier/zero_shot_review_classifier.py --input data/weekly/all_last_week_reviews.csv --output data/weekly/all_last_week_reviews_classified.csv
python scraper/merge_split_weekly_reviews.py split --input data/weekly/all_last_week_reviews_classified.csv --makan-output data/weekly/makan_last_week_reviews_classified.csv --competitors-output data/weekly/competitors_last_week_reviews_classified.csv

# 3) Upload classified sheets
python scraper/clickup_classified_upload.py --input data/weekly/makan_last_week_reviews_classified.csv
python scraper/clickup_classified_upload.py --input data/weekly/competitors_last_week_reviews_classified.csv
```
