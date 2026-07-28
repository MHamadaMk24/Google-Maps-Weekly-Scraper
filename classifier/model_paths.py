"""Resolve Hugging Face model IDs to local project copies when available."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_MODELS_DIR = REPO_ROOT / "data" / "models"

# Hub ID -> folder name under data/models/
MODEL_REGISTRY: dict[str, tuple[str, str]] = {
    "bart": ("facebook/bart-large-mnli", "bart-large-mnli"),
    "deberta_large": (
        "MoritzLaurer/deberta-v3-large-zeroshot-v2.0",
        "deberta-v3-large-zeroshot-v2.0",
    ),
    "deberta_base": (
        "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli",
        "deberta-v3-base-mnli-fever-anli",
    ),
}

MODEL_MAX_CHARS: dict[str, int] = {
    "bart": 1024,
    "deberta_large": 512,
    "deberta_base": 512,
}

MODEL_KEYS = list(MODEL_REGISTRY.keys())


def local_models_dir() -> Path:
    override = os.environ.get("REVIEW_MODELS_DIR", "").strip()
    return Path(override) if override else DEFAULT_LOCAL_MODELS_DIR


def hub_model_id(model_key: str) -> str:
    return MODEL_REGISTRY[model_key][0]


def local_model_dir(model_key: str) -> Path:
    folder = MODEL_REGISTRY[model_key][1]
    return local_models_dir() / folder


def is_model_cached_locally(model_key: str) -> bool:
    path = local_model_dir(model_key)
    return path.is_dir() and (path / "config.json").exists()


def resolve_model_source(model_key: str) -> tuple[str, bool]:
    """
    Return (model_path_or_id, is_local).
    Prefer project-local copy in data/models/ when present.
    """
    local_path = local_model_dir(model_key)
    if is_model_cached_locally(model_key):
        return str(local_path), True
    return hub_model_id(model_key), False


def all_models_cached_locally() -> bool:
    return all(is_model_cached_locally(key) for key in MODEL_KEYS)
