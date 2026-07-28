"""
Dynamic review text normalization for parking classification.

Instead of a fixed typo list, this module:
1. Fuzzy-matches tokens against a parking domain lexicon (catches new misspellings).
2. Learns recurring corrections from each weekly batch (parking-context reviews).
3. Optionally persists learned corrections across runs.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

WORD_RE = re.compile(r"[A-Za-z]+")

# Anchor vocabulary — parking concepts, not individual typos.
PARKING_LEXICON = frozenset(
    {
        "parking",
        "park",
        "parked",
        "parkin",
        "garage",
        "valet",
        "gate",
        "gates",
        "entrance",
        "barrier",
        "ticket",
        "riyal",
        "riyals",
        "sar",
        "fee",
        "fees",
        "charge",
        "charges",
        "charged",
        "hour",
        "hours",
        "hourly",
        "lot",
        "space",
        "spaces",
        "vehicle",
        "vehicles",
        "car",
        "cars",
        "subscribe",
        "subscription",
        "camera",
        "cameras",
        "scratch",
        "scratched",
    }
)

# Weak signals that a review may be parking-related (used to gate fuzzy fixes).
PARKING_CONTEXT_SIGNALS = frozenset(
    {
        "gate",
        "gates",
        "hour",
        "hours",
        "charge",
        "charges",
        "fee",
        "fees",
        "money",
        "cost",
        "costs",
        "car",
        "cars",
        "vehicle",
        "park",
        "parking",
        "lot",
        "valet",
        "garage",
        "ticket",
        "riyal",
        "riyals",
        "barrier",
        "entrance",
        "scratch",
        "camera",
    }
)

REVIEW_PREFIX = "Mall customer review: "
FUZZY_STRONG_RATIO = 0.82

# Common valid words that must not be fuzzy-corrected into parking terms.
PROTECTED_WORDS = frozenset(
    {
        "free",
        "care",
        "cares",
        "cared",
        "market",
        "mall",
        "branch",
        "branches",
        "rivals",
        "rival",
        "gate",
        "gates",
        "hour",
        "hours",
        "car",
        "cars",
        "park",
        "parking",
        "fee",
        "fees",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "fifteen",
    }
)

CURRENCY_TYPO_RE = re.compile(r"(\d+)\s+rivals?\b", re.IGNORECASE)
MARKING_TYPO_RE = re.compile(r"\bmarking\b", re.IGNORECASE)
EXPLICIT_PARKING_RE = re.compile(
    r"\b(?:parking|parked|valet|car\s+park|parking\s+lot)\b|\bpark\s+it\b|\bmarking\b",
    re.IGNORECASE,
)
STRONG_PARKING_RE = re.compile(
    r"\b(?:"
    r"subscribe\s+to\s+(?:the\s+)?parking|parking\s+fee|parking\s+costs?\b|parking\s+lot|"
    r"parking\s+spot|parking\s+attendant|paid\s+parking|parking\s+.*\b(?:charge|fee|riyal|sar|hour)"
    r"|(?:charge|fee|riyal|sar|hour).{0,40}\bparking\b|"
    r"scratch(?:ed)?\s+while\s+(?:it'?s\s+)?parked|waited?\s+(?:over\s+)?(?:an?\s+)?\w*\s*parking|"
    r"parking\s+.*(?:exploited|not enough|isn'?t enough|too high|too short)|"
    r"(?:not enough|isn'?t enough|too high|too short).{0,40}\bparking\b|"
    r"every\s+parking\s+spot|marking\s+costs|should\s+not\s+charge\s+the\s+parking|"
    r"forcibly\s+take.*parking|parking\s+is\s+paid|parking\s+spaces?\s+now\s+cost"
    r")\b",
    re.IGNORECASE,
)
CASUAL_PARKING_RE = re.compile(
    r"\b(?:free\s+parking\s+is\s+available|free\s+parking\s+for\s+the\s+first)\b",
    re.IGNORECASE,
)
LONG_REVIEW_CHAR_THRESHOLD = 200


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _is_protected(word: str) -> bool:
    return word.lower() in PROTECTED_WORDS


def _apply_phrase_corrections(text: str) -> str:
    """High-confidence phrase fixes that are safe in mall/parking reviews."""
    text = MARKING_TYPO_RE.sub("parking", text)
    text = CURRENCY_TYPO_RE.sub(r"\1 riyals", text)
    return text


def explicitly_mentions_parking(text: str) -> bool:
    """True when the review literally mentions parking (even if briefly)."""
    if not text or not str(text).strip():
        return False

    raw = str(text)
    corrected = _apply_phrase_corrections(raw)
    return bool(EXPLICIT_PARKING_RE.search(raw) or EXPLICIT_PARKING_RE.search(corrected))


def parking_rule_override(text: str) -> bool:
    """
    Force Parking Related only when parking is a main topic.
    Casual mentions (e.g. 'free parking is available') in long general reviews
    return False so the 3-model ensemble decides.
    """
    if not explicitly_mentions_parking(text):
        return False

    raw = str(text)
    corrected = _apply_phrase_corrections(raw)

    for body in (raw, corrected):
        if STRONG_PARKING_RE.search(body):
            return True

        parking_mentions = len(EXPLICIT_PARKING_RE.findall(body))
        if parking_mentions >= 2:
            return True

        if parking_mentions == 1 and len(body) > LONG_REVIEW_CHAR_THRESHOLD:
            if CASUAL_PARKING_RE.search(body):
                return False

        if parking_mentions >= 1 and len(body) <= LONG_REVIEW_CHAR_THRESHOLD:
            return True

    return False


def _best_lexicon_match(word: str) -> tuple[str, float] | None:
    lower = word.lower()
    if lower in PARKING_LEXICON or len(lower) < 4 or _is_protected(lower):
        return None

    best_word = ""
    best_ratio = 0.0
    for candidate in PARKING_LEXICON:
        ratio = _similarity(lower, candidate)
        if ratio > best_ratio:
            best_ratio = ratio
            best_word = candidate

    if best_ratio < FUZZY_STRONG_RATIO:
        return None
    return best_word, best_ratio


def has_parking_context(text: str) -> bool:
    tokens = {t.lower() for t in WORD_RE.findall(str(text))}
    return bool(tokens & PARKING_CONTEXT_SIGNALS)


def discover_batch_corrections(texts: list[str], min_count: int = 2) -> dict[str, str]:
    """
    Learn typo -> lexicon mappings from the current weekly batch.
    Only considers reviews that already contain parking context signals.
    """
    votes: Counter[tuple[str, str]] = Counter()

    for text in texts:
        if not has_parking_context(text):
            continue

        seen_in_review: set[str] = set()
        for token in WORD_RE.findall(str(text)):
            match = _best_lexicon_match(token)
            if not match:
                continue

            corrected, ratio = match
            source = token.lower()
            if source == corrected or source in seen_in_review or _is_protected(source):
                continue

            if ratio >= FUZZY_STRONG_RATIO and has_parking_context(text):
                votes[(source, corrected)] += 1
                seen_in_review.add(source)

    return {
        source: corrected
        for (source, corrected), count in votes.items()
        if count >= min_count or _similarity(source, corrected) >= FUZZY_STRONG_RATIO
    }


def load_persisted_corrections(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k).lower(): str(v) for k, v in data.items()}


def save_persisted_corrections(path: Path, corrections: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(corrections, indent=2, sort_keys=True), encoding="utf-8")


def merge_corrections(*maps: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for item in maps:
        merged.update({k.lower(): v for k, v in item.items()})
    return merged


def _replace_token(
    word: str,
    batch_corrections: dict[str, str],
    review_has_context: bool,
) -> str:
    lower = word.lower()

    if lower in batch_corrections and not _is_protected(lower):
        replacement = batch_corrections[lower]
        return _preserve_case(word, replacement)

    match = _best_lexicon_match(word)
    if not match:
        return word

    corrected, ratio = match
    if ratio >= FUZZY_STRONG_RATIO and review_has_context:
        return _preserve_case(word, corrected)
    return word


def _preserve_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[0].isupper():
        return replacement.capitalize()
    return replacement


def normalize_review_text(
    text: str,
    batch_corrections: dict[str, str] | None = None,
    add_prefix: bool = True,
) -> str:
    """Return a model-friendly version of the review without changing stored originals."""
    if not text or not str(text).strip():
        return ""

    batch_corrections = batch_corrections or {}
    raw = _apply_phrase_corrections(str(text).strip())
    review_has_context = has_parking_context(raw)

    def replacer(match: re.Match[str]) -> str:
        return _replace_token(match.group(0), batch_corrections, review_has_context)

    normalized = WORD_RE.sub(replacer, raw)
    if add_prefix:
        return f"{REVIEW_PREFIX}{normalized}"
    return normalized


def build_normalization_context(
    texts: list[str],
    corrections_file: Path | None = None,
    min_batch_count: int = 2,
) -> dict[str, str]:
    """Prepare dynamic corrections for a weekly batch."""
    persisted = load_persisted_corrections(corrections_file)
    batch = discover_batch_corrections(texts, min_count=min_batch_count)
    combined = merge_corrections(persisted, batch)

    if corrections_file:
        save_persisted_corrections(corrections_file, combined)

    return combined
