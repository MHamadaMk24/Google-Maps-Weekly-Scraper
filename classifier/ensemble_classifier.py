"""Shared model config, inference helpers, and Phase 2 ensemble fusion."""

from __future__ import annotations

import gc
from collections import Counter
from statistics import median
from typing import Any

import torch
from transformers import pipeline

from model_paths import MODEL_KEYS, MODEL_MAX_CHARS, resolve_model_source
from sentiment_classifier import (
    SENTIMENT_INDEX,
    SENTIMENT_LABELS,
    SENTIMENT_HYPOTHESIS,
    calibrate_sentiment,
)

from topic_classifier import TOPIC_HYPOTHESIS, TOPIC_LABELS, build_topic_text

PARKING_LABELS = ["Parking Related", "General"]
PARKING_HYPOTHESIS = "This mall review is about {}."

TIEBREAKER_ORDER = ["deberta_large", "deberta_base", "bart"]

DEFAULT_BATCH_SIZE_CPU = 16
DEFAULT_BATCH_SIZE_GPU = 16
RECOMMENDED_BATCH_SIZE_CPU = 16


def default_inference_batch_size() -> int:
    return DEFAULT_BATCH_SIZE_GPU if torch.cuda.is_available() else DEFAULT_BATCH_SIZE_CPU


def device_label() -> str:
    if torch.cuda.is_available():
        return f"GPU ({torch.cuda.get_device_name(0)})"
    return "CPU"


def load_classifier(model_key: str):
    model_source, is_local = resolve_model_source(model_key)
    device = 0 if torch.cuda.is_available() else -1
    kwargs: dict[str, Any] = {"model": model_source, "device": device}
    if is_local:
        kwargs["local_files_only"] = True
    return pipeline("zero-shot-classification", **kwargs)


def unload_classifier(classifier) -> None:
    del classifier
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _print_model_load(model_key: str) -> None:
    model_source, is_local = resolve_model_source(model_key)
    source_label = "local" if is_local else "hub"
    print(f"\nLoading {model_key} ({source_label}: {model_source})...")


def classify_raw_batch(
    classifier,
    texts: list[str],
    labels: list[str],
    max_chars: int,
    hypothesis_template: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE_CPU,
) -> tuple[list[str], list[float]]:
    default_label = "General" if "General" in labels else "Normal"
    result_labels = [default_label] * len(texts)
    result_scores = [0.0] * len(texts)

    work_indices: list[int] = []
    work_texts: list[str] = []
    for index, text in enumerate(texts):
        cleaned = str(text).strip()[:max_chars] if text and str(text).strip() else ""
        if cleaned:
            work_indices.append(index)
            work_texts.append(cleaned)

    if not work_texts:
        return result_labels, result_scores

    kwargs: dict = {
        "candidate_labels": labels,
        "multi_label": False,
        "batch_size": batch_size,
    }
    if hypothesis_template:
        kwargs["hypothesis_template"] = hypothesis_template

    outputs = classifier(work_texts, **kwargs)
    if isinstance(outputs, dict):
        outputs = [outputs]

    for index, output in zip(work_indices, outputs):
        result_labels[index] = output["labels"][0]
        result_scores[index] = round(float(output["scores"][0]), 4)

    return result_labels, result_scores


def classify_raw_batch_full(
    classifier,
    texts: list[str],
    labels: list[str],
    max_chars: int,
    hypothesis_template: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE_CPU,
) -> list[tuple[list[str], list[float]]]:
    """Return full label/score rankings for each input text."""
    default_label = "General" if "General" in labels else "Other"
    results: list[tuple[list[str], list[float]]] = [
        ([default_label], [0.0]) for _ in texts
    ]

    work_indices: list[int] = []
    work_texts: list[str] = []
    for index, text in enumerate(texts):
        cleaned = str(text).strip()[:max_chars] if text and str(text).strip() else ""
        if cleaned:
            work_indices.append(index)
            work_texts.append(cleaned)

    if not work_texts:
        return results

    kwargs: dict = {
        "candidate_labels": labels,
        "multi_label": False,
        "batch_size": batch_size,
    }
    if hypothesis_template:
        kwargs["hypothesis_template"] = hypothesis_template

    outputs = classifier(work_texts, **kwargs)
    if isinstance(outputs, dict):
        outputs = [outputs]

    for index, output in zip(work_indices, outputs):
        ranked_labels = [str(label) for label in output["labels"]]
        ranked_scores = [round(float(score), 4) for score in output["scores"]]
        results[index] = (ranked_labels, ranked_scores)

    return results


