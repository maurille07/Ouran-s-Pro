"""Bible reference detection.

Two entry points, both driven by the book names in ``data/books.json``:

* ``parse_typed_reference`` -- the operator's search box: ``jn 3 16``,
  ``romains 8:28``, ``1 cor 13:4-7``, ``Ps 23`` (abbreviations allowed).

* ``ReferenceParser.parse`` -- running text from a sermon transcript, English and
  French, the way preachers say it: "John three sixteen", "First Corinthians
  chapter 13 verses 4 to 7", "Jean chapitre trois verset seize", and follow-ups
  such as "look at verse 7" that rely on the chapter mentioned just before.
  Every detection is validated against the real chapter/verse counts.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rapidfuzz import fuzz, process

from . import config

# Books that have a single chapter: "Jude 5" means Jude 1:5.
SINGLE_CHAPTER_BOOKS: frozenset[int] = frozenset({31, 57, 63, 64, 65})

# Leading ordinals for numbered books, EN + FR ("First Corinthians", "1er Rois", "II Kings").
_ORDINALS: dict[str, str] = {
    "1": "1", "1st": "1", "first": "1", "i": "1", "one": "1",
    "premier": "1", "premiere": "1", "1er": "1", "1ere": "1", "1e": "1", "un": "1",
    "2": "2", "2nd": "2", "second": "2", "ii": "2", "two": "2",
    "seconde": "2", "deuxieme": "2", "2e": "2", "2eme": "2", "2nde": "2", "deux": "2",
    "3": "3", "3rd": "3", "third": "3", "iii": "3", "three": "3",
    "troisieme": "3", "3e": "3", "3eme": "3", "trois": "3",
}
_ORDINAL_PREFIX = re.compile(r"^(" + "|".join(sorted(_ORDINALS, key=len, reverse=True)) + r")\s+(?=[a-z])")
_DIGIT_GLUED = re.compile(r"^([1-3])(?=[a-z])")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Lowercase, remove accents, turn punctuation into single spaces."""
    text = strip_accents(text).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def book_key(name: str) -> str:
    """Canonical lookup key for a book name: '1st Corinthians' -> '1corinthians'."""
    text = normalize(name)
    text = _ORDINAL_PREFIX.sub(lambda m: _ORDINALS[m.group(1)] + " ", text)  # "1st cor" -> "1 cor"
    text = _DIGIT_GLUED.sub(r"\1 ", text)                                    # "1cor" -> "1 cor"
    return text.replace(" ", "")


@dataclass(frozen=True)
class ParsedReference:
    """A reference as written. ``verse_start`` is None for a whole chapter ('Psalm 23')."""

    book_id: int
    chapter: int
    verse_start: int | None = None
    verse_end: int | None = None


class BookIndex:
    """Resolves book names, abbreviations and misspellings to canonical ids (1-66)."""

    def __init__(self, books_json: Path = config.BOOKS_JSON) -> None:
        meta = json.loads(books_json.read_text(encoding="utf-8"))
        self._keys: dict[str, int] = {}
        # Forms accepted in *speech*: full names plus aliases of 4+ letters. Shorter
        # aliases ("is", "he", "am", "so"...) are typed abbreviations only -- in a
        # sermon they are ordinary words. Split by leading number: "" / "1" / "2" / "3".
        self.spoken: dict[str, dict[str, int]] = {"": {}, "1": {}, "2": {}, "3": {}}
        for book in meta["books"]:
            names: list[str] = list(book["names"].values())
            aliases: list[str] = [a for alias_list in book.get("aliases", {}).values() for a in alias_list]
            for form in names + aliases:
                key = book_key(form)
                # First book to claim a key keeps it (canonical order wins on clashes).
                self._keys.setdefault(key, book["id"])
                number = key[0] if key[:1].isdigit() else ""
                body = key[len(number):]
                if form in names or len(body) >= 4:
                    self.spoken[number].setdefault(body, book["id"])

    def resolve(self, name: str, fuzzy: bool = True) -> int | None:
        """Book id for name, or None. Tries exact, then prefix, then fuzzy match."""
        key = book_key(name)
        if not key:
            return None
        if key in self._keys:
            return self._keys[key]
        # Numbered books only compete with books that share the same number.
        number = key[0] if key[0].isdigit() else ""
        candidates = {k: v for k, v in self._keys.items() if (k[0] if k[0].isdigit() else "") == number}
        body = key[len(number):]
        if len(body) >= 2:
            prefixed = [book_id for k, book_id in candidates.items() if k.startswith(key)]
            if prefixed:
                return min(prefixed)
        if fuzzy and len(body) >= 4:
            hit = process.extractOne(key, list(candidates), scorer=fuzz.ratio, score_cutoff=80)
            if hit:
                return candidates[hit[0]]
        return None


