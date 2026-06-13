"""All spec examples from specs/01-tags.md §2, plus edge cases."""

import pytest
from pipeline.tags.normalize import TRASH, normalize


@pytest.mark.parametrize("raw,expected", [
    # ── §2 spec examples ──────────────────────────────────────────────────────
    ("Big_Tits",     "big tit"),       # separators + case + singular
    ("big boobs",    "big boob"),      # stays distinct (synonym is L2/L3's job)
    ("Teen (18+)",   "teen"),          # parens die in step 3, marker in step 4
    ("Teens 18+",    "teen"),          # marker strip + singularize
    ("School (18+)", "school"),
    ("18yo",         "teen"),          # marker-only → exception dict
    ("3some",        "threesome"),     # exception dict
    ("69",           "69"),            # protected identity
    ("Doggy style",  "doggy style"),   # case collapse
    ("Doggy Style",  "doggy style"),
    ("Face Sitting", "face sitting"),  # NOT merged with 'facesitting' here (L2's job)
    ("RedHead",      "redhead"),
    ("Wives",        "wife"),          # irregular plural via exception dict
    ("Feet",         "foot"),          # irregular plural via exception dict
    ("Glasses",      "glasses"),       # sses-rule would give 'glass'; exception protects it
    ("Pussies",      "pussy"),         # ies-rule
    # acronyms: survive singularization (don't end in s)
    ("BDSM",  "bdsm"),
    ("POV",   "pov"),
    ("MILF",  "milf"),
    ("BBC",   "bbc"),
    # TRASH
    ("Sex",     TRASH),          # stoplist: true noise
    ("porn",    TRASH),          # stoplist: true noise
    ("Hot",     "hot"),          # modifier tag — passes through, resolved via L1
    ("Beauty",  "beauty"),       # modifier tag
    ("sexy",    "sexy"),         # modifier tag
    ("日本人",  TRASH),  # non-Latin script
    ("!!!",     TRASH),  # no letters left after cleaning
    # Latin non-English: passes through, lands in review queue (D6)
    # singularize rule drops trailing 's' → transexuale (rules are applied)
    ("transexuales", "transexuale"),

    # ── additional edge cases ─────────────────────────────────────────────────
    ("18",         "teen"),          # bare age marker → exception dict
    ("18+",        "teen"),          # age marker with +
    ("18 yo",      "teen"),          # two-token age marker
    ("teen 18",    "teen"),          # marker stripped when other token exists
    ("schoolgirl 18+", "schoolgirl"),
    ("big tits",   "big tit"),       # lowercase input
    ("facesitting","facesitting"),   # stays as-is (no 's' suffix to drop)
    ("lesbians",   "lesbian"),       # plain -s rule
    ("blowjob",    "blowjob"),       # no plural form
    ("ass",        "ass"),           # ends ss → not singularized
    ("class",      "class"),         # ends ss → not singularized
    ("milfs",      "milf"),          # -s rule on known acronym base

    ("",           TRASH),           # empty
])
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected
