"""Precompute one embedding per verse, for quoted/paraphrased verse detection.

For each translation in data/bible.sqlite this writes, in data/embeddings/:

    <TR>.npy         float16 matrix (verses x 384), rows L2-normalised
    <TR>.index.npy   int16 matrix (verses x 3): book_id, chapter, verse for each row

Very short verses ("Jesus wept.") are left out: a few words match far too many
ordinary sentences. Takes roughly 5-15 minutes on a laptop CPU, once.

Usage:  python scripts/build_embeddings.py [--force]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402

MIN_WORDS = 5


def build(force: bool = False) -> None:
    if not config.BIBLE_DB.exists():
        raise SystemExit("ERROR: data/bible.sqlite is missing. Run scripts/build_bible_db.py first.")
    if not (config.EMBEDDING_MODEL_DIR / "modules.json").exists():
        raise SystemExit("ERROR: the embedding model is missing. Run scripts/download_models.py --embedding first.")

    con = sqlite3.connect(config.BIBLE_DB)
    translations = [row[0] for row in con.execute("SELECT DISTINCT translation FROM verses ORDER BY translation")]
    todo = [t for t in translations if force or not (config.EMBEDDINGS_DIR / f"{t}.npy").exists()]
    if not todo:
        print("Verse embeddings already built (use --force to rebuild).")
        return

    from sentence_transformers import SentenceTransformer

    print("Loading the embedding model...")
    model = SentenceTransformer(str(config.EMBEDDING_MODEL_DIR), device="cpu")
    config.EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)

    for translation in todo:
        rows = con.execute(
            "SELECT book_id, chapter, verse, text FROM verses WHERE translation=? "
            "ORDER BY book_id, chapter, verse",
            (translation,),
        ).fetchall()
        rows = [r for r in rows if len(r[3].split()) >= MIN_WORDS]
        print(f"{translation}: embedding {len(rows):,} verses (several minutes)...")
        started = time.time()
        vectors = model.encode(
            [r[3] for r in rows], batch_size=64, normalize_embeddings=True,
            show_progress_bar=True, convert_to_numpy=True,
        )
        index = np.array([(r[0], r[1], r[2]) for r in rows], dtype=np.int16)
        # Write to temporary names first so an interrupted build never leaves half a file.
        tmp_vectors = config.EMBEDDINGS_DIR / f"{translation}.tmp.npy"
        tmp_index = config.EMBEDDINGS_DIR / f"{translation}.index.tmp.npy"
        np.save(tmp_vectors, vectors.astype(np.float16))
        np.save(tmp_index, index)
        tmp_index.replace(config.EMBEDDINGS_DIR / f"{translation}.index.npy")
        tmp_vectors.replace(config.EMBEDDINGS_DIR / f"{translation}.npy")
        print(f"{translation}: done in {time.time() - started:.0f} s")
    con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Precompute verse embeddings.")
    parser.add_argument("--force", action="store_true", help="rebuild even if the files exist")
    build(force=parser.parse_args().force)
