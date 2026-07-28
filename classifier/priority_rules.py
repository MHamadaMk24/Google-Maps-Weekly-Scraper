"""Rules-first Priority from Topic + Sentiment Score."""

from __future__ import annotations

HIGH_TOPICS = frozenset({"Crowded Time", "Staff"})

PRIORITY_HIGH = "High"
PRIORITY_MEDIUM = "Medium"
PRIORITY_LOW = "Low"


def get_priority(topic: str, sentiment_score: int | float | str | None) -> str:
    """
    Derive Priority from final Topic + Sentiment Score (no ML).

    Rules (v1):
    - score ≤ 2 → High
    - Topic in {Crowded Time, Staff} and score ≤ 3 → High
    - score ≤ 6 → Medium
    - else → Low
    """
    topic_value = str(topic or "").strip()
    try:
        score = int(float(sentiment_score))
    except (TypeError, ValueError):
        return PRIORITY_MEDIUM

    if score <= 2:
        return PRIORITY_HIGH
    if topic_value in HIGH_TOPICS and score <= 3:
        return PRIORITY_HIGH
    if score <= 6:
        return PRIORITY_MEDIUM
    return PRIORITY_LOW