# book  [chapter-word]  chapter  [separator verse [- verse]]
_TYPED_REF = re.compile(
    r"""^\s*
    (?P<book>.*?[a-z])\.?\s*
    (?:(?:chapter|chapitre|chap|ch)\.?\s*)?
    (?P<chapter>\d{1,3})
    (?:
        (?:\s*(?:verses?|versets?|vs|vv|v|[:.,])\s*|\s+)
        (?P<v1>\d{1,3})
        (?:\s*(?:-|to|through|thru|a|au)\s*(?P<v2>\d{1,3}))?
    )?
    \s*$""",
    re.VERBOSE,
)


def parse_typed_reference(text: str, books: BookIndex) -> ParsedReference | None:
    """Parse a reference typed by the operator. Returns None if it is not a reference.

    The result is *not* yet validated against real chapter/verse counts.
    """
    cleaned = strip_accents(text).lower().replace("–", "-").replace("—", "-")
    match = _TYPED_REF.match(cleaned)
    if not match:
        return None
    book_id = books.resolve(match.group("book"))
    if book_id is None:
        return None
    chapter = int(match.group("chapter"))
    v1 = int(match.group("v1")) if match.group("v1") else None
    v2 = int(match.group("v2")) if match.group("v2") else None
    if book_id in SINGLE_CHAPTER_BOOKS and v1 is None and chapter != 1:
        # "Jude 5" -> Jude 1:5
        chapter, v1 = 1, chapter
    if v1 is not None and v2 is not None and v2 < v1:
        v2 = None
    return ParsedReference(book_id, chapter, v1, v2 if v2 is not None else v1)


# ===========================================================================
# Spoken references in running text (sermon transcript)
# ===========================================================================

class VerseCounts(Protocol):
    """What the parser needs to validate references (implemented by BibleDB)."""

    def chapter_count(self, book_id: int, translation: str = "KJV") -> int: ...

    def verse_count(self, book_id: int, chapter: int, translation: str = "KJV") -> int: ...


@dataclass(frozen=True)
class Detection:
    """One reference found in a text. ``verse_start`` is None for a whole chapter."""

    book_id: int
    chapter: int
    verse_start: int | None
    verse_end: int | None
    start: int          # character span of the matched words in the original text
    end: int
    text: str           # the matched words, e.g. "John chapter 3 verse 16"
    kind: str           # "direct" (book was named) or "followup" ("verse 5" using context)


@dataclass
class ParserContext:
    """Remembers the last book/chapter so that a bare "verse 5" can be resolved."""

    book_id: int | None = None
    chapter: int | None = None


@dataclass(frozen=True)
class _Tok:
    kind: str           # "w" word, "n" number written in digits, "p" punctuation
    norm: str           # lowercase, no accents
    raw: str
    start: int
    end: int
    value: int = 0      # for numbers
    tight: bool = False  # punctuation glued on both sides, as in "3.16"


