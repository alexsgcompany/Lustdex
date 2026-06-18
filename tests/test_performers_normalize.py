"""normalize(name) for performers — spec 11 §P5."""

import pytest

from pipeline.performers.normalize import normalize


@pytest.mark.parametrize("raw,expected", [
    # basic case/whitespace fold
    ("Aubrey Kate",      "aubrey kate"),
    ("  Natalie  Mars ", "natalie mars"),
    # gender/honorific prefix stripped → folds the variant onto the base key
    ("Ts Izzy Wilde",    "izzy wilde"),
    ("Izzy Wilde",       "izzy wilde"),
    ("TS Foxxy",         "foxxy"),
    ("Shemale Foxxy",    "foxxy"),
    ("Ladyboy Mint",     "mint"),
    ("Tranny Jane",      "jane"),
    ("T-Girl Mia",       "mia"),
    # prefix only stripped at the START and on a word boundary
    ("Trans Angeles",    "angeles"),     # leading prefix word
    ("Tsunami Rose",     "tsunami rose"),  # 'ts' not a whole word → kept
    # NFKD ascii fold
    ("Renée",            "renee"),
    ("Chloë",            "chloe"),
    # punctuation → space, collapse
    ("Korra-Del-Rio",    "korra del rio"),
    ("Jane (TS)",        "jane ts"),
    # empties / noise that folds away
    ("",                 ""),
    ("   ",              ""),
    ("***",              ""),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected
