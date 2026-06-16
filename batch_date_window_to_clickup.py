"""
Batch scrape Google Maps reviews within a selectable date window (text reviews only)
and upload them to ClickUp.

Uses the same config file shape as last_7_days_batch_to_clickup.py (last_7_days_batch_config.json).
last_7_days_batch_to_clickup.py is unchanged; this script adds a configurable lookback window
and exposes DATE_WINDOW_OPTIONS / date_window_choice_to_days for review_analyzer_gui_ClickUp.py.
"""

import json
import re
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from google_maps_scraper import (
    process_reviews_function,
    scrape_reviews_function_recent_with_text,
)


CONFIG_FILE = Path(__file__).resolve().parent / "last_7_days_batch_config.json"

# Ordered labels for CLI --date-window and the GUI combobox. Month spans use 30-day periods.
_DATE_WINDOW_ROWS: Tuple[Tuple[str, int], ...] = (
    ("Last week", 7),
    ("Last 2 weeks", 14),
    ("Last 3 weeks", 21),
    ("Last 1 month", 30),
    ("Last 2 months", 60),
    ("Last 3 months", 90),
    ("Last 4 months", 120),
    ("Last 5 months", 150),
    ("Last 6 months", 180),
)

DATE_WINDOW_OPTIONS: List[str] = [label for label, _ in _DATE_WINDOW_ROWS]
_DATE_WINDOW_DAYS: Dict[str, int] = dict(_DATE_WINDOW_ROWS)


def date_window_choice_to_days(choice: str) -> int:
    """Resolve a UI/CLI label to a number of calendar days (minimum 1)."""
    key = (choice or "").strip()
    if key not in _DATE_WINDOW_DAYS:
        raise KeyError(f"Unknown date window: {choice!r}")
    return max(1, int(_DATE_WINDOW_DAYS[key]))


@dataclass
class LocationConfig:
    name: str
    url: str
    clickup_list_id: Optional[str] = None
    tag: Optional[str] = None