def agreement_label(votes: list[str]) -> str:
    top_count = Counter(votes).most_common(1)[0][1]
    return f"{top_count}/{len(votes)}"


def format_votes(votes: dict[str, str]) -> str:
    return " | ".join(f"{key}:{votes[key]}" for key in MODEL_KEYS)


def ensemble_parking(
    votes: dict[str, str],
    scores: dict[str, float],
    explicit_parking: bool,
) -> tuple[str, float, str]:
    if explicit_parking:
        return "Parking Related", 1.0, "rule"

    vote_list = [votes[key] for key in MODEL_KEYS]
    agreement = agreement_label(vote_list)
    counts = Counter(vote_list)
    winner, _count = counts.most_common(1)[0]

    agreeing_scores = [scores[key] for key in MODEL_KEYS if votes[key] == winner]
    final_score = round(sum(agreeing_scores) / len(agreeing_scores), 4)
    return winner, final_score, agreement


def ensemble_sentiment(
    votes: dict[str, str],
    scores: dict[str, float],
    prepared_text: str,
    rating_value: str | int | float | None,
    parking_related: bool,
) -> tuple[str, float, str]:
    vote_list = [votes[key] for key in MODEL_KEYS]
    agreement = agreement_label(vote_list)

    indices = [SENTIMENT_INDEX.get(vote, 2) for vote in vote_list]
    median_idx = int(median(indices))
    median_label = SENTIMENT_LABELS[median_idx]

    if len(set(vote_list)) == len(vote_list):
        median_label = votes[TIEBREAKER_ORDER[0]]
        median_idx = SENTIMENT_INDEX.get(median_label, 2)
        agreement = "1/3"

    closest_scores = [
        scores[key]
        for key in MODEL_KEYS
        if SENTIMENT_INDEX.get(votes[key], 2) == median_idx
    ]
    if not closest_scores:
        closest_scores = [scores[key] for key in MODEL_KEYS]
    ensemble_score = round(sum(closest_scores) / len(closest_scores), 4)

    final_label, final_score = calibrate_sentiment(
        median_label,
        ensemble_score,
        prepared_text,
        rating_value,
        parking_related,
    )
    return final_label, final_score, agreement


def _parking_with_classifier(
    classifier,
    model_key: str,
    prepared_texts: list[str],
    batch_size: int,
) -> tuple[list[str], list[float]]:
    max_chars = MODEL_MAX_CHARS[model_key]
    total = len(prepared_texts)
    print(f"  [{model_key} parking] classifying {total} reviews (batch_size={batch_size})...")
    parking_labels, parking_scores = classify_raw_batch(
        classifier,
        prepared_texts,
        PARKING_LABELS,
        max_chars,
        hypothesis_template=PARKING_HYPOTHESIS,
        batch_size=batch_size,
    )
    print(f"  [{model_key} parking] done ({total}/{total})")
    return parking_labels, parking_scores


def _sentiment_with_classifier(
    classifier,
    model_key: str,
    sentiment_inputs: list[str],
    batch_size: int,
) -> tuple[list[str], list[float]]:
    max_chars = MODEL_MAX_CHARS[model_key]
    total = len(sentiment_inputs)
    print(f"  [{model_key} sentiment] classifying {total} reviews (batch_size={batch_size})...")
    sentiment_labels, sentiment_scores = classify_raw_batch(
        classifier,
        sentiment_inputs,
        SENTIMENT_LABELS,
        max_chars,
        hypothesis_template=SENTIMENT_HYPOTHESIS,
        batch_size=batch_size,
    )
    print(f"  [{model_key} sentiment] done ({total}/{total})")
    return sentiment_labels, sentiment_scores


