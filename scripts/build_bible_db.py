"""Build data/bible.sqlite from public-domain sources.

    KJV  (English) -- github.com/scrollmapper/bible_databases
    LSG  (French, Louis Segond 1910) -- getbible.net (scrollmapper no longer
         ships Segond 1910, so we use getBible's public-domain copy)

Run once at setup (needs internet). After that the app is fully offline.
Downloaded files are cached in data/sources/ so a rebuild needs no network.

Usage:  python scripts/build_bible_db.py [--force]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SOURCES_DIR = DATA_DIR / "sources"
DB_PATH = DATA_DIR / "bible.sqlite"
BOOKS_JSON = DATA_DIR / "books.json"

SOURCES: dict[str, dict[str, str]] = {
    "KJV": {
        "url": "https://raw.githubusercontent.com/scrollmapper/bible_databases/master/formats/json/KJV.json",
        "file": "KJV.json",
    },
    "LSG": {
        "url": "https://api.getbible.net/v2/ls1910.json",
        "file": "LSG1910.json",
    },
}

# Narrow / thin spaces used by French typography -> one non-breaking space,
# so "berger :" never wraps between the word and the punctuation.
_FR_SPACES = re.compile(r"[   ]+")
_MULTI_SPACE = re.compile(r"[ \t\r\n]+")


def clean_text(text: str) -> str:
    """Normalise whitespace in a verse."""
    text = _FR_SPACES.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip("  ")


def download(url: str, dest: Path) -> None:
    """Download url to dest (skipped if already cached)."""
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"  using cached {dest.name}")
        return
    print(f"  downloading {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "ouranos-pro-setup"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
    except Exception as exc:  # network errors: give a clear, human message
        raise SystemExit(
            f"\nERROR: could not download {url}\n  {exc}\n"
            "Check your internet connection and run the setup again."
        ) from exc
    dest.write_bytes(data)


def load_rows(translation: str, path: Path, books: list[dict]) -> list[tuple]:
    """Read one source JSON file into (translation, book_id, book_name, chapter, verse, text) rows.

    Both sources share the shape books[] -> chapters[] -> verses[] and list the
    66 books in canonical order, so the book id is simply position + 1.
    """
    language = "en" if translation == "KJV" else "fr"
    data = json.loads(path.read_text(encoding="utf-8"))
    src_books = data["books"]
    if len(src_books) != 66:
        raise SystemExit(f"ERROR: {path.name} has {len(src_books)} books, expected 66.")
    rows: list[tuple] = []
    for index, src_book in enumerate(src_books):
        book = books[index]
        book_name = book["names"][language]
        for chapter in src_book["chapters"]:
            chapter_no = int(chapter["chapter"])
            for verse in chapter["verses"]:
                text = clean_text(verse["text"])
                if text:
                    rows.append((translation, book["id"], book_name, chapter_no, int(verse["verse"]), text))
    return rows


def build(force: bool = False) -> None:
    if DB_PATH.exists() and not force:
        print(f"{DB_PATH.name} already exists (use --force to rebuild).")
        return

    books = json.loads(BOOKS_JSON.read_text(encoding="utf-8"))["books"]
    print("Fetching Bible texts (public domain)...")
    all_rows: list[tuple] = []
    for translation, source in SOURCES.items():
        path = SOURCES_DIR / source["file"]
        download(source["url"], path)
        rows = load_rows(translation, path, books)
        print(f"  {translation}: {len(rows):,} verses")
        all_rows.extend(rows)

    tmp_path = DB_PATH.with_suffix(".sqlite.tmp")
    tmp_path.unlink(missing_ok=True)
    con = sqlite3.connect(tmp_path)
    try:
        con.executescript(
            """
            CREATE TABLE verses (
                translation TEXT    NOT NULL,
                book_id     INTEGER NOT NULL,   -- canonical 1..66, same in every translation
                book_name   TEXT    NOT NULL,
                chapter     INTEGER NOT NULL,
                verse       INTEGER NOT NULL,
                text        TEXT    NOT NULL,
                PRIMARY KEY (translation, book_id, chapter, verse)
            );
            -- Full-text keyword search. Accents are ignored ("eternel" finds "Éternel").
            CREATE VIRTUAL TABLE verses_fts USING fts5(
                text,
                content='verses',
                content_rowid='rowid',
                tokenize="unicode61 remove_diacritics 2"
            );
            """
        )
        con.executemany("INSERT INTO verses VALUES (?,?,?,?,?,?)", all_rows)
        con.execute("INSERT INTO verses_fts(rowid, text) SELECT rowid, text FROM verses")
        con.execute("INSERT INTO verses_fts(verses_fts) VALUES ('optimize')")
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    DB_PATH.unlink(missing_ok=True)
    tmp_path.rename(DB_PATH)
    print(f"Built {DB_PATH} ({DB_PATH.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the offline Bible database.")
    parser.add_argument("--force", action="store_true", help="rebuild even if the database exists")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    build(force=args.force)
