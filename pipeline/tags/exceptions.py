"""Static data for tag normalization: stoplist and exception dictionary."""

# Full-string match after step 4.
# Criterion: word appears on a large share of all videos AND intersection with it does not
# narrow the selection (e.g. "milf sex" ≈ "milf"). Full-string match only — "rough sex",
# "group sex" are NOT affected.
# Modifier tags (hot, sexy, beauty, naked, wild …) are NOT here — they live in the canonical
# tags table with category='modifier'. Standalone-page restriction is the pages module's job.
STOPLIST: frozenset[str] = frozenset({
    "sex",
    "porn",
})

# Full-string match applied at step 6. Keys are post-step-3 strings (lowercased, separators
# replaced, + still present where applicable). Values are the replacement key.
EXCEPTIONS: dict[str, str] = {
    # age markers (all-marker tokens end up here after step 4 keeps them)
    "18":   "teen",
    "18+":  "teen",
    "18yo": "teen",
    # numeric protected forms
    "69":   "69",
    # digit-letter fusions
    "3some": "threesome",
    # irregular plurals confirmed in feed data
    "feet":  "foot",
    "wives": "wife",
    # nouns where singularize would mangle (glasses → glass via sses-rule)
    "glasses": "glasses",
}
