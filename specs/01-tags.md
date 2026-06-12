# specs/01-tags.md — Tag normalization: normalize() + cascade L1–L2

Scope: collecting raw tags, the `normalize()` function, cascade levels 1–2 (exact + fuzzy),
review queue semantics, migration 002. Levels 3 (embeddings) and 4 (LLM) — separate spec later.

All decisions below are FINAL (approved by the owner). Do not re-decide them in code.

---

## 1. Decisions (fixed)

| # | Decision | Value |
|---|---|---|
| D1 | Canonical slug number form | **plural** (`big-tits`, not `big-tit`) — matches search demand and URLs |
| D2 | Age markers (`18`, `18+`, `18yo`) | **stripped** when other tokens remain; tag consisting ONLY of a marker maps to canonical `teen` via exception dict |
| D3 | Other digits (`69`, `3some`) | NOT stripped; handled by exception dictionary only |
| D4 | Word order in multi-word tags | NOT sorted at L1; word-order variants are L2 fuzzy's job |
| D5 | Fuzzy thresholds | score ≥ 94 → auto-map; 86–93.99 → suggestion in review queue; < 86 → stays pending for L3/L4 |
| D6 | Non-English | non-Latin script → `trash` immediately; Latin-script non-English (Spanish etc.) gets no special handling — falls into review queue naturally |
| D8 | Generic modifier tags (`hot`, `beauty`, `sexy`, ...) | NOT trash — canonical tags with `category='modifier'`: no standalone landing page, but valid for intersections (`hot milf`) and search. Only true noise (`sex`, `porn` — present on everything, zero filtering signal) goes to STOPLIST |
| D7 | Lemmatization | rule-based last-token singularization for the **matching key only**; no spaCy/NLTK dependency |

Note on D1+D7: the normalized KEY is singular (deterministic matching), the canonical SLUG is plural (display/URL). These are different layers; do not conflate them.

Steps are numbered; ORDER IS LOAD-BEARING (e.g. parens must be stripped before age-marker detection, stoplist before singularization).

## 2. normalize(raw: str) -> str | TRASH

Pure function, no DB access, fully covered by tests. Steps in exact order:

