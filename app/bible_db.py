"""SQLite access to the Bible: single verses, ranges, navigation, keyword search.

A verse is addressed by ``(translation, book_id, chapter, verse)`` where
``book_id`` is canonical (1-66), so the same reference works in KJV and LSG.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config


class BibleDBError(RuntimeError):
    """The Bible database is missing or unreadable."""


@dataclass(frozen=True)
class VerseRef:
    """A verse or a verse range inside one chapter."""

    book_id: int
    chapter: int
    verse_start: int
    verse_end: int

    def to_dict(self) -> dict[str, int]:
        return {
            "book_id": self.book_id,
            "chapter": self.chapter,
            "verse_start": self.verse_start,
            "verse_end": self.verse_end,
        }


class BibleDB:
    """Read-only Bible store. Safe to call from several threads."""

    def __init__(self, db_path: Path = config.BIBLE_DB, books_json: Path = config.BOOKS_JSON) -> None:
        if not db_path.exists():
            raise BibleDBError(
                f"Bible database not found at {db_path}. Run the setup script (setup.ps1 / setup.sh) first."
            )
        meta = json.loads(books_json.read_text(encoding="utf-8"))
        self.translation_language: dict[str, str] = {
            code: info["language"] for code, info in meta["translations"].items()
        }
        self._book_names: dict[int, dict[str, str]] = {b["id"]: b["names"] for b in meta["books"]}

        self._lock = threading.Lock()
        try:
            self._con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
            self._con.row_factory = sqlite3.Row
            # (translation, book_id, chapter) -> last verse number
            self._verse_counts: dict[tuple[str, int, int], int] = {
                (r["translation"], r["book_id"], r["chapter"]): r["n"]
                for r in self._con.execute(
                    "SELECT translation, book_id, chapter, MAX(verse) AS n FROM verses "
                    "GROUP BY translation, book_id, chapter"
                )
            }
        except sqlite3.Error as exc:
            raise BibleDBError(f"Could not open the Bible database ({exc}). Re-run the setup script.") from exc
        if not self._verse_counts:
            raise BibleDBError("The Bible database is empty. Re-run the setup script.")
        # (translation, book_id) -> number of chapters
        self._chapter_counts: dict[tuple[str, int], int] = {}
        for (translation, book_id, chapter) in self._verse_counts:
            key = (translation, book_id)
            self._chapter_counts[key] = max(self._chapter_counts.get(key, 0), chapter)
        self.translations: tuple[str, ...] = tuple(
            t for t in config.TRANSLATIONS if any(k[0] == t for k in self._chapter_counts)
        )

    # -- helpers ------------------------------------------------------------
    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._con.close()

    # -- structure ----------------------------------------------------------
    def book_name(self, book_id: int, translation: str) -> str:
        language = self.translation_language.get(translation, "en")
        names = self._book_names[book_id]
        return names.get(language) or names["en"]

    def chapter_count(self, book_id: int, translation: str = "KJV") -> int:
        return self._chapter_counts.get((translation, book_id), 0)

    def verse_count(self, book_id: int, chapter: int, translation: str = "KJV") -> int:
        return self._verse_counts.get((translation, book_id, chapter), 0)

    def exists(self, book_id: int, chapter: int, verse: int, translation: str = "KJV") -> bool:
        return 1 <= verse <= self.verse_count(book_id, chapter, translation)

    def validate(self, ref: VerseRef, translation: str = "KJV") -> VerseRef | None:
        """Return ref (range end clamped to the chapter) or None if it does not exist."""
        last = self.verse_count(ref.book_id, ref.chapter, translation)
        if last == 0 or not (1 <= ref.verse_start <= last):
            return None
        end = min(max(ref.verse_end, ref.verse_start), last)
        end = min(end, ref.verse_start + config.MAX_RANGE_VERSES - 1)
        return VerseRef(ref.book_id, ref.chapter, ref.verse_start, end)

    def format_reference(self, ref: VerseRef, translation: str) -> str:
        """e.g. 'John 3:16' or 'Jean 3:16-18'."""
        text = f"{self.book_name(ref.book_id, translation)} {ref.chapter}:{ref.verse_start}"
        if ref.verse_end > ref.verse_start:
            text += f"-{ref.verse_end}"
        return text

    # -- reading ------------------------------------------------------------
    def get_range(self, translation: str, ref: VerseRef) -> list[dict[str, Any]]:
        """Verses of ref in one translation: [{'verse': 16, 'text': '...'}]."""
        rows = self._query(
            "SELECT verse, text FROM verses WHERE translation=? AND book_id=? AND chapter=? "
            "AND verse BETWEEN ? AND ? ORDER BY verse",
            (translation, ref.book_id, ref.chapter, ref.verse_start, ref.verse_end),
        )
        return [{"verse": r["verse"], "text": r["text"]} for r in rows]

    def passage(self, ref: VerseRef, translations: tuple[str, ...]) -> list[dict[str, Any]]:
        """One display panel per translation (translations lacking the verse are skipped)."""
        panels: list[dict[str, Any]] = []
        for translation in translations:
            verses = self.get_range(translation, ref)
            if verses:
                shown = VerseRef(ref.book_id, ref.chapter, verses[0]["verse"], verses[-1]["verse"])
                panels.append(
                    {
                        "translation": translation,
                        "language": self.translation_language.get(translation, "en"),
                        "reference": self.format_reference(shown, translation),
                        "verses": verses,
                    }
                )
        return panels

    # -- navigation ---------------------------------------------------------
    def neighbor(self, ref: VerseRef, delta: int, translation: str = "KJV") -> VerseRef | None:
        """The single verse after (delta=+1) or before (-1) ref, crossing chapters and books."""
        book, chapter = ref.book_id, ref.chapter
        if delta > 0:
            verse = ref.verse_end + 1
            if verse > self.verse_count(book, chapter, translation):
                chapter, verse = chapter + 1, 1
                if chapter > self.chapter_count(book, translation):
                    book, chapter = book + 1, 1
                    if book > 66:
                        return None
        else:
            verse = ref.verse_start - 1
            if verse < 1:
                chapter -= 1
                if chapter < 1:
                    book -= 1
                    if book < 1:
                        return None
                    chapter = self.chapter_count(book, translation)
                verse = self.verse_count(book, chapter, translation)
        if not self.exists(book, chapter, verse, translation):
            return None
        return VerseRef(book, chapter, verse, verse)

    # -- keyword search -----------------------------------------------------
    def search_text(self, query: str, translations: tuple[str, ...], limit: int = 30) -> list[dict[str, Any]]:
        """Full-text search. Exact-phrase hits come first, then verses with all the words."""
        words = re.findall(r"\w+", query, flags=re.UNICODE)
        if not words:
            return []
        quoted = [f'"{w}"' for w in words]
        phrase = '"' + " ".join(words) + '"'
        all_words = " ".join(quoted[:-1] + [quoted[-1] + "*"])  # last word may be half-typed
        marks = ",".join("?" for _ in translations)
        base_sql = (
            "SELECT v.translation, v.book_id, v.chapter, v.verse, v.text "
            "FROM verses_fts JOIN verses v ON v.rowid = verses_fts.rowid "
            f"WHERE verses_fts MATCH ? AND v.translation IN ({marks}) "
        )
        # Exact phrases are listed in Bible order (predictable for the operator);
        # looser matches are listed best-match first.
        attempts = (
            (phrase, "ORDER BY v.book_id, v.chapter, v.verse, v.translation LIMIT ?"),
            (all_words, "ORDER BY bm25(verses_fts), v.book_id, v.chapter, v.verse LIMIT ?"),
        )
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int, int]] = set()
        for match, order in attempts:
            try:
                rows = self._query(base_sql + order, (match, *translations, limit))
            except sqlite3.Error:
                continue  # odd characters in the query: just skip this attempt
            for r in rows:
                key = (r["translation"], r["book_id"], r["chapter"], r["verse"])
                if key in seen or len(results) >= limit:
                    continue
                seen.add(key)
                ref = VerseRef(r["book_id"], r["chapter"], r["verse"], r["verse"])
                results.append(
                    {
                        **ref.to_dict(),
                        "translation": r["translation"],
                        "reference": self.format_reference(ref, r["translation"]),
                        "text": r["text"],
                    }
                )
        return results
