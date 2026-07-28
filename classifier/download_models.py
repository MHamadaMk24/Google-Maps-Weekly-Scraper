"""
One-time download of all 3 classification models into data/models/.

After this, the classifier loads from disk only (no Hugging Face re-download).

Usage:
  python download_models.py
"""

from __future__ import annotations

import argparse

from huggingface_hub import snapshot_download

from model_paths import MODEL_KEYS, MODEL_REGISTRY, local_model_dir, local_models_dir


def download_all(force: bool = False) -> None:
    base = local_models_dir()
    base.mkdir(parents=True, exist_ok=True)
    print(f"Models will be saved to: {base}\n")

    for key in MODEL_KEYS:
        hub_id, folder = MODEL_REGISTRY[key]
        target = local_model_dir(key)
        if target.exists() and (target / "config.json").exists() and not force:
            print(f"[skip] {key} already exists at {target}")
            continue

        print(f"[download] {key}: {hub_id}")
        snapshot_download(
            repo_id=hub_id,
            local_dir=str(target),
            local_dir_use_symlinks=False,
        )
        print(f"  saved -> {target}\n")

    print("Done. All models are stored locally.")
    print("Future runs will load from data/models/ without re-downloading.")


def main():
    parser = argparse.ArgumentParser(description="Download ensemble models locally")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if models already exist locally",
    )
    args = parser.parse_args()
    download_all(force=args.force)


if __name__ == "__main__":
    main()
