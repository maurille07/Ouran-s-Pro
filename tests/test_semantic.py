"""Quoted-verse detection: text windows, detector merge rules, and the real matcher.

Tests that need the embedding model and verse embeddings are skipped until
scripts/download_models.py --embedding and scripts/build_embeddings.py have run.
"""
from __future__ import annotations

import pytest

from app import semantic_matcher
from app.bible_db import BibleDB, VerseRef
from app.detector import Detector
from app.reference_parser import BookIndex, ReferenceParser
from app.semantic_matcher import SemanticMatch, sliding_windows, tail_windows

needs_embeddings = pytest.mark.skipif(not semantic_matcher.is_available(), reason="verse embeddings not built")

JOHN_3_16 = SemanticMatch(43, 3, 16, 0.86, "KJV", "for God so loved the world that he gave his only begotten Son")
MARK_1_1 = SemanticMatch(41, 1, 1, 0.80, "KJV", "the beginning of the gospel")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def detector(books: BookIndex, db: BibleDB, clock: Clock) -> Detector:
    return Detector(ReferenceParser(books, db), dedupe_seconds=lambda: 20.0, clock=clock)


# ---------------------------------------------------------------------- windows
def test_tail_windows() -> None:
    assert tail_windows("too few words here") == []
    assert tail_windows("one two three four five six seven") == ["one two three four five six seven"]
    words = " ".join(str(n) for n in range(30))
    windows = tail_windows(words)
    assert [len(w.split()) for w in windows] == [8, 14, 22, 30]      # 32 is capped by the text length
    assert all(w.endswith("29") for w in windows)


def test_word_overlap() -> None:
    from app.semantic_matcher import content_words, word_overlap

    verse = content_words("For God so loved the world, that he gave his only begotten Son")
    quote = content_words("God loved the world so much that he gave his only Son")
    other = content_words("we thank the choir for that wonderful song this morning")
    assert word_overlap(quote, verse) > 0.8
    assert word_overlap(other, verse) < 0.2
    assert content_words("believeth") == content_words("believes")     # crude stemming
    assert content_words("the of and le la les") == frozenset()        # stop words only


def test_sliding_windows_cover_the_text_with_spans() -> None:
    text = " ".join(f"w{n}" for n in range(40))
    windows = sliding_windows(text)
    assert all(text[start:end] == words for start, end, words in windows)
    assert any(start == 0 for start, _, _ in windows)
    assert any(end == len(text) for _, end, _ in windows)
    assert {len(words.split()) for _, _, words in windows} == {12, 22}
    assert sliding_windows("only four words here") == []


# ------------------------------------------------------------- detector merging
def test_quote_needs_two_consecutive_windows(detector: Detector) -> None:
    assert detector.feed_semantic([JOHN_3_16]) == []                 # first sighting: wait
    found = detector.feed_semantic([JOHN_3_16])
    assert [c.ref for c in found] == [VerseRef(43, 3, 16, 16)]
    assert found[0].kind == "quoted" and found[0].confidence == 0.86
    assert "God so loved" in found[0].heard


def test_quote_must_be_consecutive(detector: Detector) -> None:
    detector.feed_semantic([JOHN_3_16])
    detector.feed_semantic([])                                        # gone in the next window
    assert detector.feed_semantic([JOHN_3_16]) == []


def test_only_the_best_match_is_suggested(detector: Detector) -> None:
    detector.feed_semantic([JOHN_3_16, MARK_1_1])
    found = detector.feed_semantic([JOHN_3_16, MARK_1_1])
    assert [c.ref.book_id for c in found] == [43]


def test_previous_verse_is_caught_when_the_preacher_moves_on(detector: Detector) -> None:
    """Reading Psalm 23: verse 1 was the best match, then verse 2 takes over."""
    verse1 = SemanticMatch(19, 23, 1, 0.85, "KJV", "the Lord is my shepherd I shall not want")
    verse2 = SemanticMatch(19, 23, 2, 0.80, "KJV", "he maketh me to lie down in green pastures")
    assert detector.feed_semantic([verse1]) == []
    found = detector.feed_semantic([verse2, SemanticMatch(19, 23, 1, 0.70, "KJV", verse1.window)])
    assert [c.ref.verse_start for c in found] == [1]                 # verse 2 is new: it waits
    found = detector.feed_semantic([verse2])
    assert [c.ref.verse_start for c in found] == [2]


def test_quote_is_not_repeated_within_dedupe_window(detector: Detector, clock: Clock) -> None:
    detector.feed_semantic([JOHN_3_16])
    assert len(detector.feed_semantic([JOHN_3_16])) == 1
    assert detector.feed_semantic([JOHN_3_16]) == []                 # still in the window
    clock.now += 30
    assert len(detector.feed_semantic([JOHN_3_16])) == 1             # 30 s later it may come back


