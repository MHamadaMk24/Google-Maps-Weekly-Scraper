"""Ensure absolute Review_Date for classified review outputs."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pandas as pd

_MISSING = frozenset({"", "nan", "none", "n/a", "na"})


def _is_missing(value: object) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    return str(value).strip().lower() in _MISSING


def parse_google_maps_review_date(date_str, reference=None):
    """Parse Google Maps relative/absolute date strings to datetime."""
    if _is_missing(date_str):
        return None

    try:
        original_date_str = str(date_str)
        date_str_l = original_date_str.strip().lower()
        now = reference or datetime.now()

        if "ago" in date_str_l:
            if "minute" in date_str_l:
                minutes = re.findall(r"(\d+)\s*minute", date_str_l)
                if minutes:
                    return now - timedelta(minutes=int(minutes[0]))
            elif "hour" in date_str_l:
                hours = re.findall(r"(\d+)\s*hour", date_str_l)
                if hours:
                    return now - timedelta(hours=int(hours[0]))
            elif "day" in date_str_l and "week" not in date_str_l:
                days = re.findall(r"(\d+)\s*day", date_str_l)
                if days:
                    return now - timedelta(days=int(days[0]))
                if "a day ago" in date_str_l:
                    return now - timedelta(days=1)
            elif "week" in date_str_l:
                weeks = re.findall(r"(\d+)\s*week", date_str_l)
                if weeks:
                    return now - timedelta(weeks=int(weeks[0]))
                if "a week ago" in date_str_l:
                    return now - timedelta(weeks=1)
            elif "month" in date_str_l:
                months = re.findall(r"(\d+)\s*month", date_str_l)
                if months:
                    return now - timedelta(days=int(months[0]) * 30)
                if "a month ago" in date_str_l:
                    return now - timedelta(days=30)

        for fmt in (
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%d-%m-%Y",
            "%d-%b-%Y",
            "%B %d, %Y",
            "%b %d, %Y",
        ):
            try:
                return datetime.strptime(original_date_str.strip(), fmt)
            except ValueError:
                continue
        return None
    except Exception:
        return None


def compute_review_date(date_str, scraped_at=None) -> str:
    """Convert relative/absolute date text to Review_Date like 20-Jun-2025."""
    reference = scraped_at or datetime.now()
    parsed = parse_google_maps_review_date(date_str, reference=reference)
    if parsed is None:
        return ""
    return parsed.strftime("%d-%b-%Y")


def ensure_review_date(df: pd.DataFrame, scraped_at=None) -> pd.DataFrame:
    """
    Ensure Review_Date exists for every row:
    1. Keep existing Review_Date when present
    2. Else compute from relative `date`
    3. Else forward-fill from the previous Review_Date found
    """
    out = df.copy()
    if "Review_Date" not in out.columns:
        out["Review_Date"] = ""

    computed: list[str] = []
    for _, row in out.iterrows():
        existing = row.get("Review_Date")
        if not _is_missing(existing):
            # Normalize absolute dates already present
            parsed = parse_google_maps_review_date(existing, reference=scraped_at)
            computed.append(parsed.strftime("%d-%b-%Y") if parsed else str(existing).strip())
            continue

        from_date = compute_review_date(row.get("date"), scraped_at=scraped_at) if "date" in out.columns else ""
        computed.append(from_date)

    # Forward-fill blanks from previous Review_Date
    last_seen = ""
    filled: list[str] = []
    for value in computed:
        if value and not _is_missing(value):
            last_seen = value
            filled.append(value)
        else:
            filled.append(last_seen)

    out["Review_Date"] = filled
    return out