def run_parking_pass(
    model_key: str,
    prepared_texts: list[str],
    batch_size: int = DEFAULT_BATCH_SIZE_CPU,
) -> tuple[list[str], list[float]]:
    """Load model, classify parking, unload."""
    _print_model_load(model_key)
    classifier = load_classifier(model_key)
    try:
        return _parking_with_classifier(classifier, model_key, prepared_texts, batch_size)
    finally:
        unload_classifier(classifier)


def run_sentiment_pass(
    model_key: str,
    sentiment_inputs: list[str],
    batch_size: int = DEFAULT_BATCH_SIZE_CPU,
) -> tuple[list[str], list[float]]:
    """Load model, classify sentiment, unload."""
    _print_model_load(model_key)
    classifier = load_classifier(model_key)
    try:
        return _sentiment_with_classifier(classifier, model_key, sentiment_inputs, batch_size)
    finally:
        unload_classifier(classifier)


def _topic_with_classifier(
    classifier,
    model_key: str,
    topic_inputs: list[str],
    batch_size: int,
) -> list[tuple[list[str], list[float]]]:
    max_chars = MODEL_MAX_CHARS[model_key]
    total = len(topic_inputs)
    print(f"  [{model_key} topic] classifying {total} reviews (batch_size={batch_size})...")
    rankings = classify_raw_batch_full(
        classifier,
        topic_inputs,
        TOPIC_LABELS,
        max_chars,
        hypothesis_template=TOPIC_HYPOTHESIS,
        batch_size=batch_size,
    )
    print(f"  [{model_key} topic] done ({total}/{total})")
    return rankings


def run_topic_pass(
    model_key: str,
    topic_inputs: list[str],
    batch_size: int = DEFAULT_BATCH_SIZE_CPU,
) -> list[tuple[list[str], list[float]]]:
    """Load model, classify topic rankings, unload."""
    _print_model_load(model_key)
    classifier = load_classifier(model_key)
    try:
        return _topic_with_classifier(classifier, model_key, topic_inputs, batch_size)
    finally:
        unload_classifier(classifier)


def run_parking_pass_keep_loaded(
    model_key: str,
    prepared_texts: list[str],
    batch_size: int = DEFAULT_BATCH_SIZE_CPU,
):
    """Load model, classify parking, return results and the loaded classifier."""
    _print_model_load(model_key)
    classifier = load_classifier(model_key)
    parking_labels, parking_scores = _parking_with_classifier(
        classifier, model_key, prepared_texts, batch_size
    )
    return parking_labels, parking_scores, classifier


def run_sentiment_pass_with_classifier(
    classifier,
    model_key: str,
    sentiment_inputs: list[str],
    batch_size: int = DEFAULT_BATCH_SIZE_CPU,
) -> tuple[list[str], list[float]]:
    """Classify sentiment with an already-loaded classifier."""
    return _sentiment_with_classifier(classifier, model_key, sentiment_inputs, batch_size)


def run_model_pass(
    model_key: str,
    prepared_texts: list[str],
    sentiment_inputs: list[str],
    batch_size: int = DEFAULT_BATCH_SIZE_CPU,
) -> tuple[list[str], list[float], list[str], list[float]]:
    """Load once; run parking then sentiment before unloading."""
    _print_model_load(model_key)
    classifier = load_classifier(model_key)
    try:
        parking_labels, parking_scores = _parking_with_classifier(
            classifier, model_key, prepared_texts, batch_size
        )
        sentiment_labels, sentiment_scores = _sentiment_with_classifier(
            classifier, model_key, sentiment_inputs, batch_size
        )
        return parking_labels, parking_scores, sentiment_labels, sentiment_scores
    finally:
        unload_classifier(classifier)
