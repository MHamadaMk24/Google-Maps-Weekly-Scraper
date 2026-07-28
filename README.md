# Google Maps Review Scraper & Classifier

Scrape mall reviews from Google Maps, upload to ClickUp, and classify parking-related feedback with a 3-model ensemble.

## Folder layout

```
.
├── scraper/                  # Scraping & ClickUp upload
│   ├── google_maps_scraper.py
│   ├── last_7_days_batch_to_clickup.py   # GitHub Actions weekly job
│   ├── batch_date_window_to_clickup.py     # Flexible date window (GUI)
│   └── review_analyzer_gui_ClickUp.py      # Local GUI (gitignored)
│
├── classifier/               # Parking + sentiment classification
│   ├── zero_shot_review_classifier.py      # Main two-stage pipeline
│   ├── ensemble_classifier.py
│   ├── sentiment_classifier.py
│   ├── topic_classifier.py
│   ├── escalation_rules.py
│   ├── priority_rules.py
│   ├── review_date.py
│   ├── review_text_normalizer.py
│   ├── model_paths.py
│   ├── download_models.py
│   ├── model_comparison_phase1.py        # Side-by-side model comparison
│   └── summarize_comparisons.py
│
├── config/
│   └── last_7_days_batch_config.json     # Mall locations & ClickUp settings
│
├── data/
│   ├── input/                # Drop weekly CSVs here for GitHub Actions
│   ├── models/               # Downloaded Hugging Face models (local cache)
│   └── learned_corrections.json
│
├── docs/
│   └── GITHUB_ACTIONS_SETUP.md
│
├── .github/workflows/
│   ├── last-7-days-batch.yml
│   └── classify-reviews.yml
│
├── requirements.txt          # Scraper dependencies
└── requirements-classifier.txt
```

## Quick start (local)

### Scraper GUI
```powershell
pip install -r requirements.txt
python scraper/review_analyzer_gui_ClickUp.py
```

### Weekly batch (CLI)
```powershell
python scraper/last_7_days_batch_to_clickup.py --headless
```

### Classifier
```powershell
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-classifier.txt
python classifier/download_models.py   # one-time model download
python classifier/zero_shot_review_classifier.py --input path\to\reviews.csv
# Optional: --batch-size 32 if you have enough RAM
```

## GitHub Actions

Primary weekly flow (Sunday cron):
`.github/workflows/weekly-pipeline.yml`

See [docs/GITHUB_ACTIONS_SETUP.md](docs/GITHUB_ACTIONS_SETUP.md).
