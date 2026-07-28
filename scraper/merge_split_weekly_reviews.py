"""
Merge MAKAN + competitor weekly CSVs for one classify pass, then split again.

The category identifier (`pipeline_category`) is for merge/split routing only.
It must not be uploaded to ClickUp (split drops it from classified outputs).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

CATEGORY_COLUMN = "pipeline_category"
CATEGORY_MAKAN = "makan"
CATEGORY_COMPETITORS = "competitors"

# Columns expected on scraped weekly CSVs (classifier needs these + optional extras).
RAW_KEEP = (
    "id",
    "Review_Date",
    "name",
    "date",
    "rating",
    "text",
    "link",
    "location_name",
    "mall_name",
)


def _read_raw(path: Path, category: str) -> pd.DataFrame:
    if not path.exists():
        print(f"WARNING: missing file, treating as empty: {path}")
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    df = df.copy()
    df[CATEGORY_COLUMN] = category
    return df


def merge_reviews(
    makan_path: Path,
    competitors_path: Path,
    output_path: Path,
) -> pd.DataFrame:
    makan = _read_raw(makan_path, CATEGORY_MAKAN)
    competitors = _read_raw(competitors_path, CATEGORY_COMPETITORS)
    combined = pd.concat([makan, competitors], ignore_index=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(
        f"Merged {len(makan)} MAKAN + {len(competitors)} competitors "
        f"= {len(combined)} rows -> {output_path}"
    )
    print(f"Identifier column: {CATEGORY_COLUMN} ({CATEGORY_MAKAN}|{CATEGORY_COMPETITORS})")
    return combined


def split_classified(
    combined_path: Path,
    makan_output: Path,
    competitors_output: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not combined_path.exists():
        raise FileNotFoundError(f"Classified combined CSV not found: {combined_path}")

    df = pd.read_csv(combined_path)
    if CATEGORY_COLUMN not in df.columns:
        raise ValueError(
            f"Expected identifier column '{CATEGORY_COLUMN}' in {combined_path}. "
            "Classify the merged sheet so the column is preserved."
        )

    makan = df[df[CATEGORY_COLUMN].astype(str).str.strip().str.lower() == CATEGORY_MAKAN].copy()
    competitors = (
        df[df[CATEGORY_COLUMN].astype(str).str.strip().str.lower() == CATEGORY_COMPETITORS]
        .copy()
    )

    # Drop identifier — not part of ClickUp upload sheet.
    makan = makan.drop(columns=[CATEGORY_COLUMN])
    competitors = competitors.drop(columns=[CATEGORY_COLUMN])

    makan_output.parent.mkdir(parents=True, exist_ok=True)
    competitors_output.parent.mkdir(parents=True, exist_ok=True)
    makan.to_csv(makan_output, index=False, encoding="utf-8-sig")
    competitors.to_csv(competitors_output, index=False, encoding="utf-8-sig")

    print(f"Split MAKAN classified: {len(makan)} rows -> {makan_output}")
    print(f"Split competitors classified: {len(competitors)} rows -> {competitors_output}")
    print(f"Dropped '{CATEGORY_COLUMN}' from both outputs (ClickUp upload only).")
    return makan, competitors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge/split weekly review CSVs around a single classify pass"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    merge_p = sub.add_parser("merge", help="Combine MAKAN + competitors raw CSVs")
    merge_p.add_argument("--makan", required=True, type=Path)
    merge_p.add_argument("--competitors", required=True, type=Path)
    merge_p.add_argument("--output", required=True, type=Path)

    split_p = sub.add_parser("split", help="Split classified combined CSV; drop identifier")
    split_p.add_argument("--input", required=True, type=Path)
    split_p.add_argument("--makan-output", required=True, type=Path)
    split_p.add_argument("--competitors-output", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "merge":
        merge_reviews(args.makan, args.competitors, args.output)
    elif args.command == "split":
        split_classified(args.input, args.makan_output, args.competitors_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