def load_config(path: Path) -> Dict:
    if not path.exists():
        create_example_config(path)
        raise FileNotFoundError(
            f"Config file was not found. I created a template at: {path.resolve()}"
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def create_example_config(path: Path) -> None:
    example = {
        "clickup": {
            "api_token": "PASTE_CLICKUP_TOKEN_HERE",
            "default_list_id": "OPTIONAL_DEFAULT_LIST_ID",
            "status": "to do",
        },
        "makan_locations": [
            {
                "name": "MAKAN - Riyadh Branch",
                "url": "https://maps.google.com/?q=...",
                "clickup_list_id": "OPTIONAL_LIST_ID_FOR_THIS_LOCATION",
                "tag": "makan",
            }
        ],
        "competitor_locations": [
            {
                "name": "Competitor - Branch 1",
                "url": "https://maps.google.com/?q=...",
                "clickup_list_id": "OPTIONAL_LIST_ID_FOR_THIS_LOCATION",
                "tag": "competitor",
            }
        ],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(example, f, indent=2, ensure_ascii=False)


def parse_locations(raw_locations: List[Dict]) -> List[LocationConfig]:
    parsed: List[LocationConfig] = []
    for item in raw_locations:
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url:
            continue
        parsed.append(
            LocationConfig(
                name=name,
                url=url,
                clickup_list_id=(str(item.get("clickup_list_id")).strip() or None)
                if item.get("clickup_list_id") is not None
                else None,
                tag=(str(item.get("tag")).strip() or None)
                if item.get("tag") is not None
                else None,
            )
        )
    return parsed


def _clickup_get(url: str, token: str) -> Dict:
    headers = {"Authorization": token, "Content-Type": "application/json"}
    response = requests.get(url, headers=headers, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f"ClickUp GET failed ({response.status_code}): {response.text[:250]}")
    return response.json()


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def resolve_clickup_list_id_by_name(
    token: str,
    workspace_name: str,
    space_name: str,
    list_name: str,
) -> Optional[str]:
    workspace_name = workspace_name.strip()
    space_name = space_name.strip()
    list_name = list_name.strip()
    if not workspace_name or not space_name or not list_name:
        return None

    teams_data = _clickup_get("https://api.clickup.com/api/v2/team", token)
    teams = teams_data.get("teams", [])
    team_target = _normalize_name(workspace_name)
    team = next((t for t in teams if _normalize_name(str(t.get("name", "")).strip()) == team_target), None)
    if not team:
        raise RuntimeError(f"Workspace not found in ClickUp: '{workspace_name}'")
    team_id = str(team.get("id", "")).strip()

    spaces_data = _clickup_get(f"https://api.clickup.com/api/v2/team/{team_id}/space", token)
    spaces = spaces_data.get("spaces", [])
    space_target = _normalize_name(space_name)
    space = next((s for s in spaces if _normalize_name(str(s.get("name", "")).strip()) == space_target), None)
    if not space:
        raise RuntimeError(f"Space not found in workspace '{workspace_name}': '{space_name}'")
    space_id = str(space.get("id", "")).strip()

    lists_data = _clickup_get(f"https://api.clickup.com/api/v2/space/{space_id}/list", token)
    lists = lists_data.get("lists", [])
    list_target = _normalize_name(list_name)
    lst = next((l for l in lists if _normalize_name(str(l.get("name", "")).strip()) == list_target), None)
    if not lst:
        raise RuntimeError(
            f"List not found in workspace '{workspace_name}' / space '{space_name}': '{list_name}'"
        )
    return str(lst.get("id", "")).strip() or None


def get_priority_from_rating(rating: str) -> int:
    try:
        match = re.search(r"\d+(\.\d+)?", str(rating))
        rating_num = float(match.group(0)) if match else 0.0
        if rating_num <= 2:
            return 1
        if rating_num <= 3:
            return 2
        if rating_num <= 4:
            return 3
        return 4
    except Exception:
        return 3


def build_task_payload(
    location: LocationConfig,
    review: Dict,
    status: str,
    review_index: int,
) -> Dict:
    reviewer = str(review.get("name", "N/A"))
    review_date = str(review.get("date", "N/A"))
    rating = str(review.get("rating", "N/A"))
    review_text = str(review.get("text", "N/A"))
    review_link = review.get("link") or review.get("review_link") or review.get("url")
    if not review_link or str(review_link).strip() == "N/A":
        review_link = "N/A (link scraping failed)"

    title = f"{location.name} - Review {review_index}"

    description_lines = [
        f"Review from {location.name}",
        "",
        f"Name: {reviewer}",
        f"Date: {review_date}",
        f"Rating: {rating}",
        f"Review: {review_text}",
    ]

    description_lines.append(f"Link: {review_link}")

    return {
        "name": title[:255],
        "description": "\n".join(description_lines),
        "status": status,
        "priority": get_priority_from_rating(str(rating)),
        "tags": [],
    }


def upload_reviews_to_clickup(
    token: str,
    list_id: str,
    location: LocationConfig,
    group_name: str,
    reviews: List[Dict],
    status: str,
) -> Dict[str, int]:
    headers = {"Authorization": token, "Content-Type": "application/json"}
    success = 0
    failed = 0

    for i, review in enumerate(reviews, 1):
        payload = build_task_payload(location, review, status, i)
        response = requests.post(
            f"https://api.clickup.com/api/v2/list/{list_id}/task",
            headers=headers,
            json=payload,
            timeout=20,
        )
        if response.status_code == 200:
            success += 1
        else:
            failed += 1
            print(
                f"  ! Upload failed for '{location.name}' "
                f"(status={response.status_code}): {response.text[:200]}"
            )
    return {"success": success, "failed": failed}


def scrape_and_process_location(
    location: LocationConfig,
    group_name: str,
    days_back: int,
) -> List[Dict]:
    print(f"\n=== {group_name} | {location.name} ===")
    print(f"Scraping reviews with text from the last {days_back} day(s)...")
    raw_reviews = scrape_reviews_function_recent_with_text(location.url, days_back)
    if not raw_reviews:
        print(f"No reviews found in the last {days_back} day(s).")
        return []

    processed_reviews = process_reviews_function(raw_reviews)
    print(f"Scraped and processed {len(processed_reviews)} reviews for {location.name}.")
    return processed_reviews


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch scrape reviews in a configurable date window (with text) and upload to ClickUp."
    )
    parser.add_argument(
        "--date-window",
        choices=DATE_WINDOW_OPTIONS,
        default="Last week",
        help="How far back to collect reviews (text reviews only; months use 30-day periods).",
    )
    parser.add_argument("--makan-limit", type=int, default=None, help="Process only first N MAKAN locations.")
    parser.add_argument(
        "--parallel-scrapers",
        type=int,
        default=2,
        help="Number of parallel scraping windows (default: 2).",
    )
    parser.add_argument(
        "--skip-competitors",
        action="store_true",
        help="Skip competitor locations for this run.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    days_back = date_window_choice_to_days(args.date_window)
    print(f"Date window: {args.date_window} → last {days_back} day(s)")

    try:
        config = load_config(CONFIG_FILE)
    except FileNotFoundError as e:
        print(e)
        print("Fill the config file, then run the script again.")
        return
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in config file: {e}")
        return

    clickup_config = config.get("clickup", {})
    token = str(clickup_config.get("api_token", "")).strip()
    default_list_id = str(clickup_config.get("default_list_id", "")).strip() or None
    clickup_status = str(clickup_config.get("status", "to do")).strip() or "to do"
    workspace_name = str(clickup_config.get("workspace_name", "")).strip()
    space_name = str(clickup_config.get("space_name", "")).strip()
    makan_list_name = str(clickup_config.get("makan_list_name", "")).strip()
    competitor_list_name = str(clickup_config.get("competitor_list_name", "")).strip()

    if not token or token == "PASTE_CLICKUP_TOKEN_HERE":
        print("Please set clickup.api_token in last_7_days_batch_config.json")
        return

    makan_group_list_id: Optional[str] = None
    competitor_group_list_id: Optional[str] = None
    if workspace_name and space_name:
        try:
            if makan_list_name:
                makan_group_list_id = resolve_clickup_list_id_by_name(
                    token=token,
                    workspace_name=workspace_name,
                    space_name=space_name,
                    list_name=makan_list_name,
                )
                print(
                    f"Resolved MAKAN list '{makan_list_name}' "
                    f"to id '{makan_group_list_id}'."
                )
            if competitor_list_name:
                competitor_group_list_id = resolve_clickup_list_id_by_name(
                    token=token,
                    workspace_name=workspace_name,
                    space_name=space_name,
                    list_name=competitor_list_name,
                )
                print(
                    f"Resolved competitor list '{competitor_list_name}' "
                    f"to id '{competitor_group_list_id}'."
                )
        except Exception as e:
            print(f"Failed resolving ClickUp list IDs by names: {e}")
            print("You can set clickup.default_list_id or per-location clickup_list_id as fallback.")

    makan_locations = parse_locations(config.get("makan_locations", []))
    competitor_locations = parse_locations(config.get("competitor_locations", []))

    if args.makan_limit is not None and args.makan_limit >= 0:
        makan_locations = makan_locations[: args.makan_limit]
        print(f"Run option: processing first {len(makan_locations)} MAKAN locations only.")

    if args.skip_competitors:
        competitor_locations = []
        print("Run option: competitor locations are skipped.")

    if not makan_locations and not competitor_locations:
        print("No locations found. Add entries under makan_locations/competitor_locations.")
        return

    totals = {"scraped": 0, "uploaded": 0, "failed": 0}
    jobs: List[Tuple[LocationConfig, str, Optional[str]]] = []
    jobs.extend(
        [(loc, "MAKAN", loc.clickup_list_id or makan_group_list_id or default_list_id) for loc in makan_locations]
    )
    jobs.extend(
        [
            (loc, "COMPETITOR", loc.clickup_list_id or competitor_group_list_id or default_list_id)
            for loc in competitor_locations
        ]
    )

    max_workers = max(1, int(args.parallel_scrapers))
    print(f"Running with {max_workers} parallel scraping window(s).")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(scrape_and_process_location, location, group_name, days_back): (
                location,
                group_name,
                list_id,
            )
            for location, group_name, list_id in jobs
        }

        for future in as_completed(future_map):
            location, group_name, list_id = future_map[future]

            if not list_id:
                print(f"- Skipping upload for '{location.name}' ({group_name}): no ClickUp list id set")
                continue

            try:
                processed_reviews = future.result()
            except Exception as e:
                print(f"! Scraping failed for '{location.name}' ({group_name}): {e}")
                continue

            totals["scraped"] += len(processed_reviews)
            if not processed_reviews:
                continue

            print(
                f"Uploading {len(processed_reviews)} reviews for '{location.name}' "
                f"to ClickUp list {list_id}..."
            )
            upload_result = upload_reviews_to_clickup(
                token=token,
                list_id=list_id,
                location=location,
                group_name=group_name,
                reviews=processed_reviews,
                status=clickup_status,
            )
            print(
                f"Upload done for '{location.name}': "
                f"{upload_result['success']} success, {upload_result['failed']} failed."
            )
            totals["uploaded"] += upload_result["success"]
            totals["failed"] += upload_result["failed"]

    print("\n===== Batch Finished =====")
    print(f"Total scraped:  {totals['scraped']}")
    print(f"Total uploaded: {totals['uploaded']}")
    print(f"Total failed:   {totals['failed']}")


if __name__ == "__main__":
    main()
