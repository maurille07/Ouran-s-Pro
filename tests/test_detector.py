"""Detector: stability on partial transcripts, refinement, de-duplication, context."""
from __future__ import annotations

import pytest

from app.bible_db import BibleDB, VerseRef
from app.detector import Detector
from app.reference_parser import BookIndex, ReferenceParser

JHN, ROM, COR1 = 43, 45, 46


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


def refs(candidates) -> list[VerseRef]:
    return [c.ref for c in candidates]


def test_final_text_gives_a_candidate(detector: Detector) -> None:
    found, spans = detector.feed(1, "The Bible says in John 3:16 that God loved the world.", final=True)
    assert refs(found) == [VerseRef(JHN, 3, 16, 16)]
    assert found[0].kind == "direct" and found[0].confidence > 0.9 and found[0].heard == "John 3:16"
    assert spans == [(18, 27)]


def test_reference_at_the_unstable_tail_waits_one_pass(detector: Detector) -> None:
    # Whisper's last words often change: "1 Corinthians 2." became "1 Corinthians 13" a second later.
    found, spans = detector.feed(1, "Finally, 1 Corinthians 2.", final=False)
    assert found == [] and len(spans) == 1                    # highlighted, but not suggested yet
    found, _ = detector.feed(1, "Finally, 1 Corinthians 13.", final=False)
    assert found == []
    found, _ = detector.feed(1, "Finally, 1 Corinthians 13 verses 4 through 7.", final=False)
    assert found == []
    found, _ = detector.feed(1, "Finally, 1 Corinthians 13 verses 4 through 7.", final=True)
    assert refs(found) == [VerseRef(COR1, 13, 4, 7)]


def test_partial_is_accepted_when_seen_twice(detector: Detector) -> None:
    assert detector.feed(1, "Turn to John 3:16", final=False)[0] == []
    assert refs(detector.feed(1, "Turn to John 3:16", final=False)[0]) == [VerseRef(JHN, 3, 16, 16)]


def test_partial_is_accepted_when_more_words_follow(detector: Detector) -> None:
    found, _ = detector.feed(1, "John 3:16 for God so loved", final=False)
    assert refs(found) == [VerseRef(JHN, 3, 16, 16)]


def test_same_utterance_never_repeats(detector: Detector) -> None:
    assert len(detector.feed(1, "John 3:16 for God so loved", final=False)[0]) == 1
    assert detector.feed(1, "John 3:16 for God so loved the world", final=False)[0] == []
    assert detector.feed(1, "John 3:16 for God so loved the world.", final=True)[0] == []


def test_chapter_is_refined_into_a_verse(detector: Detector) -> None:
    first, _ = detector.feed(1, "Open to Romans 8 and then", final=False)
    assert first[0].whole_chapter and first[0].ref == VerseRef(ROM, 8, 1, 1)
    second, _ = detector.feed(1, "Open to Romans 8 verse 28 and then", final=False)
    assert refs(second) == [VerseRef(ROM, 8, 28, 28)]
    assert second[0].replaces == first[0].id and not second[0].whole_chapter


def test_dedupe_window(detector: Detector, clock: Clock) -> None:
    assert len(detector.feed(1, "John 3:16.", final=True)[0]) == 1
    clock.now += 5
    assert detector.feed(2, "Again, John 3:16.", final=True)[0] == []      # 5 s later: stay quiet
    clock.now += 30
    assert len(detector.feed(3, "Once more John 3:16.", final=True)[0]) == 1  # 35 s later: suggest again


def test_followup_uses_context_from_previous_utterance(detector: Detector) -> None:
    detector.feed(1, "Please turn to Romans chapter 8.", final=True)
    found, _ = detector.feed(2, "Let us look at verse 28.", final=True)
    assert refs(found) == [VerseRef(ROM, 8, 28, 28)]
    assert found[0].followup and found[0].confidence < 0.97


def test_partial_text_does_not_change_context(detector: Detector) -> None:
    detector.feed(1, "Turn to Romans chapter 8.", final=True)
    detector.feed(2, "then John 3 and so on", final=False)        # never finalised with John
    detector.feed(2, "then join three of them", final=True)
    found, _ = detector.feed(3, "look at verse 1.", final=True)
    assert refs(found) == [VerseRef(ROM, 8, 1, 1)]


def test_reset_forgets_everything(detector: Detector) -> None:
    detector.feed(1, "Romans chapter 8.", final=True)
    detector.reset()
    assert detector.feed(2, "verse 28.", final=True)[0] == []
    assert len(detector.feed(3, "Romans chapter 8.", final=True)[0]) == 1   # not blocked by dedupe
