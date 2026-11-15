"""Typed references from the operator's search box (Phase 1).

The full spoken-reference test suite (60+ cases) comes with Phase 2.
"""
from __future__ import annotations

import pytest

from app.reference_parser import BookIndex, ParsedReference, book_key, parse_typed_reference


@pytest.mark.parametrize(
    "text, expected",
    [
        ("jn 3 16", (43, 3, 16, 16)),
        ("John 3:16", (43, 3, 16, 16)),
        ("john 3.16", (43, 3, 16, 16)),
        ("jn3:16", (43, 3, 16, 16)),
        ("John 3:16-18", (43, 3, 16, 18)),
        ("john 3 16 to 18", (43, 3, 16, 18)),
        ("John chapter 3 verse 16", (43, 3, 16, 16)),
        ("romains 8:28", (45, 8, 28, 28)),
        ("Rom 8 28", (45, 8, 28, 28)),
        ("Jean 3:16", (43, 3, 16, 16)),
        ("jean 3 16 à 18", (43, 3, 16, 18)),
        ("Jean chapitre 3 verset 16", (43, 3, 16, 16)),
        ("1 cor 13:4", (46, 13, 4, 4)),
        ("1cor 13 4-7", (46, 13, 4, 7)),
        ("First Corinthians 13:4", (46, 13, 4, 4)),
        ("1st Corinthians 13:4", (46, 13, 4, 4)),
        ("I Corinthians 13:4", (46, 13, 4, 4)),
        ("II Kings 2:11", (12, 2, 11, 11)),
        ("Second Kings 2 11", (12, 2, 11, 11)),
        ("Premier Corinthiens 13:4", (46, 13, 4, 4)),
        ("1er Rois 3:9", (11, 3, 9, 9)),
        ("2 Timothée 1:7", (55, 1, 7, 7)),
        ("3 jn 1:4", (64, 1, 4, 4)),
        ("Ps 23:1", (19, 23, 1, 1)),
        ("Psalm 23:1", (19, 23, 1, 1)),
        ("Psaume 23:1", (19, 23, 1, 1)),
        ("Ésaïe 53:5", (23, 53, 5, 5)),
        ("esaie 53 5", (23, 53, 5, 5)),
        ("Hébreux 11:1", (58, 11, 1, 1)),
        ("Apocalypse 21:4", (66, 21, 4, 4)),
        ("Rev 21:4", (66, 21, 4, 4)),
        ("Song of Solomon 2:1", (22, 2, 1, 1)),
        ("Cantique des Cantiques 2:1", (22, 2, 1, 1)),
        ("Phil 4:13", (50, 4, 13, 13)),
        ("Filipians 4:13", (50, 4, 13, 13)),       # misspelling
        ("Habakuk 2:4", (35, 2, 4, 4)),            # misspelling
        ("Mathew 5:3", (40, 5, 3, 3)),             # misspelling
        ("Jude 5", (65, 1, 5, 5)),                 # single-chapter book
    ],
)
def test_typed_reference(books: BookIndex, text: str, expected: tuple[int, int, int, int]) -> None:
    assert parse_typed_reference(text, books) == ParsedReference(*expected)


@pytest.mark.parametrize(
    "text, expected",
    [("Psalm 23", (19, 23)), ("Apocalypse 21", (66, 21)), ("1 Corinthiens 13", (46, 13)), ("jn 3", (43, 3))],
)
def test_whole_chapter(books: BookIndex, text: str, expected: tuple[int, int]) -> None:
    parsed = parse_typed_reference(text, books)
    assert parsed is not None
    assert (parsed.book_id, parsed.chapter, parsed.verse_start) == (*expected, None)


@pytest.mark.parametrize("text", ["God so loved", "love", "forty days 40", "", "3:16", "xyzzy 3:16", "John"])
def test_not_a_reference(books: BookIndex, text: str) -> None:
    assert parse_typed_reference(text, books) is None


def test_numbered_books_do_not_mix(books: BookIndex) -> None:
    assert books.resolve("John") == 43
    assert books.resolve("1 John") == 62
    assert books.resolve("2 Jean") == 63
    assert books.resolve("Jean") == 43


def test_book_key() -> None:
    assert book_key("1st Corinthians") == "1corinthians"
    assert book_key("Première Corinthiens") == "1corinthiens"
    assert book_key("Éphésiens") == "ephesiens"
    assert book_key("III John") == "3john"
