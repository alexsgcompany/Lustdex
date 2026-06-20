# specs/06-projections.md — Projections (thematic-site subsetting)

Scope: define how the shared `cat.*` catalog is sliced into per-site subsets
("projections" — e.g. MILF site, Trans site). A site sees only its subset;
isolation is mandatory.

All decisions below are FINAL (approved by the owner).

---

## 1. Decisions (fixed)

| #   | Decision | Value |
|-----|----------|-------|
| P1  | Selection levels | (a) feed-pin by vertical, (b) canonical-tag include, (c) canonical-tag exclude |
| P2  | Combinator | `(video.vertical = ANY(from_verticals) OR include_tag match) AND NOT exclude_tag match` |
| P3  | Tag identifiers | canonical (`cat.tags.id`) only — never raw |
| P4  | Vertical attribution | denormalized to `cat.videos.vertical text` at promote-time, copied from `raw.feeds.niche` |
| P5  | Vertical semantics | `vertical` is a feed-origin signal, not ground-truth genre. Precise membership comes from tags. A trans-MILF video from a trans-pinned feed has `vertical='trans'` but still enters MILF projection via `include_tag_ids` (the OR in P2). Free-text vocabulary (no `cat.verticals` table in MVP). |
| P6  | Projection storage shape | dedicated columns on `cat.projections` (not JSONB) |
| P7  | Membership computation | ad-hoc WHERE injection (no materialized membership table in MVP); revisit if slow |
| P8  | Isolation contract | every public-site query MUST go through the projection WHERE helper; bypassing it is a bug, not a feature |
| P9  | Editing in MVP | SQL/migration only — admin UI for projections is a separate TODO (post-MVP) |
| P10 | Multiple-projection videos | allowed — a video can satisfy multiple projections (e.g. a trans-MILF video appears in both, if rules match) |
| P11 | Page emission under projection | a `page_candidates` row renders on a projection only if the count of videos-in-page that pass the projection WHERE is `> 0` (or above a minimum threshold — TBD). Empty/under-threshold → `404` + `noindex`. Computed at request time, no materialization in MVP. **Rationale:** the whole point of projections is thin-content avoidance; serving an empty `[shemale, blonde]` page on a MILF domain reintroduces exactly the SEO problem projections exist to fix. |

---

## 2. `cat.projections` schema

```sql
CREATE TABLE cat.projections (
    id              serial PRIMARY KEY,
    slug            text UNIQUE NOT NULL,         -- 'milf', 'trans'
    name            text NOT NULL,                -- 'MILF', 'Transgender'
    domain          text,                         -- public domain (nullable until known)
    from_verticals  text[] NOT NULL DEFAULT '{}', -- match cat.videos.vertical (values from raw.feeds.niche)
    include_tag_ids int[]  NOT NULL DEFAULT '{}', -- canonical tag ids; OR'd with from_verticals
    exclude_tag_ids int[]  NOT NULL DEFAULT '{}', -- canonical tag ids; AND NOT'd over the include result
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
```

Rules-of-thumb:
- Empty `from_verticals` AND empty `include_tag_ids` → projection matches NOTHING (safer default than "matches everything").
- Empty `exclude_tag_ids` → no exclusion.

---

## 3. `cat.videos.vertical` denormalization

`cat.videos` currently has only `provider_id`. To resolve a video's vertical we
would have to join through `raw.raw_videos → raw.feeds` — violates the
schema-split contract (public site MUST NOT read `raw.*`).

Fix: add a denormalized column.

```sql
ALTER TABLE cat.videos ADD COLUMN vertical text;
CREATE INDEX ON cat.videos (vertical);
```

Backfill in migration:
```sql
UPDATE cat.videos cv
SET vertical = f.niche
FROM raw.raw_videos rv
JOIN raw.feeds f ON f.id = rv.feed_id
WHERE rv.id = cv.id;
```

**Backfill depends on a promote-contract invariant: `cat.videos.id == raw.raw_videos.id`.**
This is enforced in `pipeline/promote/videos.py` (insert uses `rv.id` verbatim, no dedup or filtering)
and in migration 007's initial backfill (`INSERT ... SELECT rv.id ...`). The match-by-id is therefore
exact, not heuristic. If promote ever introduces dedup, filtering, or a synthetic id, this backfill
must be rewritten — likely via `(provider_id, external_id)` which is `UNIQUE` on both tables.

`pipeline/promote/videos.py` must set `vertical` on insert and refresh it on
conflict (vertical reflects current feed config, unlike `slug`/`go_token` which
are pinned).

---

## 4. Selection rule semantics (canonical SQL form)

For a projection P, a video V is included iff:

```sql
(
    V.vertical = ANY(P.from_verticals)
    OR EXISTS (
        SELECT 1 FROM cat.video_tags vt
        WHERE vt.video_id = V.id AND vt.tag_id = ANY(P.include_tag_ids)
    )
)
AND NOT EXISTS (
    SELECT 1 FROM cat.video_tags vt
    WHERE vt.video_id = V.id AND vt.tag_id = ANY(P.exclude_tag_ids)
)
```

Public-site queries get this injected as a `WHERE` clause on `cat.videos`. The
Astro site reads its projection config once at startup (or per request, cached)
and builds queries with the snippet above.

