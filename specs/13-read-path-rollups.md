# specs/13-read-path-rollups.md — Read-path pre-resolution (projection rollups)

Scope: kill the cold-cache latency on public-site read routes by **pre-resolving
projection membership and per-page/per-tag counts** into rollup tables, refreshed
once per pipeline run. This activates the materialization explicitly deferred in
`specs/06-projections.md` §9 / P7 ("ad-hoc WHERE injection ... revisit if slow")
now that the slowness is measured.

This spec covers the **DB side only** — the data we hand to sites. App-side
(Worker) rewrites to consume these rollups are a separate, later iteration
(see §7).

---

## 1. Why — measured root cause

Cold cost ≈ candidate-set size × (projection re-scan? yes/no). Every cold request
re-computes projection membership from scratch (2 correlated EXISTS over the whole
vertical) and re-counts the full matching set. Nothing is pre-resolved on the
Postgres side. Measured on the local build DB (same schema as prod; trans data
present — plans transfer 1:1):

| Route pattern | Plan (EXPLAIN, estimate) | Cost |
|---|---|---|
| `/tag` count (`vertical='trans'` + tag EXISTS) | `Seq Scan videos[242k] + Hash Join Bitmap video_tags[172k]` — touches every matching row | **187,895** |
| `/tag` slice (LIMIT 48 OFFSET 240) | `Seq Scan videos[242k] → Sort[90,454] → Nested Loop` — sorts the **whole vertical** by `published_at` (no index), then tag-probes | **92,380** to first 48 |

Confirmed structurally: **0 matviews, 0 rollup tables**. `pg_trgm` extension is
installed but **no trgm index exists** on `cat.tags.name` / `cat.page_candidates.name`,
so suggest still leading-wildcard seq-scans. `cat.video_tags` is already well
indexed (`pkey(video_id,tag_id)` + `tag_id_idx`); the cost is the missing
pre-resolution, not a missing per-tag index.

---

## 2. Decisions (fixed — approved by owner)

