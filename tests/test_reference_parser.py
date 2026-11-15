"""Spoken-reference detection in running text (English + French).

Each case is (text, [expected references]) where a reference is
(book_id, chapter, verse_start, verse_end); verse_start None = whole chapter.
"""
from __future__ import annotations

import pytest

from app.bible_db import BibleDB
from app.reference_parser import BookIndex, ParserContext, ReferenceParser

Ref = tuple[int, int, int | None, int | None]

GEN, PSA, ISA, MAT, MRK, LUK, JHN, ACT, ROM = 1, 19, 23, 40, 41, 42, 43, 44, 45
COR1, COR2, GAL, EPH, PHP, TIM2, HEB, JAS, PET1, JN1, JN3, JUD, REV = 46, 47, 48, 49, 50, 55, 58, 59, 60, 62, 64, 65, 66
KI1, KI2, JOB, PRO, HAB, SNG, NUM, SA1 = 11, 12, 18, 20, 35, 22, 4, 9


@pytest.fixture(scope="module")
def parser(books: BookIndex, db: BibleDB) -> ReferenceParser:
    return ReferenceParser(books, db)


def refs(parser: ReferenceParser, text: str, context: ParserContext | None = None) -> list[Ref]:
    return [(d.book_id, d.chapter, d.verse_start, d.verse_end) for d in parser.parse(text, context)]


