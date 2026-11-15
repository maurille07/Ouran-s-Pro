"""Download the offline models (once, needs internet).

Models are saved inside the project (models/...), so at runtime nothing is
fetched from the internet and the folder can be copied to another PC.

Usage:
    python scripts/download_models.py                    # default speech model + embedding model
    python scripts/download_models.py --whisper base     # one speech model: base | small | medium
    python scripts/download_models.py --embedding        # the sentence-embedding model only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402


def download_whisper(size: str) -> None:
    target = config.whisper_model_dir(size)
    if (target / "model.bin").exists():
        print(f"Speech model '{size}' already downloaded.")
        return
    print(f"Downloading speech model '{size}' (this can take a few minutes)...")
    try:
        from faster_whisper import download_model

        target.mkdir(parents=True, exist_ok=True)
        download_model(size, output_dir=str(target))
    except Exception as exc:
        raise SystemExit(
            f"\nERROR: could not download the speech model '{size}'.\n  {exc}\n"
            "Check your internet connection and run the setup again."
        ) from exc
    print(f"Saved to {target}")


def download_embedding_model() -> None:
    target = config.EMBEDDING_MODEL_DIR
    if (target / "modules.json").exists():
        print("Embedding model already downloaded.")
        return
    print("Downloading the embedding model (about 470 MB)...")
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(config.EMBEDDING_MODEL)
        target.mkdir(parents=True, exist_ok=True)
        model.save(str(target))
    except Exception as exc:
        raise SystemExit(
            f"\nERROR: could not download the embedding model.\n  {exc}\n"
            "Check your internet connection and run the setup again."
        ) from exc
    print(f"Saved to {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download offline models.")
    parser.add_argument("--whisper", choices=config.WHISPER_MODELS, help="speech model size")
    parser.add_argument("--embedding", action="store_true", help="sentence-embedding model")
    args = parser.parse_args()
    everything = not args.whisper and not args.embedding
    if args.whisper or everything:
        download_whisper(args.whisper or config.Settings().whisper_model)
    if args.embedding or everything:
        download_embedding_model()
