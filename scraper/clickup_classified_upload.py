from __future__ import annotations

import argparse
import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests


CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "last_7_days_batch_config.json"

FINAL_COLUMNS = [
    "id",
    "Review_Date",
    "name",
    "rating",
    "text",
    "link",
    "location_name",
    "Parking Related",
    "Sentiment",
    "Sentiment Score",
    "Topic",
    "Sub_Topic",
    "Escalated To",
    "Priority",
]

FIELD_NAME_CANDIDATES = {
    "id": ["ID", "Id"],
    "Review_Date": ["Review Date"],
    "name": ["Name"],
    "rating": ["Rating"],
    "text": ["Text"],
    "link": ["Link"],
    "location_name": ["Location Name"],
    "Parking Related": ["Parking Related"],
    "Sentiment": ["Sentiment"],
    "Sentiment Score": ["Sentiment Score"],
    "Topic": ["Topic"],
    "Sub_Topic": ["Sub Topic", "Sub_Topic"],
    "Escalated To": ["Escalated To"],
    "Priority": ["Priority"],
}

EXPECTED_FIELD_TYPES = {
    "id": {"short_text"},
    "Review_Date": {"date", "short_text"},
    "name": {"short_text"},
    "rating": {"short_text"},
    "text": {"text", "short_text"},
    "link": {"short_text", "url"},
    "location_name": {"short_text"},
    "Parking Related": {"short_text", "drop_down"},
    "Sentiment": {"short_text", "drop_down"},
    "Sentiment Score": {"number", "short_text"},
    "Topic": {"short_text", "drop_down"},
    "Sub_Topic": {"short_text", "drop_down"},
    "Escalated To": {"short_text", "drop_down"},
    "Priority": {"short_text", "drop_down"},
}


@dataclass
class LocationConfig:
    name: str
    url: str
    clickup_list_id: Optional[str] = None
    tag: Optional[str] = None


def load_config(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def apply_env_overrides(config: Dict) -> Dict:
    clickup = config.setdefault("clickup", {})

    env_token = os.getenv("CLICKUP_API_TOKEN", "").strip()
    if env_token:
        clickup["api_token"] = env_token

    env_default_list_id = os.getenv("CLICKUP_DEFAULT_LIST_ID", "").strip()
    if env_default_list_id:
        clickup["default_list_id"] = env_default_list_id

    env_workspace = os.getenv("CLICKUP_WORKSPACE_NAME", "").strip()
    if env_workspace:
        clickup["workspace_name"] = env_workspace

    env_space = os.getenv("CLICKUP_SPACE_NAME", "").strip()
    if env_space:
        clickup["space_name"] = env_space

    env_makan_list = os.getenv("CLICKUP_MAKAN_LIST_NAME", "").strip()
    if env_makan_list:
        clickup["makan_list_name"] = env_makan_list

    env_competitor_list = os.getenv("CLICKUP_COMPETITOR_LIST_NAME", "").strip()
    if env_competitor_list:
        clickup["competitor_list_name"] = env_competitor_list

    env_status = os.getenv("CLICKUP_STATUS", "").strip()
    if env_status:
        clickup["status"] = env_status

    return config


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


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def _clickup_get(url: str, token: str) -> Dict:
    headers = {"Authorization": token, "Content-Type": "application/json"}
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"ClickUp GET failed ({response.status_code}): {response.text[:250]}")
    return response.json()


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
    space = next((s for s in spaces if _normalize_name(str(s.get('name', '')).strip()) == space_target), None)
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


def load_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    missing = [col for col in FINAL_COLUMNS if col not in rows[0]]
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")
    return rows


def build_location_group_map(
    makan_locations: Iterable[LocationConfig],
    competitor_locations: Iterable[LocationConfig],
) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for loc in makan_locations:
        result[_normalize_name(loc.name)] = "MAKAN"
    for loc in competitor_locations:
        result[_normalize_name(loc.name)] = "COMPETITOR"
    return result


def choose_target_list_id(
    row: Dict[str, str],
    location_group_map: Dict[str, str],
    makan_list_id: Optional[str],
    competitor_list_id: Optional[str],
    default_list_id: Optional[str],
) -> Optional[str]:
    location_name = str(row.get("location_name", "")).strip()
    group = location_group_map.get(_normalize_name(location_name))
    if group == "MAKAN":
        return makan_list_id or default_list_id
    if group == "COMPETITOR":
        return competitor_list_id or default_list_id
    return default_list_id