def test_quote_after_spoken_reference_is_not_duplicated(detector: Detector) -> None:
    """Preacher says "John 3:16" and then reads it: one card, not two."""
    assert len(detector.feed(1, "Turn to John 3:16.", final=True)[0]) == 1
    detector.feed_semantic([JOHN_3_16])
    assert detector.feed_semantic([JOHN_3_16]) == []


def test_quote_inside_a_spoken_range_is_not_duplicated(detector: Detector) -> None:
    detector.feed(1, "Read John 3:14-18.", final=True)
    detector.feed_semantic([JOHN_3_16])
    assert detector.feed_semantic([JOHN_3_16]) == []


def test_spoken_reference_after_quote_is_not_duplicated(detector: Detector) -> None:
    detector.feed_semantic([JOHN_3_16])
    detector.feed_semantic([JOHN_3_16])
    assert detector.feed(1, "That was John 3:16.", final=True)[0] == []


def test_reset_clears_quote_memory(detector: Detector) -> None:
    detector.feed_semantic([JOHN_3_16])
    detector.reset()
    assert detector.feed_semantic([JOHN_3_16]) == []


# ------------------------------------------------------------- the real matcher
@pytest.fixture(scope="module")
def matcher() -> semantic_matcher.SemanticMatcher:
    from app.bible_db import BibleDB as _DB
    return semantic_matcher.SemanticMatcher(_DB())


def top(matcher, text: str, threshold: float = 0.0) -> SemanticMatch | None:
    found = matcher.match(tail_windows(text), threshold, top_k=1)
    return found[0] if found else None


@needs_embeddings
@pytest.mark.parametrize(
    "text, expected",
    [
        ("for God so loved the world that he gave his only begotten Son that whosoever believeth in him should not perish", (43, 3, 16)),
        ("and the Bible tells us that the Lord is my shepherd I shall not want", (19, 23, 1)),
        ("I can do all things through Christ who strengthens me", (50, 4, 13)),
        ("for the wages of sin is death but the gift of God is eternal life in Christ Jesus our Lord", (45, 6, 23)),
        ("car Dieu a tant aimé le monde qu'il a donné son Fils unique afin que quiconque croit en lui ne périsse point", (43, 3, 16)),
        ("je puis tout par celui qui me fortifie", (50, 4, 13)),
    ],
)
def test_quotes_are_found(matcher, text: str, expected: tuple[int, int, int]) -> None:
    match = top(matcher, text, threshold=0.65)
    assert match is not None, "no verse reached the default threshold"
    assert (match.book_id, match.chapter, match.verse) == expected


@needs_embeddings
@pytest.mark.parametrize(
    "text",
    [
        "good morning church it is so good to see everyone here today please find your seats",
        "last week my wife and I were driving to the market and the car broke down on the road",
        "after the service there will be a meeting for all the youth leaders in the hall",
        "bonjour église nous sommes heureux de vous voir ce matin veuillez prendre place",
    ],
)
def test_ordinary_talk_is_not_matched(matcher, text: str) -> None:
    assert top(matcher, text, threshold=0.65) is None


@needs_embeddings
def test_match_is_fast_enough(matcher) -> None:
    import time

    windows = tail_windows("for God so loved the world that he gave his only begotten Son " * 2)
    matcher.match(windows, 0.65)                                      # warm up
    started = time.perf_counter()
    matcher.match(windows, 0.65)
    assert time.perf_counter() - started < 1.5


@needs_embeddings
def test_detect_endpoint_finds_a_quote(client) -> None:
    text = ("Good morning everyone, please find your seats. You know the scripture says, for God so loved "
            "the world that he gave his only begotten Son, that whosoever believeth in him should not perish, "
            "but have everlasting life. And that is the good news we celebrate today.")
    data = client.post("/api/detect", json={"text": text}).json()
    quoted = [d for d in data["detections"] if d["kind"] == "quoted"]
    assert [d["reference"] for d in quoted] == ["John 3:16"]
    assert quoted[0]["confidence"] >= 0.65
    assert "loved the world" in text[quoted[0]["start"]:quoted[0]["end"]]
    assert data["quoted"]["status"] == "ready"


@needs_embeddings
def test_detect_endpoint_respects_the_switch(client) -> None:
    client.post("/api/settings", json={"semantic_enabled": False})
    text = "for God so loved the world that he gave his only begotten Son that whosoever believeth in him"
    data = client.post("/api/detect", json={"text": text}).json()
    assert data["detections"] == [] and data["quoted"]["status"] == "off"


def test_semantic_settings_validation(client) -> None:
    snap = client.post("/api/settings", json={"semantic_threshold": 0.2}).json()
    assert snap["settings"]["semantic_threshold"] == 0.4              # clamped
    snap = client.post("/api/settings", json={"semantic_enabled": False}).json()
    assert snap["quoted"]["status"] == "off"
