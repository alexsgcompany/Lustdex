# specs/03-page-builder.md — Page builder: manual tag-combination pages

Scope: manual workflow for creating SEO landing-page candidates from tag combinations.
This is the INTERACTIVE version of the lexical filter; it lays the full toolset for a later auto mode
(same SQL, same slug generator, same table — auto mode just loops instead of clicking).

Serpstat integration (search volume, final word order) is OUT of scope here — separate spec.
This spec stops at: candidate exists, with lexical_count and a provisional slug.

## 0. Model decisions (fixed)

| # | Decision | Value |
|---|---|---|
| M1 | A page's base is a SET of tags, not one | `base = [milf]` or `base = [hot, milf]` — same structure |
| M2 | A candidate = full member set | `member_tag_ids int[]`: `[milf]`, `[hot,milf,asian]` — base+additions flattened |
| M3 | member_tag_ids is always sorted before save | canonical order → UNIQUE catches `milf+asian` == `asian+milf` |
| M4 | Slug at approve time is PROVISIONAL | deterministic order; final word order comes later from Serpstat |
| M5 | Additions list is ordered by co-occurrence count DESC | highest-content combos on top; you approve down until content thins out |
| M6 | Same table serves performer pages later | `kind` discriminator; performer pages are the same funnel, not a new pipeline |

## 1. Migration 005_page_candidates.sql

```sql
CREATE TABLE page_candidates (
    id               bigserial PRIMARY KEY,
    kind             text NOT NULL DEFAULT 'tags',   -- 'tags' | 'performer_tags' (future)
    member_tag_ids   int[] NOT NULL,                 -- ALL tags of the page, SORTED ASC
    lexical_count    int NOT NULL,                   -- videos having ALL member tags, at creation time
    slug_provisional text NOT NULL,                  -- deterministic, generated on approve
    slug_final       text,                           -- set later by Serpstat batch, nullable
    volume           int,                            -- set later by Serpstat, nullable
    status           text NOT NULL DEFAULT 'approved', -- 'approved' | 'published' | 'rejected'
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (member_tag_ids)
);
CREATE INDEX ON page_candidates USING gin (member_tag_ids);
```

UNIQUE on the sorted array is the dedup guarantee. GIN index supports "does a candidate with these members exist" lookups.

## 2. Co-occurrence query (the core, reused by auto mode)

Given a base set of tag_ids, return candidate ADDITION tags that pass the threshold, ranked.

For base `B` (one or more tag_ids):
- videos matching base = videos whose `video_tags` include ALL of B.
- for every OTHER canonical tag `t`, count videos in that base-set that ALSO have `t`.
- return tags with count >= THRESHOLD, ordered by count DESC.

THRESHOLD default = 20 (config constant, not hardcoded in multiple places). Tune later on real data.

This is plain SQL over `video_tags` + `tags`. No API. Must be a single reusable function
`pipeline/pages/cooccurrence.py: additions_for(conn, base_tag_ids) -> [(tag_id, name, category, count)]`
so the admin screen and the future auto loop call the same code.

## 3. Slug generation (provisional, M4)

Two functions in `pipeline/pages/slug.py`:

`load_tag_slugs(conn, tag_ids) -> dict[int, str]` — fetches `{tag_id: slug}` for the given ids.

`provisional_slug(tag_slugs, ordered_tag_ids) -> str` — joins slugs in the given order with `-`.

**Order rule (decided):** base tags in the order the user selected them, then the addition tag appended.
Example: user selects `asian → milf`, addition `anal` → `asian-milf-anal`.
This is predictable and matches user intent; freq-based ordering was considered but rejected.

The admin screen pre-fills the slug field with this value and lets the user edit it before approving.
The edited value (not the auto-generated one) is what gets stored in `slug_provisional`.

## 4. Admin screen — Page Builder (Streamlit, Pages section)

New screen under a "Pages" section (sibling to Tags section).

Flow:
1. **Select base**: multiselect over canonical tags (1+). `[milf]` or `[hot, milf]`.
2. On selection → call `additions_for(base)` → show table of addition candidates:
   columns: tag name · category · **co-occurrence count** · [approve checkbox].
   Sorted count DESC (M5). This count is the primary signal — show it prominently.
3. For each addition the user checks and approves:
   - build `member_tag_ids = sorted(base + [addition])`.
   - compute `lexical_count` (the count already shown).
   - compute `slug_provisional`.
   - insert into `page_candidates` (status 'approved').
4. **Already-existing handling (critical UX, M3):** if `member_tag_ids` already exists in `page_candidates`,
   the row must render as **already approved** (badge + disabled checkbox), NOT silently vanish.
   Otherwise the user is confused why some combos "don't approve".
   This applies across bases: after approving `milf+asian`, selecting base `asian` must show `milf` as already-done.

Approve granularity: the user may check MULTIPLE additions and approve them in one click
(each becomes its own candidate row). Base is a set; additions are checked individually.

Editable slug: each row has a `text_input` pre-filled with the provisional slug. User can edit before approving. The stored `slug_provisional` is whatever the field contains at approve time, not the auto-generated default.

## 5. Files

```
pipeline/pages/cooccurrence.py       — additions_for(conn, base_tag_ids), THRESHOLD constant
pipeline/pages/slug.py               — load_tag_slugs(conn, ids), provisional_slug(tag_slugs, ordered_ids)
admin/page_screens/builder.py        — the Streamlit screen
admin/page_screens/queries.py        — SQL for existence checks, inserts
```

Note: `admin/page_screens/` not `admin/pages/` — Streamlit auto-discovers a `pages/` directory next to the entry script and registers every `.py` file there as a standalone page, breaking the shared `sys.path` setup in `app.py`.

No new Python dependency. Migration 005.

## 6. Definition of Done

- Migration 005 applies; re-run is a no-op.
- Selecting base `[milf]` lists additions ranked by real co-occurrence count from video_tags.
- Approving an addition creates exactly one `page_candidates` row with sorted member_tag_ids, correct lexical_count, provisional slug.
- Approving `[milf]+asian` then selecting base `[asian]` shows `milf` as already-approved (not absent, not duplicable).
- Multi-base works: `[hot, milf]` as base produces members like `[asian, hot, milf]` sorted.
- UNIQUE prevents any duplicate member set regardless of click order.
- `additions_for` and `provisional_slug` are standalone functions callable without Streamlit (auto-mode ready).

## 7. Explicitly out of scope (later specs)

- Serpstat: search volume, slug_final word-order decision.
- Auto mode: looping additions_for across all bases without manual clicks.
- Triples beyond manual approval; performer_tags kind.
- Page rendering (SSR from Postgres).
- Status beyond 'approved' (publish workflow).