def fetch_list_field_mapping(token: str, list_id: str) -> Dict[str, Dict]:
    fields = _clickup_get(f"https://api.clickup.com/api/v2/list/{list_id}/field", token).get("fields", [])
    by_name: Dict[str, List[Dict]] = {}
    for field in fields:
        by_name.setdefault(str(field.get("name", "")).strip(), []).append(field)

    mapping: Dict[str, Dict] = {}
    for csv_col, candidates in FIELD_NAME_CANDIDATES.items():
        matches: List[Dict] = []
        for candidate in candidates:
            matches.extend(by_name.get(candidate, []))
        if not matches:
            raise RuntimeError(f"ClickUp list {list_id} missing field for '{csv_col}'")

        preferred = [m for m in matches if str(m.get("type", "")).strip() in EXPECTED_FIELD_TYPES[csv_col]]
        # Prefer strongest type when multiple matches exist
        type_priority = {
            "Review_Date": ["date", "short_text"],
            "Sentiment Score": ["number", "short_text"],
            "text": ["text", "short_text"],
            "link": ["url", "short_text"],
        }
        chosen = None
        for preferred_type in type_priority.get(csv_col, []):
            typed = [m for m in preferred if str(m.get("type", "")).strip() == preferred_type]
            if typed:
                chosen = typed[0]
                break
        if chosen is None:
            chosen = preferred[0] if preferred else matches[0]
        mapping[csv_col] = chosen
    return mapping


def format_clickup_date(value: str) -> Optional[int]:
    """Convert Review_Date / relative date text to ClickUp date ms timestamp."""
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "n/a", "na", "-"}:
        return None

    # Preferred export format
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            continue

    # Relative Google Maps phrases (should already be converted, but harden upload)
    lower = text.lower()
    now = datetime.now()
    if "ago" in lower:
        day_match = re.findall(r"(\d+)\s*day", lower)
        if day_match and "week" not in lower:
            return int((now - timedelta(days=int(day_match[0]))).timestamp() * 1000)
        week_match = re.findall(r"(\d+)\s*week", lower)
        if week_match:
            return int((now - timedelta(weeks=int(week_match[0]))).timestamp() * 1000)
        hour_match = re.findall(r"(\d+)\s*hour", lower)
        if hour_match:
            return int((now - timedelta(hours=int(hour_match[0]))).timestamp() * 1000)
        if "a day ago" in lower:
            return int((now - timedelta(days=1)).timestamp() * 1000)
        if "a week ago" in lower:
            return int((now - timedelta(weeks=1)).timestamp() * 1000)
    if lower == "today":
        return int(now.timestamp() * 1000)
    if lower == "yesterday":
        return int((now - timedelta(days=1)).timestamp() * 1000)
    return None


def absolute_review_date_string(value: str) -> Optional[str]:
    """Normalize any date text to dd-Mon-YYYY for short_text ClickUp fields."""
    ms = format_clickup_date(value)
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000).strftime("%d-%b-%Y")


def convert_field_value(csv_col: str, value: str, field_type: str):
    text = str(value if value is not None else "").strip()
    if text.lower() in {"", "nan", "none", "n/a", "na", "-"}:
        return None

    if csv_col == "Review_Date":
        if field_type == "date":
            return format_clickup_date(text)
        # short_text (or unexpected type): always send absolute calendar date, never "X days ago"
        return absolute_review_date_string(text)

    if csv_col == "Sentiment Score" or field_type == "number":
        try:
            cleaned = text.replace(",", "").strip()
            number = float(cleaned)
            if number.is_integer():
                number = int(number)
            # ClickUp number fields need numeric; short_text needs string
            if csv_col == "Sentiment Score" and field_type != "number":
                return str(number)
            return number
        except ValueError:
            return None

    if csv_col == "link":
        if not text.startswith("http"):
            return None
        return text

    return text


def build_task_name(row: Dict[str, str], row_index: int) -> str:
    location = str(row.get("location_name", "")).strip() or "Location"
    raw_date = str(row.get("Review_Date", "")).strip()
    review_date = absolute_review_date_string(raw_date) or raw_date or "ReviewDate"
    reviewer = str(row.get("name", "")).strip() or f"Review {row_index}"
    return f"{location} - {review_date} - {reviewer}"[:255]


def build_task_description(row: Dict[str, str]) -> str:
    lines = [
        f"Location: {row.get('location_name', '')}",
        f"Reviewer: {row.get('name', '')}",
        f"Rating: {row.get('rating', '')}",
        f"Review Date: {row.get('Review_Date', '')}",
        "",
        str(row.get("text", "")).strip(),
    ]
    link = str(row.get("link", "")).strip()
    if link:
        lines.extend(["", f"Link: {link}"])
    return "\n".join(lines).strip()


