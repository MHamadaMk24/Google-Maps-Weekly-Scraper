"""
Merge MAKAN + competitor weekly CSVs for one classify pass, then split again.

The category identifier (`pipeline_category`) is for merge/split routing only.
It must not be uploaded to ClickUp (split drops it from classified outputs).

Uses the stdlib csv module so this can run before classifier deps (pandas) are installed.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

CATEGORY_COLUMN = "pipeline_category"
CATEGORY_MAKAN = "makan"
CATEGORY_COMPETITORS = "competitors"


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        print(f"WARNING: missing file, treating as empty: {path}")
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fieldnames, rows


def _write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in fieldnames})


def merge_reviews(
    makan_path: Path,
    competitors_path: Path,
    output_path: Path,
) -> None:
    makan_fields, makan_rows = _read_rows(makan_path)
    comp_fields, comp_rows = _read_rows(competitors_path)

    for row in makan_rows:
        row[CATEGORY_COLUMN] = CATEGORY_MAKAN
    for row in comp_rows:
        row[CATEGORY_COLUMN] = CATEGORY_COMPETITORS

    # Preserve column order: union of both sheets, category last.
    fieldnames: list[str] = []
    for col in makan_fields + comp_fields:
        if col not in fieldnames and col != CATEGORY_COLUMN:
            fieldnames.append(col)
    fieldnames.append(CATEGORY_COLUMN)

    combined = makan_rows + comp_rows
    _write_rows(output_path, fieldnames, combined)
    print(
        f"Merged {len(makan_rows)} MAKAN + {len(comp_rows)} competitors "
        f"= {len(combined)} rows -> {output_path}"
    )
    print(f"Identifier column: {CATEGORY_COLUMN} ({CATEGORY_MAKAN}|{CATEGORY_COMPETITORS})")


def split_classified(
    combined_path: Path,
    makan_output: Path,
    competitors_output: Path,
) -> None:
    if not combined_path.exists():
        raise FileNotFoundError(f"Classified combined CSV not found: {combined_path}")

    fieldnames, rows = _read_rows(combined_path)
    if CATEGORY_COLUMN not in fieldnames:
        raise ValueError(
            f"Expected identifier column '{CATEGORY_COLUMN}' in {combined_path}. "
            "Classify the merged sheet so the column is preserved."
        )

    makan_rows = [
        row
        for row in rows
        if str(row.get(CATEGORY_COLUMN, "")).strip().lower() == CATEGORY_MAKAN
    ]
    competitor_rows = [
        row
        for row in rows
        if str(row.get(CATEGORY_COLUMN, "")).strip().lower() == CATEGORY_COMPETITORS
    ]

    out_fields = [col for col in fieldnames if col != CATEGORY_COLUMN]
    _write_rows(makan_output, out_fields, makan_rows)
    _write_rows(competitors_output, out_fields, competitor_rows)

    print(f"Split MAKAN classified: {len(makan_rows)} rows -> {makan_output}")
    print(f"Split competitors classified: {len(competitor_rows)} rows -> {competitors_output}")
    print(f"Dropped '{CATEGORY_COLUMN}' from both outputs (ClickUp upload only).")


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
