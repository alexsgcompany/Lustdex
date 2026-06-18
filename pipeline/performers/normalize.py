"""normalize(name) → merge key, or '' (skip). Pure, unit-tested (spec 11 §P5).

The key is for matching only; the display name is kept separate (mirrors tags
D7). Leading gender/honorific prefixes are stripped so `Ts Izzy Wilde` and
`Izzy Wilde` collapse to the same key. No singularization — names are not plural.
"""

import re
import unicodedata

# Leading prefixes that mark gender/honorific, not identity. Stripped once,
# at the start of the name only (spec 11 §P5).
_PREFIX_RE = re.compile(
    r"^(?:ts|t-girl|tgirl|tgirls|tranny|shemale|ladyboy|trans)\b[\s._:'-]*"
)


def normalize(name: str) -> str:
    # NFKD ASCII fold (Renée → renee), lowercase, strip.
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    if not s:
        return ""

    # Strip one leading gender/honorific prefix.
    s = _PREFIX_RE.sub("", s)

    # Punctuation → space, drop anything non-alphanumeric, collapse whitespace.
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
