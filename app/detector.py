"""Turns live transcript text into verse suggestions for the operator's queue.

Two sources are merged here, sharing one de-duplication memory:

* "direct"  -- explicit references found by the reference parser (high confidence);
* "quoted"  -- verses quoted or paraphrased, found by the semantic matcher (scored).

The transcript arrives as *utterances* that are re-transcribed about once a
second while the preacher is still speaking (partial text), then once more as
final text. The detector therefore:

* waits until a reference is stable (seen twice, followed by more words, or
  final) before suggesting it -- the tail of a partial transcript often changes;
* upgrades "Romans 8" to "Romans 8:28" when the same phrase grows;
* never suggests the same verse twice within ``dedupe_seconds``.
"""
from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from .bible_db import VerseRef
from .reference_parser import Detection, ParserContext, ReferenceParser
from .semantic_matcher import SemanticMatch

_WORD = re.compile(r"\w+")
Key = tuple[int, int, int | None, int | None]


@dataclass
class Candidate:
    """One suggestion for the operator."""

    id: int
    ref: VerseRef            # a whole chapter is represented by its verse 1
    whole_chapter: bool
    kind: str                # "direct" (reference was spoken); Phase 4 adds "quoted"
    followup: bool           # True for "verse 5" resolved from the previous chapter
    confidence: float        # 0..1
    heard: str               # the words that triggered it
    replaces: int | None = None   # id of an earlier candidate this one refines


class Detector:
    def __init__(
        self,
        parser: ReferenceParser,
        dedupe_seconds: Callable[[], float],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._parser = parser
        self._dedupe_seconds = dedupe_seconds
        self._clock = clock
        self._next_id = 1
        self.reset()

    def reset(self) -> None:
        """Forget context and history (called when listening starts)."""
        self._context = ParserContext()             # book/chapter after the last *final* text
        self._utterance: int | None = None
        self._emitted: dict[Key, tuple[int | None, int]] = {}   # key -> (candidate id, text position)
        self._previous_keys: set[Key] = set()
        self._recent: dict[Key, float] = {}         # key -> time it was last suggested
        self._semantic_previous: set[tuple[int, int, int]] = set()   # verses matched by the previous window
        self._semantic_previous_top: tuple[int, int, int] | None = None   # ...and its best match

    # -- helpers --------------------------------------------------------------
    def _is_recent(self, key: Key) -> bool:
        seen = self._recent.get(key)
        return seen is not None and self._clock() - seen < self._dedupe_seconds()

    @staticmethod
    def _confidence(found: Detection) -> float:
        if found.verse_start is None:
            return 0.85                              # only a chapter was named
        return 0.90 if found.kind == "followup" else 0.97

    # -- main entry -------------------------------------------------------------
    def feed(
        self, utterance: int, text: str, final: bool, translation: str = "KJV"
    ) -> tuple[list[Candidate], list[tuple[int, int]]]:
        """Process the current text of an utterance.

        Returns (new candidates, character spans of every reference in the text --
        used to highlight the transcript).
        """
        if utterance != self._utterance:
            self._utterance = utterance
            self._emitted = {}
            self._previous_keys = set()

        context = copy.copy(self._context)
        found = self._parser.parse(text, context, translation)
        spans = [(d.start, d.end) for d in found]
        candidates: list[Candidate] = []
        keys_now: set[Key] = set()

        for d in found:
            key: Key = (d.book_id, d.chapter, d.verse_start, d.verse_end)
            keys_now.add(key)
            if key in self._emitted:
                continue
            words_after = len(_WORD.findall(text[d.end:]))
            if not (final or key in self._previous_keys or words_after >= 2):
                continue                             # still at the unstable tail: wait one more pass

            # "Romans 8" heard a second ago has become "Romans 8:28": refine, do not duplicate.
            replaces: int | None = None
            chapter_key: Key = (d.book_id, d.chapter, None, None)
            earlier = self._emitted.get(chapter_key)
            if d.verse_start is not None and d.kind == "direct" and earlier and earlier[1] == d.start:
                replaces = earlier[0]

            if replaces is None and self._is_recent(key):
                self._emitted[key] = (None, d.start)     # suggested moments ago: stay quiet
                continue

            first = d.verse_start or 1
            candidate = Candidate(
                id=self._next_id,
                ref=VerseRef(d.book_id, d.chapter, first, d.verse_end or first),
                whole_chapter=d.verse_start is None,
                kind="direct",
                followup=d.kind == "followup",
                confidence=self._confidence(d),
                heard=d.text,
                replaces=replaces,
            )
            self._next_id += 1
            self._emitted[key] = (candidate.id, d.start)
            self._recent[key] = self._clock()
            candidates.append(candidate)

        self._previous_keys = keys_now
        if final:
            self._context = context                  # later "verse 5" builds on this
        return candidates, spans

    # -- quoted / paraphrased verses (semantic matcher) ---------------------------
    def _covered_recently(self, book_id: int, chapter: int, verse: int) -> bool:
        """True if this verse (alone or inside a range) was suggested moments ago."""
        now, window = self._clock(), self._dedupe_seconds()
        for (book, chap, first, last), seen in self._recent.items():
            if book == book_id and chap == chapter and first is not None and now - seen < window:
                if first <= verse <= (last or first):
                    return True
        return False

    def feed_semantic(self, matches: Sequence[SemanticMatch]) -> list[Candidate]:
        """Process the semantic matches of the latest transcript window (best first).

        A verse is suggested only once it has shown up in two consecutive windows
        (one lucky score is not enough), and only if it was the best match of one of
        them. The second rule keeps look-alike verses out, while still catching verse 1
        when the preacher has already moved on to verse 2.
        """
        keys_now = {(m.book_id, m.chapter, m.verse) for m in matches}
        previous, self._semantic_previous = self._semantic_previous, keys_now
        previous_top, self._semantic_previous_top = self._semantic_previous_top, (
            (matches[0].book_id, matches[0].chapter, matches[0].verse) if matches else None
        )
        candidates: list[Candidate] = []
        for position, match in enumerate(matches):
            key = (match.book_id, match.chapter, match.verse)
            confirmed = key in previous and (position == 0 or key == previous_top)
            if not confirmed or self._covered_recently(*key):
                continue
            heard = match.window if len(match.window) <= 110 else "…" + match.window[-108:]
            candidates.append(
                Candidate(
                    id=self._next_id,
                    ref=VerseRef(match.book_id, match.chapter, match.verse, match.verse),
                    whole_chapter=False,
                    kind="quoted",
                    followup=False,
                    confidence=round(min(match.score, 0.99), 2),
                    heard=heard,
                )
            )
            self._next_id += 1
            self._recent[(match.book_id, match.chapter, match.verse, match.verse)] = self._clock()
        return candidates
