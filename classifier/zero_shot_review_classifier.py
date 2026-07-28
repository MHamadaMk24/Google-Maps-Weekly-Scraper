"""
Three-stage ensemble review classification.

Stage 1: Classify all reviews for Parking Related (3-model ensemble).
Stage 2: Keep only parking reviews, then classify Sentiment on that subset.
Stage 3: Classify Topic + Sub_Topic on parking rows (3-model ensemble).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from ensemble_classifier import (
    MODEL_KEYS,
    default_inference_batch_size,
    device_label,
    ensemble_parking,
    ensemble_sentiment,
    run_parking_pass,
    run_sentiment_pass,
    run_topic_pass,
)
from model_paths import all_models_cached_locally, local_models_dir
from review_text_normalizer import (
    build_normalization_context,
    normalize_review_text,
    parking_rule_override,
)
from escalation_rules import escalate_to
from priority_rules import get_priority
from review_date import ensure_review_date
from sentiment_classifier import build_sentiment_text, sentiment_score_1_to_10
from topic_classifier import build_topic_text, ensemble_topic

DEFAULT_CORRECTIONS_FILE = Path(__file__).resolve().parent.parent / "data" / "learned_corrections.json"

# Final export columns (current names kept). Relative `date` / mall_name / needs_review excluded.
OUTPUT_COLUMNS = (
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
)

# Merge/split routing only — preserved through classify, dropped before ClickUp upload.
CATEGORY_COLUMN = "pipeline_category"


def _finalize_output(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure Review_Date and return only the fixed final column set/order."""
    prepared = ensure_review_date(df)
    for col in OUTPUT_COLUMNS:
        if col not in prepared.columns:
            prepared[col] = ""
    cols = list(OUTPUT_COLUMNS)
    # Keep category identifier when present (combined weekly classify pass).
    if CATEGORY_COLUMN in prepared.columns:
        cols.append(CATEGORY_COLUMN)
    return prepared[cols]