def build_task_payload(
    row: Dict[str, str],
    row_index: int,
    status: str,
    field_mapping: Dict[str, Dict],
) -> Dict:
    custom_fields: List[Dict] = []
    skipped_fields: List[str] = []
    for csv_col in FINAL_COLUMNS:
        field = field_mapping[csv_col]
        field_id = str(field.get("id", "")).strip()
        field_type = str(field.get("type", "")).strip()
        value = convert_field_value(csv_col, row.get(csv_col, ""), field_type)
        if not field_id or value is None:
            skipped_fields.append(csv_col)
            continue
        custom_fields.append({"id": field_id, "value": value})

    if skipped_fields:
        print(
            f"WARNING: row {row_index} skipped fields ({len(skipped_fields)}): "
            f"{', '.join(skipped_fields)}"
        )

    return {
        "name": build_task_name(row, row_index),
        "description": build_task_description(row),
        "status": status,
        "custom_fields": custom_fields,
        "tags": [],
    }


def upload_classified_rows(
    token: str,
    rows: List[Dict[str, str]],
    makan_list_id: Optional[str],
    competitor_list_id: Optional[str],
    default_list_id: Optional[str],
    location_group_map: Dict[str, str],
    status: str,
    dry_run: bool = False,
) -> Dict[str, int]:
    headers = {"Authorization": token, "Content-Type": "application/json"}
    field_cache: Dict[str, Dict[str, Dict]] = {}
    success = 0
    failed = 0
    skipped = 0

    for index, row in enumerate(rows, start=1):
        list_id = choose_target_list_id(
            row=row,
            location_group_map=location_group_map,
            makan_list_id=makan_list_id,
            competitor_list_id=competitor_list_id,
            default_list_id=default_list_id,
        )
        if not list_id:
            skipped += 1
            _safe_print(f"- Skipping row {index}: no target ClickUp list for '{row.get('location_name', '')}'")
            continue

        if list_id not in field_cache:
            field_cache[list_id] = fetch_list_field_mapping(token, list_id)

        payload = build_task_payload(
            row=row,
            row_index=index,
            status=status,
            field_mapping=field_cache[list_id],
        )

        if dry_run:
            success += 1
            _safe_print(
                f"[dry-run] row {index} -> list {list_id} | "
                f"{payload['name']} | custom_fields={len(payload['custom_fields'])}"
            )
            continue

        response = requests.post(
            f"https://api.clickup.com/api/v2/list/{list_id}/task",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if response.status_code == 200:
            success += 1
        else:
            failed += 1
            _safe_print(f"! Upload failed for row {index} ({response.status_code}): {response.text[:250]}")

    return {"success": success, "failed": failed, "skipped": skipped}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload final classified CSV rows into ClickUp custom fields.")
    parser.add_argument("--input", required=True, help="Path to final classified CSV")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate routing and payloads without creating ClickUp tasks.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = apply_env_overrides(load_config(CONFIG_FILE))
    clickup = config.get("clickup", {})

    token = str(clickup.get("api_token", "")).strip()
    if not token:
        raise RuntimeError("ClickUp API token is missing.")

    workspace_name = str(clickup.get("workspace_name", "")).strip()
    space_name = str(clickup.get("space_name", "")).strip()
    makan_list_name = str(clickup.get("makan_list_name", "")).strip()
    competitor_list_name = str(clickup.get("competitor_list_name", "")).strip()
    default_list_id = str(clickup.get("default_list_id", "")).strip() or None
    status = str(clickup.get("status", "to do")).strip() or "to do"

    makan_locations = parse_locations(config.get("makan_locations", []))
    competitor_locations = parse_locations(config.get("competitor_locations", []))
    location_group_map = build_location_group_map(makan_locations, competitor_locations)

    makan_list_id = resolve_clickup_list_id_by_name(token, workspace_name, space_name, makan_list_name)
    competitor_list_id = resolve_clickup_list_id_by_name(token, workspace_name, space_name, competitor_list_name)

    rows = load_csv_rows(Path(args.input))
    result = upload_classified_rows(
        token=token,
        rows=rows,
        makan_list_id=makan_list_id,
        competitor_list_id=competitor_list_id,
        default_list_id=default_list_id,
        location_group_map=location_group_map,
        status=status,
        dry_run=args.dry_run,
    )
    print(
        f"Upload finished: success={result['success']} "
        f"failed={result['failed']} skipped={result['skipped']}"
    )
    if result["failed"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
