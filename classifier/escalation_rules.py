"""Rules-first Escalated To routing from Topic + Sentiment."""

from __future__ import annotations

NEGATIVE_SENTIMENTS = frozenset({"Negative", "very negative"})
NO_NEED_SENTIMENTS = frozenset({"Normal", "Positive", "very positive"})

FINANCIAL_TOPICS = frozenset({"Price"})
OPERATIONAL_TOPICS = frozenset(
    {
        "Staff",
        "Cleanliness",
        "Free Parking Time",
        "Crowded Time",
        "Facilities",
    }
)

ESCALATED_FINANCIAL = "Financial Team"
ESCALATED_OPERATIONAL = "Operational Team"
ESCALATED_NO_NEED = "No Need"


def escalate_to(topic: str, sentiment: str) -> str:
    """
    Derive Escalated To from final Topic + Sentiment (no ML).

    Rules (v1):
    - Price + Neg/very neg → Financial Team
    - Staff/Cleanliness/Free Parking Time/Crowded Time/Facilities + Neg/very neg
      → Operational Team
    - Normal/Positive/very positive, Other topic, or unclear → No Need
    """
    topic_value = str(topic or "").strip()
    sentiment_value = str(sentiment or "").strip()

    if sentiment_value in NO_NEED_SENTIMENTS:
        return ESCALATED_NO_NEED

    if sentiment_value in NEGATIVE_SENTIMENTS:
        if topic_value in FINANCIAL_TOPICS:
            return ESCALATED_FINANCIAL
        if topic_value in OPERATIONAL_TOPICS:
            return ESCALATED_OPERATIONAL
        return ESCALATED_NO_NEED

    return ESCALATED_NO_NEED
