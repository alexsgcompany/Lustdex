"""normalize(raw) → normalized key string, or TRASH sentinel."""

import re
import unicodedata

from pipeline.tags.exceptions import EXCEPTIONS, STOPLIST

TRASH = "__TRASH__"

_SINGLE_MARKERS = frozenset({"18", "18+", "18yo"})


def normalize(raw: str) -> str:
    # Step 1: NFKC, lowercase, strip
    s = unicodedata.normalize("NFKC", raw).lower().strip()
    if not s:
        return TRASH

    # Step 2a: any alphabetic char that is not Latin → TRASH
    for ch in s:
        if ch.isalpha() and "LATIN" not in unicodedata.name(ch, ""):
            return TRASH

    # Step 3: replace separators (not + yet), drop remaining punctuation, collapse whitespace
    s = re.sub(r"[_\-./,&]", " ", s)
    s = re.sub(r"[^\w\s+]", "", s)   # keep word chars, whitespace, +
    s = s.replace("_", " ")           # \w matched underscores; clean up
    s = re.sub(r"\s+", " ", s).strip()

    if not s:
        return TRASH

    # Step 4: age-marker stripping
    # Normalize two-token "18 yo" → single token "18yo" before splitting
    s = re.sub(r"(?<!\w)18\s+yo(?!\w)", "18yo", s)
    tokens = s.split()
    non_markers = [t for t in tokens if t not in _SINGLE_MARKERS]
    if non_markers:
        tokens = non_markers
    # else: all tokens are markers → keep for exception dict
    s = " ".join(tokens)

    # After step 4: replace + with space, collapse
    s = re.sub(r"\+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    if not s:
        return TRASH

    # Step 5: stoplist (full-string match on the cleaned, age-stripped string)
    if s in STOPLIST:
        return TRASH

    # Step 6: exception dictionary (full-string match)
    applied_exception = s in EXCEPTIONS
    if applied_exception:
        s = EXCEPTIONS[s]

    # [a-z] guard: pure-numeric strings that are NOT protected by the exception dict
    if not applied_exception and not re.search(r"[a-z]", s):
        return TRASH

    # Step 7: singularize last token (rule-based, matching key only)
    tokens = s.split()
    tokens[-1] = _singularize(tokens[-1])
    return " ".join(tokens)


def _singularize(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if any(word.endswith(suffix) for suffix in ("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word
