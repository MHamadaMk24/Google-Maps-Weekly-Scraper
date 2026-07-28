"""Topic classification helpers for parking-related reviews."""

from __future__ import annotations

from collections import Counter

from model_paths import MODEL_KEYS

TOPIC_LABELS = [
    "Staff",
    "Cleanliness",
    "Free Parking Time",
    "Crowded Time",
    "Price",
    "Facilities",
    "Other",
]

TOPIC_HYPOTHESIS = (
    "In this parking-related mall review, the main subject is {} "
    "(staff and attendants, cleanliness, free parking hours duration, "
    "crowding or queues at entry or exit, parking fees and pricing, "
    "or parking facilities such as gates, spaces, cameras, and safety)."
)

# Primary label forced to Other when ensemble confidence is this low.
OTHER_SCORE_THRESHOLD = 0.20

# Sub-topic must clear an absolute floor and a fraction of the primary score.
SUB_TOPIC_MIN_SCORE = 0.18
SUB_TOPIC_MIN_RATIO = 0.35

TIEBREAKER_ORDER = ["deberta_large", "deberta_base", "bart"]


def build_topic_text(prepared_text: str) -> str:
    """Use normalized review text as-is (parking rows only)."""
    return prepared_text


def _score_for_label(labels: list[str], scores: list[float], label: str) -> float:
    for candidate, score in zip(labels, scores):
        if candidate == label:
            return float(score)
    return 0.0


def _aggregate_label_scores(
    model_rankings: dict[str, tuple[list[str], list[float]]],
) -> dict[str, float]:
    totals: dict[str, float] = {label: 0.0 for label in TOPIC_LABELS}
    counts: dict[str, int] = {label: 0 for label in TOPIC_LABELS}

    for key in MODEL_KEYS:
        labels, scores = model_rankings[key]
        for label, score in zip(labels, scores):
            if label in totals:
                totals[label] += float(score)
                counts[label] += 1

    return {
        label: totals[label] / counts[label]
        for label in TOPIC_LABELS
        if counts[label] > 0
    }


def _majority_primary(votes: dict[str, str]) -> tuple[str, str]:
    vote_list = [votes[key] for key in MODEL_KEYS]
    counts = Counter(vote_list)
    top_count = counts.most_common(1)[0][1]
    agreement = f"{top_count}/{len(vote_list)}"

    winners = [label for label, count in counts.items() if count == top_count]
    if len(winners) == 1:
        return winners[0], agreement

    for key in TIEBREAKER_ORDER:
        if votes[key] in winners:
            return votes[key], "1/3"
    return winners[0], "1/3"


def _primary_confidence(
    primary: str,
    model_rankings: dict[str, tuple[list[str], list[float]]],
) -> float:
    scores = [
        _score_for_label(*model_rankings[key], primary)
        for key in MODEL_KEYS
    ]
    return round(sum(scores) / len(scores), 4)


def _pick_sub_topic(
    primary: str,
    aggregated: dict[str, float],
    *,
    models_agree: bool,
) -> str:
    """
    Choose Sub_Topic as a helper signal (not an action trigger).

    When models agree on Topic: always take the next strongest label.
    When they disagree: only fill Sub_Topic if the secondary score is strong enough.
    Prefer real labels over Other; use Other only as a last resort when agreeing.
    """
    ranked = sorted(aggregated.items(), key=lambda item: item[1], reverse=True)
    primary_score = aggregated.get(primary, 0.0)

    real_candidates: list[tuple[str, float]] = []
    other_score: float | None = None
    for label, score in ranked:
        if label == primary:
            continue
        if label == "Other":
            other_score = score
            continue
        real_candidates.append((label, score))

    if models_agree:
        if real_candidates:
            return real_candidates[0][0]
        if other_score is not None:
            return "Other"
        return ""

    for label, score in real_candidates:
        if score < SUB_TOPIC_MIN_SCORE:
            return ""
        if primary_score > 0 and score < primary_score * SUB_TOPIC_MIN_RATIO:
            return ""
        return label
    return ""


def ensemble_topic(
    model_rankings: dict[str, tuple[list[str], list[float]]],
) -> tuple[str, str, float, str, bool]:
    """
    Fuse per-model topic rankings into Topic + Sub_Topic.

    Returns: topic, sub_topic, confidence, agreement, needs_review
    """
    top_votes = {key: model_rankings[key][0][0] for key in MODEL_KEYS}
    primary, agreement = _majority_primary(top_votes)
    confidence = _primary_confidence(primary, model_rankings)
    needs_review = len(set(top_votes.values())) > 1
    # 2/3 or 3/3 majority → always fill Sub_Topic as a helper signal
    models_agree = agreement in {"2/3", "3/3"}

    if primary != "Other" and confidence < OTHER_SCORE_THRESHOLD:
        primary = "Other"
        confidence = _primary_confidence("Other", model_rankings)

    aggregated = _aggregate_label_scores(model_rankings)
    sub_topic = _pick_sub_topic(primary, aggregated, models_agree=models_agree)

    return primary, sub_topic, confidence, agreement, needs_review