The DB-docs deliverable (TODO item #3 of MVP phase) documents this exact
pattern as the ONE canonical way to filter — no shortcuts.

---

## 5. Page-candidate scoping under projection (P11 in detail)

`cat.page_candidates` is global: each row is a `member_tag_ids int[]` combo. A
projection does NOT alter `page_candidates` rows — it filters them at render
time.

A page `P` (with `member_tag_ids = M`) is renderable on projection `Π` iff:

```sql
SELECT COUNT(*) FROM cat.videos v
WHERE v.id IN (
    -- videos that have ALL of the page's member tags
    SELECT vt.video_id FROM cat.video_tags vt
    WHERE vt.tag_id = ANY(M)
    GROUP BY vt.video_id
    HAVING COUNT(DISTINCT vt.tag_id) = cardinality(M)
)
AND (
    -- projection include rule
    v.vertical = ANY(Π.from_verticals)
    OR EXISTS (
        SELECT 1 FROM cat.video_tags vt2
        WHERE vt2.video_id = v.id AND vt2.tag_id = ANY(Π.include_tag_ids)
    )
)
AND NOT EXISTS (
    -- projection exclude rule
    SELECT 1 FROM cat.video_tags vt3
    WHERE vt3.video_id = v.id AND vt3.tag_id = ANY(Π.exclude_tag_ids)
) > 0;  -- (threshold MIN_VIDEOS_PER_PAGE; MVP: 1)
```

Site behavior:
- `count >= MIN_VIDEOS_PER_PAGE` (MVP: `1`, will likely raise later) → render normally.
- `count == 0` (or below threshold) → respond `404` and set `noindex` so the URL
  is not eligible for SEO from this projection's domain.
- Listing pages (sitemap, internal cross-links) must apply the same filter so
  the site never links to a `404`-eligible page.

MVP implementation: compute at request time. Acceptable cost given current
volumes (~11k videos, hundreds of page candidates). If page-render latency
degrades, revisit by introducing a `cat.projection_page_emission` materialized
table on cron.

---

## 6. Seed projections (MILF, Trans)

Done in the same migration. IDs below are picked from the live `cat.tags` table
(verified at spec time — re-confirm before running migration).

```sql
INSERT INTO cat.projections (slug, name, from_verticals, include_tag_ids, exclude_tag_ids)
VALUES
  ('trans', 'Transgender',
   ARRAY['trans'],                          -- raw.feeds.niche='trans' confirmed present
   ARRAY[179]::int[],                       -- shemale
   ARRAY[]::int[]),
  ('milf',  'MILF',
   ARRAY[]::text[],                         -- no MILF-pinned feed: only 'trans' and 'mix' exist
   ARRAY[81, 127, 80, 20]::int[],           -- milf, stepmom, mature, cougar
   ARRAY[]::int[]);                         -- DO NOT exclude trans/shemale here: it would drop
                                            -- legitimate trans-MILF content that legitimately
                                            -- belongs in both projections (P10)
```

Notes on the seed:
- Trans tag list is minimal (only `shemale` exists canonically — no `trans` /
  `tgirl` / `ladyboy` slugs in `cat.tags` yet). When the tag dictionary
  expands, add via `UPDATE`.
- MILF candidates `step-fantasy` (123) and other step-* roles deliberately
  omitted from initial seed — owner to decide whether `step-fantasy` belongs
  in MILF (it's broader than mother-figures). Easy to add later.
- `from_verticals` for MILF is empty because the current feed inventory has
  no MILF-pinned feed (`raw.feeds.niche` ∈ {`trans`, `mix`} only). MILF
  membership is 100% tag-driven from the `mix` pool.

**Update (migration 017):** MILF is now hetero-only. The original "DO NOT
exclude trans/shemale" choice above is reversed — `milf.exclude_tag_ids =
{179, 57}` (shemale, gay), matching the `mix` projection's hetero rule. A
trans-MILF video no longer appears on the MILF site (P10 still allows
multi-projection membership in general; this is a per-projection editorial
decision, not a rule change).

---

## 7. Migration 008 — what it does

1. `ALTER TABLE cat.videos ADD COLUMN vertical text;`
2. `CREATE INDEX ON cat.videos (vertical);`
3. Backfill `cat.videos.vertical` from `raw.raw_videos → raw.feeds.niche`
4. `CREATE TABLE cat.projections (...)`
5. Seed MILF + Trans projections (with real tag IDs picked at seed-time)

---

## 8. Code-side changes

| File | Change |
|------|--------|
| `pipeline/promote/videos.py` | set `vertical` on INSERT and refresh on conflict (unlike pinned fields) |
| `demo/app.py` | (optional) projection picker dropdown — preview how MILF/Trans subsets look |
| (Astro site, separate repo) | implement projection-aware query helper per Section 4 |

No changes to ingest, tags cascade, page-builder — projections are downstream
of all of those. Page candidates remain global; the site filters them at read
time via the projection WHERE.

---

## 9. Out of scope (future)

- **Admin UI** for editing projection rules + previewing affected video counts
  and affected page count under each projection.
- **`cat.verticals` lookup** if `raw.feeds.niche` free-text gets messy.
- **Materialized membership table** (`cat.projection_videos`) and/or
  **materialized page-emission table** (`cat.projection_page_emission`) — only
  if the request-time computation in §5 becomes a measured bottleneck.
- **Raised `MIN_VIDEOS_PER_PAGE` threshold** (§5 currently uses `1` for MVP;
  realistic SEO floor is higher — pick after observing live data).
- **Multi-vertical videos** — current model assumes one feed → one niche
  (P5); harmless because tags carry the precise membership signal anyway.
