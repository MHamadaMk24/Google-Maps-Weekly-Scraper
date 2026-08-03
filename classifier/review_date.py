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


def _looks_relative(value: object) -> bool:
    """True for Google Maps relative phrases like '6 days ago' (never keep as Review_Date)."""
    if _is_missing(value):
        return True
    text = str(value).strip().lower()
    if "ago" in text:
        return True
    if text in {"today", "yesterday"}:
        return True
    return False


def _is_absolute_review_date(value: object) -> bool:
    """True only for calendar dates in the export format (e.g. 28-Jul-2026)."""
    if _is_missing(value) or _looks_relative(value):
        return False
    text = str(value).strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


def parse_google_maps_review_date(date_str, reference=None):
    """Parse Google Maps relative/absolute date strings to datetime."""
    if _is_missing(date_str):
        return None

    try:
        original_date_str = str(date_str)
        date_str_l = original_date_str.strip().lower()
        now = reference or datetime.now()

        if date_str_l in {"today"}:
            return now
        if date_str_l in {"yesterday"}:
            return now - timedelta(days=1)

        if "ago" in date_str_l:
            if "minute" in date_str_l:
                minutes = re.findall(r"(\d+)\s*minute", date_str_l)
                if minutes:
                    return now - timedelta(minutes=int(minutes[0]))
            if "hour" in date_str_l:
                hours = re.findall(r"(\d+)\s*hour", date_str_l)
                if hours:
                    return now - timedelta(hours=int(hours[0]))
            if "day" in date_str_l and "week" not in date_str_l:
                days = re.findall(r"(\d+)\s*day", date_str_l)
                if days:
                    return now - timedelta(days=int(days[0]))
                if "a day ago" in date_str_l:
                    return now - timedelta(days=1)
            if "week" in date_str_l:
                weeks = re.findall(r"(\d+)\s*week", date_str_l)
                if weeks:
                    return now - timedelta(weeks=int(weeks[0]))
                if "a week ago" in date_str_l:
                    return now - timedelta(weeks=1)
            if "month" in date_str_l:
                months = re.findall(r"(\d+)\s*month", date_str_l)
                if months:
                    return now - timedelta(days=int(months[0]) * 30)
                if "a month ago" in date_str_l:
                    return now - timedelta(days=30)
            if "year" in date_str_l:
                years = re.findall(r"(\d+)\s*year", date_str_l)
                if years:
                    return now - timedelta(days=int(years[0]) * 365)
                if "a year ago" in date_str_l:
                    return now - timedelta(days=365)

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
    Ensure Review_Date exists for every row as an absolute calendar date:
    1. Keep existing Review_Date only when it is already absolute
    2. Else compute from relative `date` / relative Review_Date text
    3. Else forward-fill from the previous absolute Review_Date found
    Never leave phrases like '6 days ago' in Review_Date.
    """
    out = df.copy()
    if "Review_Date" not in out.columns:
        out["Review_Date"] = ""

    computed: list[str] = []
    for _, row in out.iterrows():
        existing = row.get("Review_Date")
        absolute = ""
        if not _looks_relative(existing):
            parsed = parse_google_maps_review_date(existing, reference=scraped_at)
            if parsed is not None:
                absolute = parsed.strftime("%d-%b-%Y")

        if not absolute and not _is_missing(existing) and _looks_relative(existing):
            absolute = compute_review_date(existing, scraped_at=scraped_at)

        if not absolute and "date" in out.columns:
            absolute = compute_review_date(row.get("date"), scraped_at=scraped_at)

        computed.append(absolute)

    # Forward-fill blanks from previous absolute Review_Date
    last_seen = ""
    filled: list[str] = []
    for value in computed:
        if value and _is_absolute_review_date(value):
            last_seen = value
            filled.append(value)
        elif value and not _looks_relative(value):
            last_seen = value
            filled.append(value)
        else:
            filled.append(last_seen)

    out["Review_Date"] = filled
    return out