1. Unicode NFKC normalize, lowercase, strip.
2. **Trash check:** if the string contains any non-Latin letters (Cyrillic, CJK, Arabic, ...) → return TRASH sentinel. If after step 3 no `[a-z]` remains → TRASH.
3. Replace separators `_ - . / , + &` with space; drop remaining punctuation; collapse whitespace.
   (`+` is replaced AFTER step 4's marker detection — see implementation note below.)
4. **Age-marker stripping (D2):** remove tokens matching `18`, `18+`, `18yo`, `18 yo` if at least one other token remains. `teen 18` → `teen`, `schoolgirl 18+` → `schoolgirl`.
5. **Stoplist (true noise only, D8):** full-string match against `STOPLIST` in `exceptions.py` → TRASH.
   Seed: `sex`, `porn`. Criterion for adding a word here: it appears on a large share of all videos and an intersection with it does not narrow the selection (`milf sex` ≈ `milf`). Full-string match only — `rough sex`, `group sex` are NOT affected.
   Generic modifiers (`hot`, `beauty`, `sexy`, `naked`, `wild`, `sweet`, `crazy`, `perfect`) are NOT stoplisted — they become canonical tags with `category='modifier'` (seeded via dictionary, resolved via L1): valid for intersections and search, excluded from standalone page generation (enforced later by the pages module, not here).
6. **Exception dictionary (full-string lookup, D2/D3):** exact match on the current string → replace.
   Seed entries (extend during review, stored in code as a dict in `pipeline/tags/exceptions.py`):
   - `18` / `18+` / `18yo` → `teen`
   - `3some` → `threesome`
   - `69` → `69` (identity = protected from further transforms)
   - irregular plurals (confirmed in real feeds): `feet` → `foot`, `wives` → `wife`
7. **Singularize last token (D7),** rule-based:
   - ends `ies` (len>4) → `y` (`pussies` → `pussy`)
   - ends `sses`/`shes`/`ches`/`xes`/`zes` → drop `es`
   - ends `s` and not `ss` → drop `s` (`tits` → `tit`)
8. Join tokens with single space → normalized key.

Examples (must be test cases; right column = expected key):

| raw | key | note |
|---|---|---|
| `Big_Tits` | `big tit` | separators + case + singular |
| `big boobs` | `big boob` | stays distinct — synonym is L2/L3's job |
| `Teen (18+)` | `teen` | parens die in step 3, marker in step 4 — ORDER MATTERS |
| `Teens 18+` | `teen` | marker strip + singularize |
| `School (18+)` | `school` | |
| `18yo` | `teen` | marker-only → exception dict |
| `3some` | `threesome` | exception dict |
| `69` | `69` | protected identity |
| `Doggy style` / `Doggy Style` | `doggy style` | case collapse |
| `Face Sitting` | `face sitting` | NOT merged with `facesitting` here — that's L2 |
| `RedHead` | `redhead` | |
| `Wives` | `wife` | irregular plural |
| `Feet` | `foot` | irregular plural |
| `Pussies` | `pussy` | ies-rule |
| `BDSM` / `POV` / `MILF` / `BBC` | unchanged lowercase | acronyms survive singularization |
| `Sex` / `porn` | TRASH | stoplist (true noise) |
| `Hot` / `Beauty` | `hot` / `beauty` | pass through → resolve to `modifier` tags via L1 |
| `日本人` | TRASH | non-Latin |
| `!!!` | TRASH | no letters left |
| `transexuales` | `transexuale` | singularize mangles non-English — acceptable, lands in review (D6) |

## 3. Data flow

> Note for ingest (out of scope here, but discovered in feed data): localized tags (`transen`, `transexuales`, `transsexuel`) suggest at least one provider sends language-dependent tags. When writing that provider's parser, check the feed API for a `lang`/`language` parameter and pin it to English — kills the problem at the source.

### 3.1 Collect — `python -m pipeline.tags.collect`
Scan `raw_videos.tags_raw`, for every raw tag compute `normalize()`:
- TRASH → upsert into `unmapped_tags` with `status='trash'` (kept for stats, never reviewed).
- Otherwise upsert into `unmapped_tags`: increment `freq` by occurrence count, append raw spelling to `raw_examples` (cap 5, distinct), merge provider id into `providers`.
- Keys already present in `tag_aliases` are counted into stats but NOT inserted (already solved).
Idempotent: full recount per run (truncate-and-rebuild of freq is acceptable and simplest — freq is derived data).

### 3.2 Cascade — `python -m pipeline.tags.cascade`
Processes `unmapped_tags` with `status='pending'`, in `freq` DESC order.

**L1 — exact (source `rule`, confidence 1.0):**
normalized key == normalize(canonical tag name) for some tag in `tags` → write `tag_aliases`, mark row `resolved`.

**L2 — fuzzy (rapidfuzz, `token_sort_ratio`):**
Compare the key against: all canonical names (normalized) + all existing alias keys (an alias hit maps to that alias's tag_id).
- best score ≥ 94 → write `tag_aliases` (source `fuzzy`, confidence = score/100), mark `resolved`.
- 86 ≤ score < 94 → fill `suggested_tag_id`, `suggested_source='fuzzy'`, `suggested_confidence`; status stays `pending`.
- score < 86 → leave untouched (waits for L3/L4).

Guard: candidate set for auto-map excludes pairs where one side is a single token of length ≤ 4 and the other differs in first letter (cheap protection against `teen`/`ten`-class collisions). Such pairs go to suggestions regardless of score.

### 3.3 Review resolution semantics (consumed by admin/CLI later)
- **Accept suggestion** → `tag_aliases(normalized, suggested_tag_id, source=suggested_source, confidence=suggested_confidence)`, status `resolved`.
- **Map to other tag** → alias with `source='manual'`, confidence 1.0.
- **New canonical** → insert into `tags` (slug = plural form, owner-provided), then alias `source='manual'`.
- **Trash** → status `trash`.

## 4. Migration `002_tags_pipeline.sql`

```sql
ALTER TABLE unmapped_tags
    ADD COLUMN suggested_tag_id      int REFERENCES tags(id),
    ADD COLUMN suggested_source      text,
    ADD COLUMN suggested_confidence  real,
    ADD COLUMN providers             int[] NOT NULL DEFAULT '{}';

CREATE TABLE pipeline_runs (
    id          bigserial PRIMARY KEY,
    job         text NOT NULL,                -- 'tags.collect', 'tags.cascade', 'ingest.<provider>'
    status      text NOT NULL,                -- 'running' | 'done' | 'failed'
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    stats       jsonb,
    error       text
);
```

## 5. Run logging

Every CLI entry point wraps its work:
insert `pipeline_runs` row (`running`) → work → update to `done` + `stats` jsonb, or `failed` + `error` (first 2000 chars of traceback).
Stats keys for this module: `collect`: `{raw_tags_seen, unique_keys, new_pending, trash}` · `cascade`: `{processed, auto_rule, auto_fuzzy, suggested, untouched}`.

## 6. Files

```
pipeline/tags/normalize.py     — normalize(), TRASH sentinel; pure, no DB
pipeline/tags/exceptions.py    — exception dict (data, not logic)
pipeline/tags/collect.py       — CLI: §3.1
pipeline/tags/cascade.py       — CLI: §3.2 (L1+L2)
pipeline/tags/seed_dictionary.py — one-off CLI: dump top-N keys by freq to stdout/CSV
                                   for building the initial canonical dictionary (no LLM here)
tests/test_normalize.py        — all examples from §2 + edge cases
```

New dependency (approved): `rapidfuzz`.

## 7. Definition of Done

- `pytest tests/test_normalize.py` green; every example from §2 present.
- `python -m pipeline.tags.collect` on real ingested feed data fills `unmapped_tags`; second run does not inflate `freq`.
- `python -m pipeline.tags.cascade` twice in a row: second run reports `processed` only for rows still pending; no duplicate aliases (PK on `normalized` guarantees this).
- Both jobs visible in `pipeline_runs` with sane stats.
- No canonical tags are auto-invented anywhere: `tags` rows appear only via seed script output approved by owner, or via review "New canonical".