# ---------------------------------------------------------------- English
ENGLISH: list[tuple[str, list[Ref]]] = [
    # digits, as Whisper usually writes them
    ("John 3:16", [(JHN, 3, 16, 16)]),
    ("For God so loved the world, John 3:16, that he gave", [(JHN, 3, 16, 16)]),
    ("John 3.16", [(JHN, 3, 16, 16)]),
    ("John 3, 16", [(JHN, 3, 16, 16)]),
    ("John 3 16", [(JHN, 3, 16, 16)]),
    ("John 3-16", [(JHN, 3, 16, 16)]),
    ("John 316", [(JHN, 3, 16, 16)]),
    ("Romans 828 says all things work together", [(ROM, 8, 28, 28)]),
    ("John 3:16-18", [(JHN, 3, 16, 18)]),
    ("John 3:16 to 18", [(JHN, 3, 16, 18)]),
    ("Psalm 119", [(PSA, 119, None, None)]),
    ("Psalm 119:105", [(PSA, 119, 105, 105)]),
    # number words
    ("John three sixteen", [(JHN, 3, 16, 16)]),
    ("Romans eight twenty-eight", [(ROM, 8, 28, 28)]),
    ("Acts two thirty eight", [(ACT, 2, 38, 38)]),
    ("Isaiah fifty-three five", [(ISA, 53, 5, 5)]),
    ("Isaiah forty thirty one", [(ISA, 40, 31, 31)]),
    ("Matthew six thirty-three", [(MAT, 6, 33, 33)]),
    ("Genesis one one", [(GEN, 1, 1, 1)]),
    ("Psalm twenty three", [(PSA, 23, None, None)]),
    ("Psalm one hundred and nineteen", [(PSA, 119, None, None)]),
    ("Psalm one hundred nineteen verse one hundred five", [(PSA, 119, 105, 105)]),
    ("Psalm one nineteen", [(PSA, 119, None, None)]),
    # chapter / verse words
    ("John chapter 3 verse 16", [(JHN, 3, 16, 16)]),
    ("John chapter three, verse sixteen", [(JHN, 3, 16, 16)]),
    ("John 3 verses 16 to 18", [(JHN, 3, 16, 18)]),
    ("John chapter 3, verses 16 through 18", [(JHN, 3, 16, 18)]),
    ("John 3 verse 16 through 18", [(JHN, 3, 16, 18)]),
    ("John 3 verses 16 and 17", [(JHN, 3, 16, 17)]),
    ("Romans 8 and verse 28", [(ROM, 8, 28, 28)]),
    ("Romans chapter 8 and the verse 28", [(ROM, 8, 28, 28)]),
    ("the book of Hebrews, chapter eleven, verse one", [(HEB, 11, 1, 1)]),
    ("turn with me to Psalm 23", [(PSA, 23, None, None)]),
    ("open your Bibles to Ephesians chapter 2", [(EPH, 2, None, None)]),
    ("Revelation 21 verse 4", [(REV, 21, 4, 4)]),
    # numbered books
    ("First Corinthians 13", [(COR1, 13, None, None)]),
    ("1st Corinthians 13:4", [(COR1, 13, 4, 4)]),
    ("I Corinthians 13:4-7", [(COR1, 13, 4, 7)]),
    ("1 Corinthians thirteen four", [(COR1, 13, 4, 4)]),
    ("Second Kings chapter 2 verse 11", [(KI2, 2, 11, 11)]),
    ("II Kings 2:11", [(KI2, 2, 11, 11)]),
    ("the first book of Kings chapter 18", [(KI1, 18, None, None)]),
    ("Second Timothy one seven", [(TIM2, 1, 7, 7)]),
    ("the first epistle of John chapter 4 verse 8", [(JN1, 4, 8, 8)]),
    ("First Peter 5:7", [(PET1, 5, 7, 7)]),
    ("Third John verse 4", [(JN3, 1, 4, 4)]),
    ("First Samuel 17", [(SA1, 17, None, None)]),
    # one-chapter books
    ("Jude 5", [(JUD, 1, 5, 5)]),
    ("Jude verse 24", [(JUD, 1, 24, 24)]),
    # mis-heard / misspelled names
    ("Filipians 4:13", [(PHP, 4, 13, 13)]),
    ("Philipians four thirteen", [(PHP, 4, 13, 13)]),
    ("Habakuk 2:4", [(HAB, 2, 4, 4)]),
    ("Habbakuk chapter 3", [(HAB, 3, None, None)]),
    ("Galations 5:22", [(GAL, 5, 22, 22)]),
    ("Ephesian 2:8", [(EPH, 2, 8, 8)]),
    ("Psalms 23:1", [(PSA, 23, 1, 1)]),
    ("Revelations 3:20", [(REV, 3, 20, 20)]),
    ("Proverb 3:5", [(PRO, 3, 5, 5)]),
    ("Song of Solomon 2:1", [(SNG, 2, 1, 1)]),
    # several references in one sentence
    ("Read John 3:16 and then Romans 8:28", [(JHN, 3, 16, 16), (ROM, 8, 28, 28)]),
    ("Romans 8, 1 Corinthians 13", [(ROM, 8, None, None), (COR1, 13, None, None)]),
    # range end beyond the chapter is trimmed
    ("John 3:30-99", [(JHN, 3, 30, 36)]),
    # ambiguous everyday words that ARE references here
    ("Job 2", [(JOB, 2, None, None)]),
    ("job chapter two", [(JOB, 2, None, None)]),
    ("acts 2:38", [(ACT, 2, 38, 38)]),
    ("Mark 4", [(MRK, 4, None, None)]),
    ("Numbers 6 verse 24", [(NUM, 6, 24, 24)]),
]

