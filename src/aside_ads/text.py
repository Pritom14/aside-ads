"""Small, dependency-free text helpers shared by matching, keyword filters and the sensitive guard."""

from __future__ import annotations

import re
from functools import lru_cache

# Split on whitespace and punctuation (ASCII plus the Devanagari danda) but keep
# non-Latin words intact: Python's \w would split Indic words at vowel signs.
_SPLIT = re.compile(
    r"[\s.,;:!?\u00a1\u00bf()\[\]{}\"`/\\|<>@#$%^&*+=~\-_"
    r"\u0964\u0965\u2013\u2014\u2018\u201c\u201d\u2026]+"
)

STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are as at be because been before being below
    between both but by can could did do does doing down during each few for from further had has have
    having he her here hers herself him himself his how i if in into is it its itself just me more most
    my myself no nor not now of off on once only or other our ours ourselves out over own same she
    should so some such than that the their theirs them themselves then there these they this those
    through to too under until up very was we were what when where which while who whom why will with
    would you your yours yourself yourselves im ive id youre dont doesnt didnt cant wont isnt arent
    wasnt please thanks thank hi hello hey want need get got like also really much many make know tell
    give way use using one two new best good help us let lets something anything thing things
    """.split()  # noqa: SIM905
)


def _undouble(word: str) -> str:
    if len(word) > 2 and word[-1] == word[-2] and word[-1] not in "lsz":
        return word[:-1]
    return word


def _stem(word: str) -> str:
    """Very light English suffix stripping: 'queries' -> 'query', 'indexing' -> 'index'.

    Both sides of every comparison go through the same function, so the stems
    only need to be consistent, not linguistically correct.
    """
    if word.endswith("'s"):
        word = word[:-2]
    word = word.strip("'")
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    if len(word) > 5 and word.endswith("ing"):
        return _undouble(word[:-3])
    if len(word) > 5 and word.endswith("ed") and not word.endswith("eed"):
        return _undouble(word[:-2])
    return word


@lru_cache(maxsize=4096)
def normalize_token(word: str) -> str:
    return _stem(word.lower())


def tokenize(text: str, *, drop_stopwords: bool = False) -> list[str]:
    out: list[str] = []
    # Curly apostrophes become straight ones and are then dropped: "don’t" -> "dont".
    for raw in _SPLIT.split(text.lower().replace("’", "'")):
        raw = raw.replace("'", "")
        if not raw:
            continue
        if drop_stopwords and raw in STOPWORDS:
            continue
        out.append(_stem(raw))
    return out


def normalized_phrase_text(text: str) -> str:
    """Token string padded with spaces, for whole-phrase containment checks."""
    return " " + " ".join(tokenize(text)) + " "


@lru_cache(maxsize=8192)
def _phrase_key(phrase: str) -> str:
    return " ".join(tokenize(phrase))


def contains_phrase(normalized_text: str, phrase: str) -> bool:
    p = _phrase_key(phrase)
    return bool(p) and f" {p} " in normalized_text


def any_phrase(normalized_text: str, phrases: list[str]) -> str | None:
    for phrase in phrases:
        if contains_phrase(normalized_text, phrase):
            return phrase
    return None