_TOKEN = re.compile(
    r"(?P<num>\d+)(?:(?:st|nd|rd|th|ers?|[eè]res?|[eè]mes?|nde?|e)(?![^\W\d_]))?"
    r"|(?P<word>[^\W\d_]+)"
    r"|(?P<punct>[:.,;\-–—])",
    re.IGNORECASE,
)

_CHAPTER_WORDS = frozenset({"chapter", "chapters", "chapitre", "chapitres", "chap"})
_VERSE_WORDS = frozenset({"verse", "verses", "verset", "versets"})
# Small words allowed between the chapter and the word "verse": "Romans 8 and verse 28",
# "Jean 3 du verset 16 au verset 18".
_VERSE_FILLERS = frozenset(
    {"and", "et", "at", "from", "the", "in", "with", "starting", "beginning",
     "du", "de", "le", "les", "au", "aux", "a"}
)
_RANGE_WORDS = frozenset({"to", "through", "thru", "until", "till", "a", "au", "jusqu"})
_AND_WORDS = frozenset({"and", "et"})
_DASHES = frozenset({"-", "–", "—"})

# Ordinals in front of numbered books ("First Corinthians", "Deuxième Timothée").
_SPOKEN_ORDINALS: dict[str, str] = {
    "first": "1", "premier": "1", "premiere": "1",
    "second": "2", "seconde": "2", "deuxieme": "2",
    "third": "3", "troisieme": "3",
}
# Words that may sit between the ordinal and the name: "first book of Kings",
# "première épître de Jean", "deuxième lettre aux Corinthiens".
_ORDINAL_FILLERS = frozenset(
    {"book", "epistle", "letter", "of", "to", "the",
     "livre", "epitre", "lettre", "de", "des", "du", "d", "aux", "a"}
)
# Book names that are also everyday words. A bare "job two" is only accepted with
# more evidence (a verse, the word "chapter", or a capital letter: "Job 2").
_WEAK_NAMES = frozenset(
    {"job", "mark", "acts", "numbers", "judges", "song", "songs",
     "nombres", "actes", "juges", "cantique", "cantiques"}
)
# Number words that are usually articles/pronouns: "John, one day..." / "Jean, un homme...".
_AMBIGUOUS_ONES = frozenset({"one", "un", "une"})

# After a bare "Book N", these words mean N was counting something: "Daniel 3 times a day".
_COUNT_NOUNS = frozenset(
    {"time", "times", "year", "years", "day", "days", "week", "weeks", "month", "months",
     "hour", "hours", "minute", "minutes", "people", "men", "women", "things", "sons", "daughters",
     "percent", "fois", "an", "ans", "annee", "annees", "jour", "jours", "semaine", "semaines",
     "mois", "heure", "heures", "personnes", "hommes", "femmes", "choses", "fils", "filles"}
)

# -- number words -------------------------------------------------------------
_EN_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
_EN_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
             "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
            "seventy": 70, "eighty": 80, "ninety": 90}
_FR_SMALL = {"un": 1, "une": 1, "premier": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
             "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13,
             "quatorze": 14, "quinze": 15, "seize": 16}
# septante / huitante / octante / nonante: Belgian, Swiss and Congolese French.
_FR_TENS = {"vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
            "septante": 70, "huitante": 80, "octante": 80, "nonante": 90}


def _tokenize(text: str) -> list[_Tok]:
    toks: list[_Tok] = []
    for m in _TOKEN.finditer(text):
        start, end = m.span()
        if m.group("num"):
            toks.append(_Tok("n", m.group("num"), m.group(), start, end, value=int(m.group("num"))))
        elif m.group("word"):
            toks.append(_Tok("w", strip_accents(m.group()).lower(), m.group(), start, end))
        else:
            tight = start > 0 and end < len(text) and not text[start - 1].isspace() and not text[end].isspace()
            toks.append(_Tok("p", m.group(), m.group(), start, end, tight=tight))
    # A hyphen inside words is just spelling ("vingt-trois", "twenty-one"): drop it.
    # Next to a digit it is kept, because "16-18" is a range.
    kept: list[_Tok] = []
    for i, tok in enumerate(toks):
        if tok.kind == "p" and tok.norm in _DASHES:
            prev_num = i > 0 and toks[i - 1].kind == "n"
            next_num = i + 1 < len(toks) and toks[i + 1].kind == "n"
            if not (prev_num or next_num):
                continue
        kept.append(tok)
    return kept