# ----------------------------------------------------------------- French
FRENCH: list[tuple[str, list[Ref]]] = [
    ("Jean 3:16", [(JHN, 3, 16, 16)]),
    ("Jean 3.16", [(JHN, 3, 16, 16)]),
    ("Jean 3, 16", [(JHN, 3, 16, 16)]),
    ("Jean trois seize", [(JHN, 3, 16, 16)]),
    ("Jean chapitre trois verset seize", [(JHN, 3, 16, 16)]),
    ("Jean chapitre 3, verset 16", [(JHN, 3, 16, 16)]),
    ("Jean 3 versets 16 à 18", [(JHN, 3, 16, 18)]),
    ("Jean 3 du verset 16 au verset 18", [(JHN, 3, 16, 18)]),
    ("Jean 3 verset 16 jusqu'au verset 18", [(JHN, 3, 16, 18)]),
    ("Jean 3 versets 16 et 17", [(JHN, 3, 16, 17)]),
    ("Romains 8 verset 28", [(ROM, 8, 28, 28)]),
    ("Romains huit vingt-huit", [(ROM, 8, 28, 28)]),
    ("Romains chapitre huit, verset vingt-huit", [(ROM, 8, 28, 28)]),
    ("Premier Corinthiens 13", [(COR1, 13, None, None)]),
    ("1 Corinthiens 13:4", [(COR1, 13, 4, 4)]),
    ("Première Corinthiens chapitre treize", [(COR1, 13, None, None)]),
    ("1er Rois 3:9", [(KI1, 3, 9, 9)]),
    ("le premier livre des Rois chapitre 18", [(KI1, 18, None, None)]),
    ("Deuxième Corinthiens 5 verset 17", [(COR2, 5, 17, 17)]),
    ("2 Timothée 1:7", [(TIM2, 1, 7, 7)]),
    ("la deuxième épître à Timothée chapitre un verset sept", [(TIM2, 1, 7, 7)]),
    ("la première épître de Jean chapitre 4 verset 8", [(JN1, 4, 8, 8)]),
    ("Apocalypse 21", [(REV, 21, None, None)]),
    ("le livre de l'Apocalypse chapitre vingt et un verset quatre", [(REV, 21, 4, 4)]),
    ("Psaume 23", [(PSA, 23, None, None)]),
    ("Psaumes 23:1", [(PSA, 23, 1, 1)]),
    ("Psaume vingt-trois", [(PSA, 23, None, None)]),
    ("Psaume cent dix-neuf verset cent cinq", [(PSA, 119, 105, 105)]),
    ("Psaume quatre-vingt-onze", [(PSA, 91, None, None)]),
    ("Psaume nonante et un verset un", [(PSA, 91, 1, 1)]),
    ("Psaume cent cinquante", [(PSA, 150, None, None)]),
    ("Ésaïe 53 verset 5", [(ISA, 53, 5, 5)]),
    ("Esaie cinquante-trois cinq", [(ISA, 53, 5, 5)]),
    ("Matthieu six trente-trois", [(MAT, 6, 33, 33)]),
    ("Hébreux onze un", [(HEB, 11, 1, 1)]),
    ("Jacques 1 verset 5", [(JAS, 1, 5, 5)]),
    ("Actes 2:38", [(ACT, 2, 38, 38)]),
    ("Actes des Apôtres chapitre 2", [(ACT, 2, None, None)]),
    ("Genèse chapitre premier verset premier", [(GEN, 1, 1, 1)]),
    ("ouvrons nos bibles dans Éphésiens chapitre deux", [(EPH, 2, None, None)]),
    ("Philipiens 4:13", [(PHP, 4, 13, 13)]),
    ("Habakuk 2 verset 4", [(HAB, 2, 4, 4)]),
    ("Cantique des Cantiques 2:1", [(SNG, 2, 1, 1)]),
    ("Lisez Jean 3:16 et Romains 8:28", [(JHN, 3, 16, 16), (ROM, 8, 28, 28)]),
    # mixed English / French, as many pastors preach
    ("Let us read Jean 3 verset 16 together", [(JHN, 3, 16, 16)]),
    ("Dans Romans chapter 8 verset 28", [(ROM, 8, 28, 28)]),
]

# ------------------------------------------------ things that are NOT references
NOT_REFERENCES: list[str] = [
    "",
    "God so loved the world",
    "I got a new job two weeks ago",
    "mark my words, three of you will leave",
    "he acts 2 times a week",
    "the numbers 5 and 6 are missing",
    "John, one day, came to the river",
    "Jean, un homme envoyé de Dieu",
    "the answer is 5",                    # "is" is only a typed abbreviation of Isaiah
    "he 4 times said so",
    "I am 5 minutes late",
    "John 3:99",                          # verse does not exist
    "John 25:1",                          # chapter does not exist
    "John chapter 40",
    "Jude chapter 3",
    "Psaume 200 verset 1",
    "Corinthians 13:4",                   # which Corinthians? never guess
    "verse 5",                            # no chapter mentioned before
    "chapitre 3 verset 2",
    "on page 16 of the hymn book",
    "there were 3 Johns in the room",
    "Daniel 3 times a day prayed",
    "Luke 2 people came",
    "Marc 2 fois est venu",
    "we had a relation 3 years long",     # sounds like "Revelation 3", but no verse or "chapter"
]


