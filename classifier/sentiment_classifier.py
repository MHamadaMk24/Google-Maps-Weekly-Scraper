"""Rating-aware sentiment classification helpers."""

from __future__ import annotations

import re

SENTIMENT_LABELS = ["very negative", "Negative", "Normal", "Positive", "very positive"]
SENTIMENT_INDEX = {label: index for index, label in enumerate(SENTIMENT_LABELS)}
SENTIMENT_HYPOTHESIS = "Based on the star rating and review text, the sentiment is {}."

# Sentiment → 1–10 score bands (final Sentiment + ensemble confidence).
SENTIMENT_SCORE_BANDS: dict[str, tuple[int, int]] = {
    "very negative": (1, 2),
    "Negative": (3, 4),
    "Normal": (5, 6),
    "Positive": (7, 8),
    "very positive": (9, 10),
}

STAR_RATING_RE = re.compile(r"(\d)\s*stars?", re.IGNORECASE)
COMPLAINT_RE = re.compile(
    r"\b(?:"
    r"should not|shouldn't|must not|too high|higher|exploited|not good|frustrat\w*|"
    r"annoying|never (?:go|return)|don't|do not|beware|drawback|limited options|"
    r"no air|unbearable|forcibly|complain\w*|disappoint\w*|awful|horrible|terrible|"
    r"last time|rip[\s-]?off|overpriced|worse|bad\b|hot!|dying|disturbing|"
    r"unavailable|negatively|negatively impacted|waited over|encroach\w*|"
    r"should consider|should not charge|slightly higher|wouldn't|couldn't"
    r")\b",
    re.IGNORECASE,
)

STRONG_COMPLAINT_RE = re.compile(
    r"\b(?:"
    r"very negative|never go|never return|awful|horrible|terrible|exploited|"
    r"forcibly|unbearable|dying|disturbing|rip[\s-]?off|should not charge|last time"
    r")\b",
    re.IGNORECASE,
)


def parse_star_rating(rating_value: str | int | float | None) -> int | None:
    if rating_value is None:
        return None

    if isinstance(rating_value, (int, float)) and 1 <= int(rating_value) <= 5:
        return int(rating_value)

    match = STAR_RATING_RE.search(str(rating_value))
    if match:
        stars = int(match.group(1))
        if 1 <= stars <= 5:
            return stars
    return None


def _clamp_index(value: int) -> int:
    return max(0, min(len(SENTIMENT_LABELS) - 1, value))


def _rating_anchor_index(stars: int) -> int:
    return _clamp_index(stars - 1)


def _complaint_adjustment(text: str) -> int:
    complaints = COMPLAINT_RE.findall(text)
    if not complaints:
        return 0

    penalty = 1
    if len(complaints) >= 3:
        penalty += 1
    if STRONG_COMPLAINT_RE.search(text):
        penalty += 1
    return min(penalty, 2)


def calibrate_sentiment(
    model_label: str,
    model_score: float,
    prepared_text: str,
    rating_value: str | int | float | None,
    parking_related: bool,
) -> tuple[str, float]:
    stars = parse_star_rating(rating_value)
    model_idx = SENTIMENT_INDEX.get(model_label, 2)
    final_idx = model_idx
    complaint_penalty = _complaint_adjustment(prepared_text)

    if stars is not None:
        anchor_idx = _rating_anchor_index(stars)

        if stars == 1:
            final_idx = min(final_idx, 0 if complaint_penalty else 1)
        elif stars == 2:
            final_idx = min(final_idx, 1 if complaint_penalty else 2)
        elif stars == 3:
            if complaint_penalty:
                final_idx = min(final_idx, anchor_idx)
            else:
                final_idx = min(max(final_idx, anchor_idx - 1), anchor_idx + 1)
        elif complaint_penalty:
            final_idx = min(final_idx, anchor_idx - complaint_penalty)
            if parking_related:
                final_idx = min(final_idx, SENTIMENT_INDEX["Negative"])
            else:
                final_idx = min(final_idx, SENTIMENT_INDEX["Normal"])
        else:
            final_idx = max(final_idx, anchor_idx - 1)
    elif complaint_penalty:
        final_idx -= complaint_penalty

    final_idx = _clamp_index(final_idx)
    final_label = SENTIMENT_LABELS[final_idx]

    if final_label == model_label:
        return final_label, model_score

    # Slightly lower confidence when calibration overrides the model.
    return final_label, round(min(model_score, 0.85), 4)


def build_sentiment_text(prepared_text: str, rating_value: str | int | float | None) -> str:
    stars = parse_star_rating(rating_value)
    if stars is None:
        return prepared_text
    return f"Customer gave {stars} out of 5 stars. {prepared_text}"


def sentiment_score_1_to_10(sentiment_label: str, confidence: float) -> int:
    """
    Map final Sentiment + ensemble confidence to a 1–10 score.

    Score = round(Lower + Confidence × (Upper − Lower)), clamped to 1–10.
    Missing confidence defaults to mid-band (0.5).
    """
    lower, upper = SENTIMENT_SCORE_BANDS.get(sentiment_label, (5, 6))
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.5
    if conf != conf:  # NaN
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    score = round(lower + conf * (upper - lower))
    return max(1, min(10, score))
