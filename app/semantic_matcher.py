"""Finds verses that are *quoted or paraphrased* (no reference spoken).

Every verse of every translation has a precomputed embedding (see
scripts/build_embeddings.py). At runtime the last words of the transcript are
embedded with the same multilingual model and compared with all verses by cosine
similarity -- a plain NumPy matrix product over ~60k vectors, a few milliseconds.

Meaning alone is not enough: "we must forgive one another" is close in meaning to
several verses without quoting any of them. So the best candidates are re-scored
with how many of their words were actually said:

    score = 0.5 * cosine similarity + 0.5 * word overlap

On test sentences real quotes scored 0.66-0.99 and ordinary preaching at most
0.56, which is why the default threshold is 0.65.

Because the model is multilingual, an English quote can match the KJV and a
French quote the Louis Segond; results are reported as canonical references.
"""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from . import config

log = logging.getLogger(__name__)

TAIL_SIZES: tuple[int, ...] = (8, 14, 22, 32)   # words: from a short verse to a long one
SLIDING_SIZES: tuple[int, ...] = (12, 22)        # for scanning a whole pasted text
MIN_WORDS = 6                                    # fewer words are too vague to match on
CANDIDATES = 12                                  # verses per translation that get re-scored
_WORD = re.compile(r"\S+")
_LETTERS = re.compile(r"[a-z]+")

# Words too common to count as evidence of a quote (English, KJV English, French).
_STOPWORDS = frozenset(
    """a an and the of to in is it that he she they we you i his her their our my your him them us me be was
    were are am for with on at by from as not no but or so if then this these those there here who whom which
    what when will shall have has had do does did unto thee thou thy ye hath all also into upon
    le la les l un une des du de d et au aux en dans que qui qu il elle ils elles nous vous je tu on ne pas
    point se s ce cet cette ces son sa ses leur leurs mon ma mes ton ta tes notre nos votre vos est sont etait
    ete etre ont avait par pour sur avec sans mais ou donc car y lui te""".split()
)


class SemanticError(RuntimeError):
    """Quoted-verse detection is unavailable (message can be shown to the operator)."""


class VerseTexts(Protocol):
    """Where verse texts come from (implemented by BibleDB)."""

    def get_range(self, translation: str, ref: Any) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class SemanticMatch:
    book_id: int
    chapter: int
    verse: int
    score: float        # combined score, 0..1
    translation: str    # translation whose text matched best
    window: str         # the transcript words that matched


def is_available() -> bool:
    """True when the embedding model and at least one embeddings file are on disk."""
    return (config.EMBEDDING_MODEL_DIR / "modules.json").exists() and any(
        (config.EMBEDDINGS_DIR / f"{t}.npy").exists() for t in config.TRANSLATIONS
    )


def tail_windows(text: str) -> list[str]:
    """The last 8, 14, 22 and 32 words of text: the windows we try to match."""
    words = _WORD.findall(text)
    if len(words) < MIN_WORDS:
        return []
    windows: list[str] = []
    for size in TAIL_SIZES:
        window = " ".join(words[-size:])
        if window not in windows:
            windows.append(window)
    return windows


def sliding_windows(text: str, step: int = 5) -> list[tuple[int, int, str]]:
    """Windows over a whole text, as (start char, end char, words) -- for the paste-text test box."""
    spans = [m.span() for m in _WORD.finditer(text)]
    if len(spans) < MIN_WORDS:
        return []
    found: dict[tuple[int, int], str] = {}
    for size in SLIDING_SIZES:
        if len(spans) <= size:
            found[(spans[0][0], spans[-1][1])] = text[spans[0][0]: spans[-1][1]]
            continue
        last_start = len(spans) - size
        for begin in sorted(set(range(0, last_start, step)) | {last_start}):
            start, end = spans[begin][0], spans[begin + size - 1][1]
            found[(start, end)] = text[start:end]
    return [(start, end, words) for (start, end), words in found.items()]