| #   | Decision | Value |
|-----|----------|-------|
| R1  | Rollup mechanism | **Plain tables, rebuilt via shadow-table + atomic swap**, not `MATERIALIZED VIEW`. Each refresh loads into a side table (`*_new`) the live site does NOT read, builds its index on that shadow, then `DROP old; ALTER … RENAME` in one transaction. Reason: the live table (esp. on prod) is never empty, never index-less, never mid-load while the site reads it; load-then-index is also faster than maintaining an index during a bulk insert. Plain-table choice (over `MATERIALIZED VIEW`) fits build-local→deploy-prod data-sync; no `CONCURRENTLY`/unique-index constraints. **Never `TRUNCATE` the live table.** |
| R2  | What gets pre-resolved | (a) projection→video membership, (b) per-(projection,tag) counts, (c) per-(projection,page) counts |
| R3  | Membership rule source | `cat.projections` columns, verbatim per spec 06 §4 (the OR of `from_verticals`/`include_tag_ids`, AND-NOT `exclude_tag_ids`). The rollup MUST stay byte-identical to the app projection helper. |
| R4  | Denormalize `published_at` into the membership rollup | yes — enables an ordered `(projection_id, published_at DESC, video_id)` index so the listing slice is an index walk, not a full sort. Keyset-ready. |
| R5  | Refresh trigger | a new deterministic pipeline step `python -m pipeline.projections.refresh`, run after `promote.videos` + tags cascade, before deploy. One script = one job. |
| R6  | Counts threshold semantics | `page_counts` only stores rows where `n > 0`. A page absent from the table = below `MIN_VIDEOS_PER_PAGE` (MVP 1) → site serves 404 + noindex (spec 06 §5 / P11). |
| R7  | suggest indexes | add GIN `gin_trgm_ops` on `cat.tags.name` and `cat.page_candidates.name`. |
| R8  | New module dir | `pipeline/projections/` is approved (outside the original §5 structure in CLAUDE.md, explicitly OK'd for this work). |

Not in this spec (app-side, later — see §7): keyset pagination, search double-HNSW
removal, rewriting the app projection helper to read these rollups.

---

## 3. Schema (migration 016)

```sql
-- 3.1 projection membership rollup
CREATE TABLE cat.projection_videos (
    projection_id int    NOT NULL REFERENCES cat.projections(id) ON DELETE CASCADE,
    video_id      bigint NOT NULL REFERENCES cat.videos(id)      ON DELETE CASCADE,
    published_at  timestamptz,                  -- denormalized from cat.videos (R4)
    PRIMARY KEY (projection_id, video_id)
);
-- ordered index = the fix for the listing slice (no more full Sort; keyset-ready)
CREATE INDEX projection_videos_listing
    ON cat.projection_videos (projection_id, published_at DESC NULLS LAST, video_id DESC);

-- 3.2 per-(projection,tag) counts  — drives /tag count + tag-facet chips
CREATE TABLE cat.tag_counts (
    projection_id int    NOT NULL REFERENCES cat.projections(id) ON DELETE CASCADE,
    tag_id        int    NOT NULL REFERENCES cat.tags(id)        ON DELETE CASCADE,
    n             int    NOT NULL,
    PRIMARY KEY (projection_id, tag_id)
);

-- 3.3 per-(projection,page) counts — drives /p count + P11 emission gate
CREATE TABLE cat.page_counts (
    projection_id int    NOT NULL REFERENCES cat.projections(id)        ON DELETE CASCADE,
    page_id       int    NOT NULL REFERENCES cat.page_candidates(id)    ON DELETE CASCADE,
    n             int    NOT NULL,                -- only rows with n > 0 are stored (R6)
    PRIMARY KEY (projection_id, page_id)
);

-- 3.4 suggest indexes (R7)
CREATE INDEX tags_name_trgm
    ON cat.tags USING gin (name gin_trgm_ops);
CREATE INDEX page_candidates_name_trgm
    ON cat.page_candidates USING gin (name gin_trgm_ops);
```

Migration creates **empty** tables on both local and prod. Population is the
pipeline's job (§5); data-sync ships the populated rows local→prod. The trgm
indexes are pure adds (no data dep), safe in the migration.

---

## 4. Refresh logic (shadow-table + atomic swap, per R1)

Each rollup is rebuilt by loading a shadow table, indexing it, then atomically
renaming it over the live one. The live table is never `TRUNCATE`d, never
index-less, never partially loaded while the site reads it. Idempotent: re-running
produces an identical table and the swap is all-or-nothing.

Pattern (shown for `projection_videos`; `tag_counts` / `page_counts` identical):

```sql
DROP TABLE IF EXISTS cat.projection_videos_new;
CREATE TABLE cat.projection_videos_new (LIKE cat.projection_videos INCLUDING DEFAULTS);

INSERT INTO cat.projection_videos_new (projection_id, video_id, published_at)
SELECT ... ;                                   -- the 4.1 query below

-- build index on the shadow AFTER load (faster than maintaining it during insert)
CREATE INDEX projection_videos_new_listing
    ON cat.projection_videos_new (projection_id, published_at DESC NULLS LAST, video_id DESC);
ALTER TABLE cat.projection_videos_new ADD PRIMARY KEY (projection_id, video_id);

BEGIN;                                          -- atomic publish: data + index together
DROP TABLE cat.projection_videos;
ALTER TABLE cat.projection_videos_new RENAME TO projection_videos;
ALTER INDEX projection_videos_new_listing RENAME TO projection_videos_listing;
COMMIT;
```

Note `LIKE … INCLUDING DEFAULTS` (not `INCLUDING ALL`) so the shadow loads
**without** the index/PK, then they are built post-load and renamed into place on
swap. FK constraints are re-declared on the shadow or omitted for the rollup
(membership integrity is guaranteed by the rebuild SELECT, not by FKs — decide in
migration; FKs on a swapped table need re-creation each cycle, a cost to weigh).

The three rebuild SELECTs:

```sql
-- 4.1 membership — byte-identical to spec 06 §4 (the OR include + AND-NOT exclude)
INSERT INTO cat.projection_videos_new (projection_id, video_id, published_at)
SELECT p.id, v.id, v.published_at
FROM cat.projections p
JOIN cat.videos v ON (
        v.vertical = ANY(p.from_verticals)
     OR EXISTS (SELECT 1 FROM cat.video_tags vt
                WHERE vt.video_id = v.id AND vt.tag_id = ANY(p.include_tag_ids))
)
WHERE p.active
  AND NOT EXISTS (SELECT 1 FROM cat.video_tags vt
                  WHERE vt.video_id = v.id AND vt.tag_id = ANY(p.exclude_tag_ids));

-- 4.2 tag counts — over the already-swapped fresh membership (read live projection_videos)
INSERT INTO cat.tag_counts_new (projection_id, tag_id, n)
SELECT pv.projection_id, vt.tag_id, count(*)
FROM cat.projection_videos pv
JOIN cat.video_tags vt ON vt.video_id = pv.video_id
GROUP BY pv.projection_id, vt.tag_id;

-- 4.3 page counts — videos in projection having ALL of a page's member tags (P11)
WITH page_video AS (
    SELECT pc.id AS page_id, vt.video_id
    FROM cat.page_candidates pc
    JOIN cat.video_tags vt ON vt.tag_id = ANY(pc.member_tag_ids)
    GROUP BY pc.id, vt.video_id
    HAVING count(DISTINCT vt.tag_id) = cardinality(pc.member_tag_ids)
)
INSERT INTO cat.page_counts_new (projection_id, page_id, n)
SELECT pv.projection_id, pvd.page_id, count(*)
FROM page_video pvd
JOIN cat.projection_videos pv ON pv.video_id = pvd.video_id
GROUP BY pv.projection_id, pvd.page_id;     -- n>0 only, by construction (R6)
```

Correctness anchor: 4.1 is a literal transcription of spec 06 §4. If the app
helper and 4.1 ever diverge, that is the bug — they are the same rule in two
places (until the app switches to reading 4.1's output, §7).

---

## 5. Pipeline step — `pipeline/projections/refresh.py`

One job: rebuild all three rollups in dependency order, each via the shadow+swap
of §4, progress to stdout, run via `python -m pipeline.projections.refresh`.
Strict order — **membership must swap before the counts read it**:

1. build `projection_videos_new` → index → swap → live `projection_videos` is fresh.
2. build `tag_counts_new` and `page_counts_new` (both read the now-fresh live
   `projection_videos`) → index → swap each.

State lives in the DB; re-running is safe and produces identical output. No flags
needed for MVP (rebuilds all active projections).

Ordering in the run: after `promote.videos` and the tags cascade (membership
depends on `cat.videos.vertical` + `cat.video_tags`), before deploy/data-sync.

Build-local→deploy-prod: refresh runs on the local build DB. data-sync ships the
result to prod using the **same shadow+swap on the prod side** — load into
`*_new` on prod, index, atomic rename — so the live prod tables the site reads are
never `TRUNCATE`d, emptied, or left index-less mid-sync. The very first prod load
is the only exception: the tables are freshly created by migration 016 and empty
(no live reads yet), so a direct COPY into them is fine. Never computed directly
on prod.

---

## 6. Expected effect

| Route | Before | After |
|---|---|---|
| `/tag` count | full Seq+Hash scan (cost 187k) every cold hit | single indexed lookup `tag_counts(projection_id, tag_id)` |
| `/tag` / `/all` slice | Seq+Sort of whole vertical (cost 92k) | index walk on `projection_videos_listing`, keyset-ready |
| `/p` emission + count | request-time P11 recompute | `page_counts` lookup; absent row = 404/noindex |
| suggest | leading-wildcard seq-scan per keystroke | trgm GIN index |

Independent of D1/CDN: this fixes the **origin cold cost** that every cache miss
pays. Pairs with the existing `/s` (frozen `video_ids[]`) lesson — same
pre-resolution principle, now applied to the dynamic projection routes.

---

## 7. Out of scope (app-side, next iteration)

- Rewrite the Worker projection helper to **read `projection_videos`** (join the
  rollup) instead of injecting the 2-EXISTS WHERE — this is what actually realizes
  §6 on the live site.
- Swap OFFSET → **keyset** pagination using `(published_at, video_id)` (the
  `projection_videos_listing` index is built for it).
- Search: drop the **second HNSW pass** done only to `count(*)`.
- Move `tag_counts` / `page_counts` reads into the route batches; retire the
  per-page `count(*)` and the request-time P11 recompute.

These need the rollups to exist first (this spec), then a coordinated Worker
deploy. Kept separate so the DB artifacts can land and be verified independently.

---

## 8. Open questions

- **Refresh cost at mix scale.** mix membership (892k videos, exclude-only rule)
  is the heaviest 4.1 branch. Measure refresh wall-time after first build; if it
  dominates the pipeline run, consider per-projection incremental refresh (out of
  scope for MVP — full rebuild is simplest and correct).
- **`MIN_VIDEOS_PER_PAGE`** stays at MVP 1 (R6). Raising it is a one-line change
  in 4.3 (`HAVING … AND count(*) >= N` via an outer filter) when SEO data says so.