class ReferenceParser:
    """Finds Bible references in running English/French text."""

    def __init__(self, books: BookIndex, counts: VerseCounts) -> None:
        self.books = books
        self.counts = counts

    def parse(
        self, text: str, context: ParserContext | None = None, translation: str = "KJV"
    ) -> list[Detection]:
        """Return the references found in text, in order.

        ``context`` carries the active book/chapter between calls (pass the same
        object for each new piece of transcript); it is updated in place.
        """
        return _Scan(self, text, context if context is not None else ParserContext(), translation).run()


class _Scan:
    """One pass over one text (keeps the token list so helpers stay short)."""

    def __init__(self, parser: ReferenceParser, text: str, ctx: ParserContext, translation: str) -> None:
        self.books = parser.books
        self.counts = parser.counts
        self.text = text
        self.ctx = ctx
        self.translation = translation
        self.toks = _tokenize(text)

    # -- token helpers ------------------------------------------------------
    def norm(self, i: int) -> str:
        return self.toks[i].norm if 0 <= i < len(self.toks) else ""

    def is_word(self, i: int) -> bool:
        return 0 <= i < len(self.toks) and self.toks[i].kind == "w"

    def punct(self, i: int) -> str:
        return self.toks[i].norm if 0 <= i < len(self.toks) and self.toks[i].kind == "p" else ""

    def chapters(self, book_id: int) -> int:
        return self.counts.chapter_count(book_id, self.translation)

    def verses(self, book_id: int, chapter: int) -> int:
        return self.counts.verse_count(book_id, chapter, self.translation)

    # -- numbers ------------------------------------------------------------
    def read_number(self, i: int) -> tuple[int, int] | None:
        """Read one number (digits or EN/FR words) at token i -> (value, next index)."""
        if not 0 <= i < len(self.toks):
            return None
        tok = self.toks[i]
        if tok.kind == "n":
            return tok.value, i + 1
        if tok.kind != "w":
            return None
        return self._english_number(i) or self._french_number(i)

    def _english_under_100(self, i: int) -> tuple[int, int] | None:
        word = self.norm(i)
        if word in _EN_TENS:
            if self.norm(i + 1) in _EN_UNITS:               # "twenty one"
                return _EN_TENS[word] + _EN_UNITS[self.norm(i + 1)], i + 2
            return _EN_TENS[word], i + 1
        if word in _EN_TEENS:
            return _EN_TEENS[word], i + 1
        if word in _EN_UNITS:
            return _EN_UNITS[word], i + 1
        return None

    def _english_number(self, i: int) -> tuple[int, int] | None:
        word = self.norm(i)
        if word in _EN_UNITS and self.norm(i + 1) == "hundred":  # "one hundred (and) nineteen"
            base, j = _EN_UNITS[word] * 100, i + 2
            rest = self._english_under_100(j + 1 if self.norm(j) == "and" else j)
            return (base + rest[0], rest[1]) if rest else (base, j)
        return self._english_under_100(i)

    def _french_small(self, i: int, limit: int) -> tuple[int, int] | None:
        """un..seize, dix-sept..dix-neuf (up to limit)."""
        word = self.norm(i)
        if word == "dix" and limit >= 19 and self.norm(i + 1) in ("sept", "huit", "neuf"):
            return 10 + _FR_SMALL[self.norm(i + 1)], i + 2
        if word in _FR_SMALL and _FR_SMALL[word] <= limit:
            return _FR_SMALL[word], i + 1
        return None

    def _french_under_100(self, i: int) -> tuple[int, int] | None:
        word = self.norm(i)
        if word == "quatre" and self.norm(i + 1) in ("vingt", "vingts"):   # quatre-vingt(-dix-sept)
            rest = self._french_small(i + 2, 19)
            return (80 + rest[0], rest[1]) if rest else (80, i + 2)
        if word in _FR_TENS:
            base = _FR_TENS[word]
            limit = 19 if word == "soixante" else 9                          # soixante-dix-sept = 77
            if self.norm(i + 1) == "et" and self.norm(i + 2) in ("un", "une", "onze"):
                extra = 11 if self.norm(i + 2) == "onze" else 1              # vingt et un, soixante et onze
                if extra <= limit:
                    return base + extra, i + 3
            rest = self._french_small(i + 1, limit)
            return (base + rest[0], rest[1]) if rest else (base, i + 1)
        return self._french_small(i, 19)

    def _french_number(self, i: int) -> tuple[int, int] | None:
        if self.norm(i) == "cent":                                           # cent dix-neuf
            rest = self._french_under_100(i + 1)
            return (100 + rest[0], rest[1]) if rest else (100, i + 1)
        return self._french_under_100(i)

    # -- book names ---------------------------------------------------------
    def _name_at(self, j: int, number: str) -> tuple[int, int, str] | None:
        """A book name starting at token j -> (book id, next index, matched key)."""
        table = self.books.spoken[number]
        for size in (4, 3, 2, 1):                       # longest first: "song of solomon"
            if all(self.is_word(j + n) for n in range(size)):
                key = "".join(self.norm(j + n) for n in range(size))
                if key in table:
                    return table[key], j + size, key
        # Misspelled / mis-heard name ("Galations 5", "Ephesian 2"): only tried when a
        # chapter clearly follows, to keep ordinary words out.
        if self.is_word(j):
            word = self.norm(j)
            follows = self.norm(j + 1) in _CHAPTER_WORDS or self.read_number(j + 1) is not None
            if follows and len(word) >= 5 and word not in _CHAPTER_WORDS and word not in _VERSE_WORDS:
                hit = process.extractOne(word, list(table), scorer=fuzz.ratio, score_cutoff=85)
                if hit:
                    return table[hit[0]], j + 1, "~" + hit[0]      # "~" marks a fuzzy match
        return None

    def match_book(self, i: int) -> tuple[int, int, bool, bool] | None:
        """A book at token i -> (book id, next index, needs extra evidence, is numbered book)."""
        tok = self.toks[i]
        ordinal, roman = "", False
        if tok.kind == "n" and tok.norm in ("1", "2", "3"):
            ordinal = tok.norm                                   # "1 Corinthians", "1er Rois", "2nd Kings"
        elif tok.kind == "w" and tok.norm in _SPOKEN_ORDINALS:
            ordinal = _SPOKEN_ORDINALS[tok.norm]
        elif tok.kind == "w" and tok.raw in ("I", "II", "III"):
            ordinal, roman = str(len(tok.raw)), True             # "II Kings"
        if ordinal:
            j = i + 1
            if not roman:
                skipped = 0
                while skipped < 4 and self.norm(j) in _ORDINAL_FILLERS:
                    j, skipped = j + 1, skipped + 1
            hit = self._name_at(j, ordinal) or (self._name_at(i + 1, ordinal) if j != i + 1 else None)
            if hit:
                return hit[0], hit[1], False, True
        if tok.kind == "w" and tok.norm not in _CHAPTER_WORDS and tok.norm not in _VERSE_WORDS:
            hit = self._name_at(i, "")
            if hit:
                # Everyday words and fuzzy matches need more evidence than a bare number.
                weak = hit[2].startswith("~") or (hit[2] in _WEAK_NAMES and not tok.raw[:1].isupper())
                return hit[0], hit[1], weak, False
        return None

    # -- verses -------------------------------------------------------------
    def range_end(self, k: int, first: int) -> tuple[int | None, int]:
        """Optional "- 18", "to 18", "through verse 18", "à 18", "et 17" -> (last verse, next index)."""
        j = k
        if self.punct(j) in _DASHES or (self.is_word(j) and self.norm(j) in _RANGE_WORDS):
            if self.norm(j) == "jusqu" and self.norm(j + 1) in ("a", "au"):   # jusqu'à, jusqu'au
                j += 1
            j += 1
            if self.norm(j) in ("the", "le"):
                j += 1
            if self.norm(j) in _VERSE_WORDS:
                j += 1
            number = self.read_number(j)
            if number and number[0] > first:
                return number
        elif self.is_word(j) and self.norm(j) in _AND_WORDS:                 # "verses 16 and 17"
            j += 1
            if self.norm(j) in _VERSE_WORDS:
                j += 1
            number = self.read_number(j)
            if number and number[0] == first + 1:
                return number
        return None, k

    def verse_numbers(self, k: int) -> tuple[int, int | None, int] | None:
        """A verse number and optional range at token k -> (first, last, next index)."""
        number = self.read_number(k)
        if not number:
            return None
        last, j = self.range_end(number[1], number[0])
        return number[0], last, j

    def verse_part(self, k: int) -> tuple[int, int | None, int, bool] | None:
        """What follows a chapter number -> (first, last, next index, explicit).

        explicit=True when the speaker/writer clearly marked a verse (":" or the
        word "verse"); False for a bare second number ("John three sixteen").
        """
        sep = self.punct(k)
        if sep == ":" or (sep == "." and self.toks[k].tight):                # 3:16  3.16
            found = self.verse_numbers(k + 1)
            return (*found, True) if found else None
        if sep in _DASHES and self.toks[k].tight:                            # "3-16" (Whisper does this)
            found = self.verse_numbers(k + 1)
            return (*found, False) if found else None
        if sep == ",":                                                       # "John 3, 16" / "John 3, verse 16"
            k += 1
        j, skipped = k, 0
        while skipped < 3 and self.is_word(j) and self.norm(j) in _VERSE_FILLERS:
            j, skipped = j + 1, skipped + 1
        if self.is_word(j) and self.norm(j) in _VERSE_WORDS:                 # "... and verse 28"
            found = self.verse_numbers(j + 1)
            return (*found, True) if found else None
        if k < len(self.toks):
            numbered_book = self.match_book(k)
            if numbered_book and numbered_book[3]:                           # "Romans 8, 1 Corinthians 13"
                return None
        found = self.verse_numbers(k)                                        # "John three sixteen"
        return (*found, False) if found else None

    # -- assembling detections ------------------------------------------------
    def _make(self, i: int, j: int, book_id: int, chapter: int,
              first: int | None, last: int | None, kind: str) -> tuple[Detection, int]:
        start, end = self.toks[i].start, self.toks[j - 1].end
        if first is not None:
            limit = self.verses(book_id, chapter)
            last = min(last, limit) if last else None
            last = last if last and last > first else first
        self.ctx.book_id, self.ctx.chapter = book_id, chapter
        return Detection(book_id, chapter, first, last, start, end, self.text[start:end], kind), j

    def after_book(self, i: int, book: tuple[int, int, bool, bool]) -> tuple[Detection, int] | None:
        """Parse "[chapter] N [verse M [to P]]" after a book name that started at token i."""
        book_id, k, weak, _ = book
        if self.punct(k) == "," and self.norm(k + 1) in _CHAPTER_WORDS:      # "John, chapter 3"
            k += 1
        keyword = self.norm(k) in _CHAPTER_WORDS or self.norm(k) == "ch"
        if keyword:
            k += 1
            if self.punct(k) == ".":
                k += 1
        number = self.read_number(k)
        if number is None:
            # One-chapter book: "Jude verse 5"
            if book_id in SINGLE_CHAPTER_BOOKS and self.norm(k) in _VERSE_WORDS:
                found = self.verse_numbers(k + 1)
                if found and found[0] <= self.verses(book_id, 1):
                    return self._make(i, found[2], book_id, 1, found[0], found[1], "direct")
            return None
        chapter_tok = self.toks[k]
        # A lone "one" / "un" / "une" is usually not a number: "John, one day..."
        lone_one = chapter_tok.norm in _AMBIGUOUS_ONES and number[1] == k + 1
        chapter, k = number
        chapter_ok = 1 <= chapter <= self.chapters(book_id)
        part = self.verse_part(k)

        if part is not None and chapter_ok:
            first, last, j, explicit = part
            if 1 <= first <= self.verses(book_id, chapter):
                return self._make(i, j, book_id, chapter, first, last, "direct")
            if explicit:
                return None                                  # "John 3:99" does not exist
            # "Psalm one nineteen" = Psalm 119
            combined = chapter * 100 + first
            if chapter < 10 <= first <= 99 and combined <= self.chapters(book_id):
                return self._make(i, j, book_id, combined, None, None, "direct")
            part = None                                      # keep the chapter, ignore the stray number

        if part is None:
            if book_id in SINGLE_CHAPTER_BOOKS and not keyword and 1 < chapter <= self.verses(book_id, 1):
                return self._make(i, k, book_id, 1, chapter, None, "direct")       # "Jude 5" = Jude 1:5
            if not chapter_ok:
                # Whisper often writes "John 316" for "John three sixteen".
                digits = chapter_tok.norm
                if chapter_tok.kind == "n" and not keyword and 3 <= len(digits) <= 4:
                    for cut in range(1, len(digits)):
                        ch, vs = int(digits[:cut]), int(digits[cut:])
                        if digits[cut] != "0" and 1 <= ch <= self.chapters(book_id) and vs <= self.verses(book_id, ch):
                            return self._make(i, k, book_id, ch, vs, None, "direct")
                return None
            if not keyword and (weak or lone_one or self.norm(k) in _COUNT_NOUNS):
                return None       # "a new job two weeks ago", "John, one day", "Daniel 3 times a day"
            return self._make(i, k, book_id, chapter, None, None, "direct")
        return None

    def followup(self, i: int) -> tuple[Detection, int] | None:
        """"verse 5" / "chapter 4 verse 2" with no book: use the active book and chapter."""
        word = self.norm(i)
        book_id, chapter = self.ctx.book_id, self.ctx.chapter
        if book_id is None:
            return None
        if word in _VERSE_WORDS and chapter is not None:
            found = self.verse_numbers(i + 1)
            if found and 1 <= found[0] <= self.verses(book_id, chapter):
                return self._make(i, found[2], book_id, chapter, found[0], found[1], "followup")
        elif word in _CHAPTER_WORDS:
            number = self.read_number(i + 1)
            if number and 1 <= number[0] <= self.chapters(book_id):
                new_chapter, k = number
                part = self.verse_part(k)
                if part and 1 <= part[0] <= self.verses(book_id, new_chapter):
                    return self._make(i, part[2], book_id, new_chapter, part[0], part[1], "followup")
                if part is None or not part[3]:
                    return self._make(i, k, book_id, new_chapter, None, None, "followup")
        return None

    def run(self) -> list[Detection]:
        found: list[Detection] = []
        i = 0
        while i < len(self.toks):
            result: tuple[Detection, int] | None = None
            if self.toks[i].kind != "p":
                book = self.match_book(i)
                if book:
                    result = self.after_book(i, book)
                if result is None and self.toks[i].kind == "w":
                    result = self.followup(i)
            if result:
                found.append(result[0])
                i = result[1]
            else:
                i += 1
        return found