def process_reviews(
    input_path: Path,
    output_path: Path,
    corrections_file: Path | None = DEFAULT_CORRECTIONS_FILE,
    min_batch_count: int = 2,
    keep_all: bool = False,
    inference_batch_size: int | None = None,
) -> pd.DataFrame:
    df = pd.read_csv(input_path)
    if "text" not in df.columns:
        raise ValueError("Input CSV must contain a 'text' column.")

    texts = df["text"].fillna("").astype(str).tolist()
    ratings = df["rating"].tolist() if "rating" in df.columns else [None] * len(df)
    total = len(texts)

    batch_size = inference_batch_size or default_inference_batch_size()

    print(f"Loaded {total} reviews from {input_path}")
    print(f"Inference device: {device_label()}")
    print(f"Inference batch size: {batch_size}")
    if all_models_cached_locally():
        print(f"Model source: local ({local_models_dir()})")
    else:
        print("Model source: Hugging Face hub (run download_models.py once to avoid network checks)")

    print("Building dynamic normalization context from batch...")
    batch_corrections = build_normalization_context(
        texts,
        corrections_file=corrections_file,
        min_batch_count=min_batch_count,
    )

    prepared_texts = [
        normalize_review_text(text, batch_corrections=batch_corrections) for text in texts
    ]
    parking_override_flags = [parking_rule_override(text) for text in texts]

    # --- Stage 1: parking (one model at a time — load, classify, unload) ---
    print(f"\n=== Stage 1: Parking classification ({total} reviews) ===")
    print("RAM-safe mode: one model in memory at a time (batch size default 16)")
    parking_model_results: dict[str, list] = {}
    for model_key in MODEL_KEYS:
        p_labels, p_scores = run_parking_pass(model_key, prepared_texts, batch_size)
        parking_model_results[f"parking_{model_key}"] = p_labels
        parking_model_results[f"parking_{model_key}_score"] = p_scores

    final_parking: list[str] = []

    for i in range(total):
        parking_vote_map = {key: parking_model_results[f"parking_{key}"][i] for key in MODEL_KEYS}
        parking_score_map = {
            key: parking_model_results[f"parking_{key}_score"][i] for key in MODEL_KEYS
        }
        parking, _parking_score, parking_agreement = ensemble_parking(
            parking_vote_map,
            parking_score_map,
            parking_override_flags[i],
        )

        final_parking.append(parking)

        if i == 0 or (i + 1) % 10 == 0 or (i + 1) == total:
            print(f"  [stage 1] {i + 1}/{total} parking={parking} ({parking_agreement})")

    stage1 = df.copy()
    stage1["Parking Related"] = final_parking

    parking_mask = stage1["Parking Related"] == "Parking Related"
    parking_count = int(parking_mask.sum())
    general_count = total - parking_count
    print(f"\nStage 1 complete: {parking_count} parking-related, {general_count} general (dropped)")

    if parking_count == 0:
        print("No parking-related reviews found. Writing empty output.")
        empty = _finalize_output(stage1.loc[parking_mask].copy())
        empty.to_csv(output_path, index=False, encoding="utf-8-sig")
        return empty

    parking_prepared = [prepared_texts[i] for i in range(total) if parking_mask.iloc[i]]
    parking_ratings = [ratings[i] for i in range(total) if parking_mask.iloc[i]]
    sentiment_inputs = [
        build_sentiment_text(prepared, rating)
        for prepared, rating in zip(parking_prepared, parking_ratings)
    ]

    # --- Stage 2: sentiment on parking rows only (one model at a time) ---
    print(f"\n=== Stage 2: Sentiment classification ({parking_count} parking reviews) ===")
    sentiment_model_results: dict[str, list] = {}
    for model_key in MODEL_KEYS:
        s_labels, s_scores = run_sentiment_pass(model_key, sentiment_inputs, batch_size)
        sentiment_model_results[f"sentiment_{model_key}"] = s_labels
        sentiment_model_results[f"sentiment_{model_key}_score"] = s_scores

    final_sentiment: list[str] = []
    final_sentiment_scores: list[int] = []

    for i in range(parking_count):
        sentiment_vote_map = {
            key: sentiment_model_results[f"sentiment_{key}"][i] for key in MODEL_KEYS
        }
        sentiment_score_map = {
            key: sentiment_model_results[f"sentiment_{key}_score"][i] for key in MODEL_KEYS
        }
        sentiment, ensemble_confidence, sentiment_agreement = ensemble_sentiment(
            sentiment_vote_map,
            sentiment_score_map,
            parking_prepared[i],
            parking_ratings[i],
            parking_related=True,
        )
        score_1_10 = sentiment_score_1_to_10(sentiment, ensemble_confidence)

        final_sentiment.append(sentiment)
        final_sentiment_scores.append(score_1_10)

        if i == 0 or (i + 1) % 5 == 0 or (i + 1) == parking_count:
            print(
                f"  [stage 2] {i + 1}/{parking_count} "
                f"sentiment={sentiment} score={score_1_10} ({sentiment_agreement})"
            )

    topic_inputs = [build_topic_text(prepared) for prepared in parking_prepared]

    # --- Stage 3: topic on parking rows only (one model at a time) ---
    print(f"\n=== Stage 3: Topic classification ({parking_count} parking reviews) ===")
    topic_model_results: dict[str, list[tuple[list[str], list[float]]]] = {}
    for model_key in MODEL_KEYS:
        topic_model_results[model_key] = run_topic_pass(model_key, topic_inputs, batch_size)

    final_topics: list[str] = []
    final_sub_topics: list[str] = []

    for i in range(parking_count):
        model_rankings = {key: topic_model_results[key][i] for key in MODEL_KEYS}
        topic, sub_topic, _topic_confidence, topic_agreement, _topic_review = ensemble_topic(
            model_rankings
        )

        final_topics.append(topic)
        final_sub_topics.append(sub_topic)

        if i == 0 or (i + 1) % 5 == 0 or (i + 1) == parking_count:
            sub_display = sub_topic or "(none)"
            print(
                f"  [stage 3] {i + 1}/{parking_count} "
                f"topic={topic} sub={sub_display} ({topic_agreement})"
            )

    final_escalated = [
        escalate_to(topic, sentiment)
        for topic, sentiment in zip(final_topics, final_sentiment)
    ]
    final_priority = [
        get_priority(topic, score)
        for topic, score in zip(final_topics, final_sentiment_scores)
    ]

    parking_df = stage1.loc[parking_mask].copy().reset_index(drop=True)
    parking_df["Sentiment"] = final_sentiment
    parking_df["Sentiment Score"] = final_sentiment_scores
    parking_df["Topic"] = final_topics
    parking_df["Sub_Topic"] = [sub or "" for sub in final_sub_topics]
    parking_df["Escalated To"] = final_escalated
    parking_df["Priority"] = final_priority

    if keep_all:
        out = df.copy()
        out["Parking Related"] = final_parking
        out["Sentiment"] = ""
        out["Sentiment Score"] = ""
        out["Topic"] = ""
        out["Sub_Topic"] = ""
        out["Escalated To"] = ""
        out["Priority"] = ""

        parking_indices = [i for i in range(total) if parking_mask.iloc[i]]
        for out_idx, src_idx in enumerate(parking_indices):
            out.at[src_idx, "Sentiment"] = parking_df.at[out_idx, "Sentiment"]
            out.at[src_idx, "Sentiment Score"] = parking_df.at[out_idx, "Sentiment Score"]
            out.at[src_idx, "Topic"] = parking_df.at[out_idx, "Topic"]
            out.at[src_idx, "Sub_Topic"] = parking_df.at[out_idx, "Sub_Topic"]
            out.at[src_idx, "Escalated To"] = parking_df.at[out_idx, "Escalated To"]
            out.at[src_idx, "Priority"] = parking_df.at[out_idx, "Priority"]
    else:
        out = parking_df

    out = _finalize_output(out)
    out.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"\nSaved to {output_path}")
    if keep_all:
        print(f"Output: all {total} reviews ({parking_count} with sentiment, {general_count} parking-only)")
    else:
        print(f"Output: {parking_count} parking-related reviews only ({general_count} general removed)")
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Three-stage ensemble: parking filter, sentiment, then topic"
    )
    parser.add_argument("--input", required=True, help="Input reviews CSV")
    parser.add_argument(
        "--output",
        help="Output CSV (default: input stem + _parking_classified.csv)",
    )
    parser.add_argument(
        "--corrections-file",
        default=str(DEFAULT_CORRECTIONS_FILE),
        help="Learned corrections JSON (pass empty to disable)",
    )
    parser.add_argument("--min-batch-count", type=int, default=2)
    parser.add_argument(
        "--keep-all",
        action="store_true",
        help="Keep general reviews in output (no sentiment columns for them)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Inference batch size (default: 16; use 32 for faster runs if RAM allows)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = (
        Path(args.output)
        if args.output
        else input_path.with_name(f"{input_path.stem}_parking_classified.csv")
    )
    corrections_path = Path(args.corrections_file) if args.corrections_file else None

    process_reviews(
        input_path,
        output_path,
        corrections_file=corrections_path,
        min_batch_count=args.min_batch_count,
        keep_all=args.keep_all,
        inference_batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