@pytest.mark.parametrize("text, expected", ENGLISH)
def test_english(parser: ReferenceParser, text: str, expected: list[Ref]) -> None:
    assert refs(parser, text) == expected


@pytest.mark.parametrize("text, expected", FRENCH)
def test_french(parser: ReferenceParser, text: str, expected: list[Ref]) -> None:
    assert refs(parser, text) == expected


@pytest.mark.parametrize("text", NOT_REFERENCES)
def test_not_a_reference(parser: ReferenceParser, text: str) -> None:
    assert refs(parser, text) == []


def test_enough_cases() -> None:
    assert len(ENGLISH) + len(FRENCH) + len(NOT_REFERENCES) >= 60
    assert len(ENGLISH) >= 30 and len(FRENCH) >= 30


# ------------------------------------------------------------ context follow-ups
def test_followup_verse_in_same_text(parser: ReferenceParser) -> None:
    assert refs(parser, "Turn to Romans 8. Look at verse 28, and then verse 31.") == [
        (ROM, 8, None, None), (ROM, 8, 28, 28), (ROM, 8, 31, 31),
    ]


def test_followup_across_transcript_chunks(parser: ReferenceParser) -> None:
    context = ParserContext()
    assert refs(parser, "John chapter 3", context) == [(JHN, 3, None, None)]
    assert refs(parser, "now look at verse 7", context) == [(JHN, 3, 7, 7)]
    assert refs(parser, "and verses 16 through 18", context) == [(JHN, 3, 16, 18)]
    assert refs(parser, "then chapter 4 verse 24", context) == [(JHN, 4, 24, 24)]
    assert refs(parser, "verse 5", context) == [(JHN, 4, 5, 5)]      # chapter 4 is now active


def test_followup_french(parser: ReferenceParser) -> None:
    context = ParserContext()
    assert refs(parser, "Romains chapitre huit", context) == [(ROM, 8, None, None)]
    assert refs(parser, "regardons le verset 5", context) == [(ROM, 8, 5, 5)]
    assert refs(parser, "au verset vingt-huit", context) == [(ROM, 8, 28, 28)]
    assert refs(parser, "les versets 16 à 18", context) == [(ROM, 8, 16, 18)]
    assert refs(parser, "chapitre douze verset deux", context) == [(ROM, 12, 2, 2)]


def test_followup_is_marked_and_validated(parser: ReferenceParser) -> None:
    context = ParserContext()
    direct = parser.parse("Psalm 23", context)[0]
    follow = parser.parse("verse 4", context)[0]
    assert (direct.kind, follow.kind) == ("direct", "followup")
    assert parser.parse("verse 40", context) == []        # Psalm 23 has 6 verses
    assert parser.parse("chapter 200", context) == []


def test_new_book_replaces_context(parser: ReferenceParser) -> None:
    context = ParserContext()
    refs(parser, "John 3:16", context)
    refs(parser, "Romans 8", context)
    assert refs(parser, "verse 1", context) == [(ROM, 8, 1, 1)]


def test_span_points_at_the_matched_words(parser: ReferenceParser) -> None:
    text = "Please turn with me to First Corinthians chapter 13, verse 4 this morning."
    detection = parser.parse(text)[0]
    assert text[detection.start:detection.end] == "First Corinthians chapter 13, verse 4"
    assert detection.text == "First Corinthians chapter 13, verse 4"


def test_french_validation_uses_requested_translation(parser: ReferenceParser) -> None:
    # Psalm 3 has 8 verses in the KJV but 9 in Louis Segond (the title is verse 1).
    assert parser.parse("Psaume 3 verset 9", translation="KJV") == []
    assert [d.verse_start for d in parser.parse("Psaume 3 verset 9", translation="LSG")] == [9]
