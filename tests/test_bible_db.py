"""Bible database: content, structure, navigation and keyword search."""
from __future__ import annotations

from app.bible_db import BibleDB, VerseRef


def one(book: int, chapter: int, verse: int) -> VerseRef:
    return VerseRef(book, chapter, verse, verse)


def test_both_translations_present(db: BibleDB) -> None:
    assert db.translations == ("KJV", "LSG")


def test_structure_counts(db: BibleDB) -> None:
    assert db.chapter_count(1, "KJV") == 50          # Genesis
    assert db.chapter_count(19, "KJV") == 150        # Psalms
    assert db.chapter_count(19, "LSG") == 150
    assert db.chapter_count(66, "LSG") == 22         # Revelation
    assert db.verse_count(43, 3, "KJV") == 36        # John 3
    assert db.verse_count(19, 119, "KJV") == 176     # Psalm 119


def test_john_3_16_in_both_languages(db: BibleDB) -> None:
    kjv = db.get_range("KJV", one(43, 3, 16))
    lsg = db.get_range("LSG", one(43, 3, 16))
    assert kjv[0]["text"].startswith("For God so loved the world")
    assert lsg[0]["text"].startswith("Car Dieu a tant aimé le monde")


def test_range(db: BibleDB) -> None:
    verses = db.get_range("KJV", VerseRef(43, 3, 16, 18))
    assert [v["verse"] for v in verses] == [16, 17, 18]


def test_reference_formatting(db: BibleDB) -> None:
    assert db.format_reference(one(43, 3, 16), "KJV") == "John 3:16"
    assert db.format_reference(VerseRef(43, 3, 16, 18), "LSG") == "Jean 3:16-18"
    assert db.format_reference(one(46, 13, 4), "LSG") == "1 Corinthiens 13:4"
    assert db.format_reference(one(66, 21, 4), "KJV") == "Revelation 21:4"


def test_validate_rejects_nonexistent(db: BibleDB) -> None:
    assert db.validate(one(43, 3, 37)) is None       # John 3 has 36 verses
    assert db.validate(one(43, 22, 1)) is None       # John has 21 chapters
    assert db.validate(one(65, 2, 1)) is None        # Jude has 1 chapter
    assert db.validate(one(43, 3, 0)) is None


def test_validate_clamps_range_end(db: BibleDB) -> None:
    assert db.validate(VerseRef(43, 3, 30, 99)) == VerseRef(43, 3, 30, 36)


def test_passage_panels(db: BibleDB) -> None:
    panels = db.passage(one(45, 8, 28), ("KJV", "LSG"))
    assert [p["translation"] for p in panels] == ["KJV", "LSG"]
    assert panels[0]["reference"] == "Romans 8:28"
    assert panels[1]["reference"] == "Romains 8:28"
    assert panels[1]["language"] == "fr"


def test_neighbor_within_chapter(db: BibleDB) -> None:
    assert db.neighbor(one(43, 3, 16), +1) == one(43, 3, 17)
    assert db.neighbor(one(43, 3, 16), -1) == one(43, 3, 15)
    assert db.neighbor(VerseRef(43, 3, 16, 18), +1) == one(43, 3, 19)   # after a range
    assert db.neighbor(VerseRef(43, 3, 16, 18), -1) == one(43, 3, 15)


def test_neighbor_crosses_chapters_and_books(db: BibleDB) -> None:
    assert db.neighbor(one(43, 3, 36), +1) == one(43, 4, 1)
    assert db.neighbor(one(43, 4, 1), -1) == one(43, 3, 36)
    assert db.neighbor(one(39, 4, 6), +1) == one(40, 1, 1)             # Malachi -> Matthew
    assert db.neighbor(one(40, 1, 1), -1) == one(39, 4, 6)


def test_neighbor_stops_at_the_ends(db: BibleDB) -> None:
    assert db.neighbor(one(1, 1, 1), -1) is None
    assert db.neighbor(one(66, 22, 21), +1) is None


def test_keyword_search_english(db: BibleDB) -> None:
    results = db.search_text("God so loved", ("KJV",))
    assert results and results[0]["reference"] == "John 3:16"


def test_keyword_search_french_ignores_accents(db: BibleDB) -> None:
    results = db.search_text("l'eternel est mon berger", ("LSG",))
    assert results and results[0]["reference"] == "Psaumes 23:1"


def test_keyword_search_respects_translation(db: BibleDB) -> None:
    assert all(r["translation"] == "LSG" for r in db.search_text("amour", ("LSG",)))
    assert db.search_text("berger", ("KJV",)) == []


def test_keyword_search_prefix_and_odd_input(db: BibleDB) -> None:
    assert db.search_text("everlast", ("KJV",))                  # half-typed last word
    assert db.search_text('"; DROP TABLE verses; --', ("KJV",)) == []
    assert db.search_text("   ", ("KJV",)) == []