def content_words(text: str) -> frozenset[str]:
    """Meaningful words of a text, crudely stemmed ("believeth"/"believes" -> "belie")."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return frozenset(w[:5] for w in _LETTERS.findall(plain) if len(w) > 1 and w not in _STOPWORDS)


def word_overlap(said: frozenset[str], verse: frozenset[str]) -> float:
    """0..1: how much of the verse was said, and how much of what was said is the verse."""
    if len(said) < 3 or len(verse) < 3:
        return 0.0
    shared = len(said & verse)
    if shared < 3:
        return shared / max(len(said), len(verse))
    return 0.5 * shared / len(verse) + 0.5 * shared / len(said)


class SemanticMatcher:
    """Loaded embedding model + verse embeddings. Thread-safe."""

    def __init__(self, verse_texts: VerseTexts) -> None:
        if not is_available():
            raise SemanticError(
                "Quoted-verse detection is not set up yet. Connect to the internet once and run the setup script again."
            )
        try:
            from sentence_transformers import SentenceTransformer

            self._model: Any = SentenceTransformer(str(config.EMBEDDING_MODEL_DIR), device="cpu")
        except Exception as exc:
            raise SemanticError(f"Could not load the embedding model ({exc}).") from exc

        self._texts = verse_texts
        self._lock = threading.Lock()
        self._verse_words: dict[tuple[str, int, int, int], frozenset[str]] = {}   # cache
        self._vectors: dict[str, np.ndarray] = {}     # translation -> (verses x dim) float32
        self._index: dict[str, np.ndarray] = {}       # translation -> (verses x 3) book, chapter, verse
        for translation in config.TRANSLATIONS:
            vectors_path = config.EMBEDDINGS_DIR / f"{translation}.npy"
            index_path = config.EMBEDDINGS_DIR / f"{translation}.index.npy"
            if not (vectors_path.exists() and index_path.exists()):
                continue
            try:
                vectors = np.load(vectors_path).astype(np.float32)
                index = np.load(index_path)
            except Exception as exc:
                raise SemanticError(f"Could not read the verse embeddings for {translation} ({exc}).") from exc
            if len(vectors) != len(index):
                raise SemanticError(f"The verse embeddings for {translation} are damaged. Re-run the setup script.")
            self._vectors[translation] = vectors
            self._index[translation] = index
        log.info("Semantic matcher ready: %s", {t: len(v) for t, v in self._vectors.items()})

    @property
    def translations(self) -> tuple[str, ...]:
        return tuple(self._vectors)

    # -- building blocks ----------------------------------------------------------
    def _cosine(self, texts: list[str]) -> dict[str, np.ndarray]:
        """Cosine similarity of every verse with each text: translation -> (verses x texts)."""
        with self._lock:                                  # the model is not safe for parallel calls
            queries = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        queries = np.asarray(queries, dtype=np.float32)
        return {t: vectors @ queries.T for t, vectors in self._vectors.items()}

    def _words_of(self, translation: str, book: int, chapter: int, verse: int) -> frozenset[str]:
        key = (translation, book, chapter, verse)
        words = self._verse_words.get(key)
        if words is None:
            from .bible_db import VerseRef

            rows = self._texts.get_range(translation, VerseRef(book, chapter, verse, verse))
            words = content_words(rows[0]["text"]) if rows else frozenset()
            self._verse_words[key] = words
        return words

    def _rescored(self, cosine: np.ndarray, translation: str, said: list[frozenset[str]]
                  ) -> list[tuple[float, int, tuple[int, int, int]]]:
        """Top verses by cosine (best over the columns), re-scored -> [(score, best column, key)]."""
        verse_best = cosine.max(axis=1)
        count = min(CANDIDATES, len(verse_best))
        results = []
        for row in np.argpartition(-verse_best, count - 1)[:count]:
            book, chapter, verse = (int(x) for x in self._index[translation][row])
            verse_words = self._words_of(translation, book, chapter, verse)
            overlaps = [word_overlap(words, verse_words) for words in said]
            column = int(np.argmax(overlaps))
            score = 0.5 * float(verse_best[row]) + 0.5 * overlaps[column]
            results.append((score, column, (book, chapter, verse)))
        return results

    # -- public -----------------------------------------------------------------
    def match(self, texts: list[str], threshold: float, top_k: int = 3) -> list[SemanticMatch]:
        """Best verses for a group of windows over the same moment of speech, best first."""
        if not texts:
            return []
        said = [content_words(t) for t in texts]
        best: dict[tuple[int, int, int], SemanticMatch] = {}
        for translation, cosine in self._cosine(texts).items():
            for score, column, key in self._rescored(cosine, translation, said):
                if score >= threshold and (key not in best or score > best[key].score):
                    best[key] = SemanticMatch(*key, score, translation, texts[column])
        return sorted(best.values(), key=lambda m: -m.score)[:top_k]

    def best_per_text(self, texts: list[str], threshold: float) -> list[SemanticMatch | None]:
        """For each text on its own: its best verse, or None if nothing reaches the threshold."""
        if not texts:
            return []
        tables = self._cosine(texts)
        results: list[SemanticMatch | None] = []
        for column, text in enumerate(texts):
            said = [content_words(text)]
            best: SemanticMatch | None = None
            for translation, cosine in tables.items():
                for score, _, key in self._rescored(cosine[:, column: column + 1], translation, said):
                    if score >= threshold and (best is None or score > best.score):
                        best = SemanticMatch(*key, score, translation, text)
            results.append(best)
        return results
