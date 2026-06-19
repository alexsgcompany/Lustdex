# specs/14-read-path-app-consumption.md — App-side adoption of the read-path rollups

Handoff TЗ for the **Worker / Astro site repo** (separate from this pipeline repo).
Implements spec 13 §7. The DB side (spec 13 + migration 016) is **done and live on
prod**; this doc is the app-side change that actually realizes the latency win. Until
this ships, the rollups sit unused and the site is exactly as slow as before.

Audience: whoever edits the site's data layer (`projection.ts`, the route loaders,
`search.tsx`, `suggest.tsx`).

---

## 0. What changed on the DB (already deployed to prod)

Migration 016 added three **derived** tables in schema `cat` + two trgm indexes.
They are populated from the catalogue (deterministic) and refreshed by the pipeline;
the app only **reads** them.

```sql
-- projection membership, pre-resolved (replaces the 2-EXISTS projectionWhere)
cat.projection_videos (projection_id int, video_id bigint, published_at timestamptz)
   PRIMARY KEY (projection_id, video_id)
   INDEX projection_videos_listing (projection_id, published_at DESC NULLS LAST, video_id DESC)

-- per-(projection, tag) video count (replaces COUNT(*) over the whole tag set)
cat.tag_counts (projection_id int, tag_id int, n int)   PK (projection_id, tag_id)

-- per-(projection, page) video count + emission gate (replaces request-time P11 recompute)
cat.page_counts (projection_id int, page_id bigint, n int)   PK (projection_id, page_id)
   -- only rows with n > 0 exist; absent row = below MIN_VIDEOS_PER_PAGE → 404/noindex

-- suggest typeahead (leading-wildcard ILIKE now indexed)
INDEX tags_name_trgm           ON cat.tags USING gin (name gin_trgm_ops)
INDEX page_candidates_slug_trgm ON cat.page_candidates USING gin ((COALESCE(slug_final, slug_provisional)) gin_trgm_ops)
```

`projection_id` = `cat.projections.id` (trans=1, milf=2, mix=3). The app already
resolves its projection from domain/slug; it now needs that **id** (cache it).

### Two rules that must not be broken

1. **Replace, do not stack.** Membership is baked into `projection_videos`. When a
   query joins `projection_videos`, do **not** also inject the old `projectionWhere()`
   (the 2 correlated EXISTS). Doing both is redundant double-filtering — correct result,
   zero benefit. The whole point is to stop recomputing membership.
2. **Rollups can lag.** They are refreshed periodically (after pipeline runs / prod
   refresh), not in real time. A video added/retagged since the last refresh appears
   after the next refresh. Acceptable for SEO listings; do not treat counts as
   transactionally exact.

---

## 1. `projectionWhere()` → join the rollup

**Before** (`projection.ts:38`): every query injected
`(vertical = ANY(...) OR EXISTS include) AND NOT EXISTS exclude` as a `WHERE` on
`cat.videos` — 2 correlated EXISTS per candidate row, the `allowed AS MATERIALIZED`
full-table scan duplicated across `all.tsx`, `server.ts`, `home-pool.ts`,
`suggest.tsx`, `tag/[slug].tsx`, `s/[slug].tsx`.

**After**: the projection filter becomes a join to `projection_videos`:

```sql
JOIN cat.projection_videos pv ON pv.video_id = v.id AND pv.projection_id = $pid
```

`projectionWhere()` should expose a helper that returns this join (and the ordering
columns) instead of the EXISTS snippet. Every route that filtered by projection now
drives off `projection_videos` for that projection.

---

## 2. Listing routes

### `/all` (`all.tsx`) — the heaviest cold route today (~5.5s)

```sql
SELECT v.<tile cols>
FROM cat.projection_videos pv
JOIN cat.videos v ON v.id = pv.video_id
WHERE pv.projection_id = $pid
ORDER BY pv.published_at DESC NULLS LAST, pv.video_id DESC
LIMIT 48;                                  -- keyset clause: see §5
```

This walks `projection_videos_listing` in order — no full `cat.videos` scan, no sort.
The total count for the footer = `SELECT count(*) FROM cat.projection_videos WHERE
projection_id = $pid` (single index-only count), or cache as today.

### `/tag/[slug]` — worst dynamic route today (2.7–4.2s)

- **Count** (was `COUNT(*)` full scan, ~187k-cost plan): replace with one lookup
  ```sql
  SELECT COALESCE((SELECT n FROM cat.tag_counts WHERE projection_id=$pid AND tag_id=$tid), 0);
  ```
- **Slice** (was full Sort of the vertical):
  ```sql
  SELECT v.<cols>
  FROM cat.projection_videos pv
  JOIN cat.video_tags vt ON vt.video_id = pv.video_id AND vt.tag_id = $tid
  JOIN cat.videos v ON v.id = pv.video_id
  WHERE pv.projection_id = $pid
  ORDER BY pv.published_at DESC NULLS LAST, pv.video_id DESC
  LIMIT 48;                                -- + keyset
  ```
  Planner note: this walks `projection_videos_listing` ordered and probes the tag per
  row. Fine for common (dense) tags. EXPLAIN a rare tag; if a sparse tag walks too far,
  the alternative is to start from `video_tags(tag_id)` and sort — but for the tags that
  actually have pages, the ordered walk wins. Verify, don't assume.
- **`relatedPages` chips** (`tag/[slug].tsx:55`, the heaviest add — full `allowed` CTE
  inside the hot batch): drive the chip list off `tag_counts` / `page_counts` for the
  projection instead of re-scanning. Drop the in-batch full projection scan.

### `/p/[slug]` (page candidates) — medium (1.7–2.1s)

- **Emission gate + count** (was request-time P11 recompute): one lookup
  ```sql
  SELECT n FROM cat.page_counts WHERE projection_id=$pid AND page_id=$page_id;
  ```
  No row (or `n < MIN_VIDEOS_PER_PAGE`) → respond 404 + `noindex`. Sitemap/internal
  links must apply the same gate (only emit pages present in `page_counts` for the
  projection) so the site never links to a 404.
- **Slice** — videos in the projection having ALL the page's `member_tag_ids` (`$M`):
  ```sql
  SELECT v.<cols>
  FROM cat.projection_videos pv
  JOIN cat.videos v ON v.id = pv.video_id
  WHERE pv.projection_id = $pid
    AND pv.video_id IN (
        SELECT vt.video_id FROM cat.video_tags vt
        WHERE vt.tag_id = ANY($M)
        GROUP BY vt.video_id
        HAVING count(DISTINCT vt.tag_id) = cardinality($M))
  ORDER BY pv.published_at DESC NULLS LAST, pv.video_id DESC
  LIMIT 48;                                -- + keyset
  ```

### `/s/[slug]` (semantic pages) — already fast (~1.0s)

Candidate set is the frozen `semantic_pages.video_ids[]` — already pre-resolved, so
little to gain. One cleanup: the projection filter currently re-derives membership over
that frozen set; swap it to `… AND EXISTS (SELECT 1 FROM cat.projection_videos pv WHERE
pv.video_id = v.id AND pv.projection_id = $pid)` for consistency. Optional, low priority.

### `/actor/[slug]` — already fast (~1.0s, small per-performer join)

Optional. If you want it consistent, intersect the performer's videos with
`projection_videos` for the projection; the win is marginal (small candidate set).

---

## 3. Search (`search.tsx`) — remove the duplicated HNSW pass

Current cost (`search.tsx:150-198`): the inner `topk` CTE runs an HNSW scan
(`ef_search=400`, `LIMIT topK` up to 2000), and the **count query (:185) re-runs the
entire vector scan a second time** just to `count(*)`. ~97% of computed candidates are
discarded and the expensive part is done twice.

Change:
- Run the HNSW `topk` CTE **once**. Apply the projection filter to its bounded result
  (now: `JOIN cat.projection_videos` on the candidate ids, per §1).
- **Total = the size of that single filtered candidate set** (it is already bounded by
  `topK`), not a second HNSW pass. Render the first 60. Delete the second vector scan.
- `dist < 0.40` + `topK 2000` still governs the candidate pool size; that is the relevance
  knob (the "huge result counts" complaint lives here) — tune `topK` / `dist` separately,
  but stop paying for it twice.

---

## 4. Suggest (`suggest.tsx`) — use the new trgm indexes

The two `MATERIALIZED` full-video-scan CTEs (`:39,63`) were projection membership recomputes
— replace with `projection_videos` joins per §1 (or drop if suggest is projection-global).

The leading-wildcard `ILIKE '%q%'` (`:51,73`) now has supporting GIN trgm indexes:
- tags: `WHERE name ILIKE '%' || $q || '%'` → uses `tags_name_trgm`.
- page_candidates: the index is on `COALESCE(slug_final, slug_provisional)`. To use it,
  the predicate **must match that expression exactly**:
  `WHERE COALESCE(slug_final, slug_provisional) ILIKE '%' || $q || '%'`.
  (If suggest currently ILIKEs a different column, either change it to this expression or
  tell us the real column — the index was built to match a slug-shaped target, per spec 13.)

Optional upgrade: switch to the pg_trgm similarity operator `%`/`<->` for ranked fuzzy
matches; the same GIN index serves it.

---

## 5. Pagination: OFFSET → keyset

Current: `LIMIT 48 OFFSET (p-1)*48` (`tag:46, p:89, s:81, actor:45`). Deep pages read and
discard `(p-1)*48` rows (`?p=100` discards 4752); crawlers walk deep, cost grows linearly.

Replace with keyset on the same columns the `projection_videos_listing` index is ordered by:

```sql
-- page 1: as shown above (no cursor)
-- page N: pass the last row's (published_at, video_id) as the cursor
... WHERE pv.projection_id = $pid
    AND (pv.published_at, pv.video_id) < ($cursor_pa, $cursor_id)
ORDER BY pv.published_at DESC NULLS LAST, pv.video_id DESC
LIMIT 48;
```

**NULL caveat:** the index is `published_at DESC NULLS LAST`, so rows with
`published_at IS NULL` sort at the very end. A naive `(published_at, video_id) < (...)`
tuple comparison does not handle the NULL tail correctly. Options: (a) ensure
`published_at` is non-null for listable videos (preferred — check the data), or (b) handle
the NULL tail as a separate keyset segment, or (c) order by `COALESCE(published_at,
'-infinity')` and build a matching expression index. Pick one and keep the index ordering
and the cursor comparison consistent. URLs can keep `?p=N` for humans but should carry the
cursor for the actual query (or move to `?after=` tokens).

---

## 6. Definition of done / how to verify

- Cold `/all`, `/tag/*`, `/p/*` drop from 2–5.5s toward the `/s` floor (~1s or below);
  EXPLAIN shows index scans on `projection_videos_listing` / lookups on `*_counts`, no
  `Seq Scan` on `cat.videos` + `Sort` of the whole vertical.
- `/tag` count is a single-row `tag_counts` lookup, not a `COUNT(*)` scan.
- `/p` emission is a `page_counts` lookup; sitemap and `/p` agree (no links to 404).
- Search issues **one** HNSW scan per request, not two.
- Suggest predicates hit the trgm indexes (EXPLAIN shows `Bitmap Index Scan` on
  `tags_name_trgm` / `page_candidates_slug_trgm`, not `Seq Scan`).
- Deep pages (`?p=50+`) no longer degrade with depth.

---

## 7. What did NOT change (so nothing breaks)

- Table/column names of `cat.videos`, `cat.video_tags`, `cat.tags`, `cat.page_candidates`,
  `cat.semantic_pages` — unchanged. The rollups are additive.
- The projection rule itself (spec 06 §4) is unchanged — it is now *precomputed* into
  `projection_videos` instead of injected. If you ever need the live rule (e.g. a preview
  before refresh), the old `projectionWhere()` still works; just don't combine the two.
- Connection setup (`db.ts`, Hyperdrive, `prepare=false`) — unchanged.

---

## 8. Notes / open items the app dev should know

- **Staleness window**: see §0 rule 2. If a route must reflect brand-new content instantly,
  it cannot rely on the rollup; flag it and we decide per-route.
- **projection_id**: trans=1, milf=2, mix=3 on prod today. Resolve via
  `cat.projections WHERE slug=$slug` (or domain) and cache; do not hardcode ids.
- **mix/milf domains are not launched yet** (TODO §"Deploy remaining two domains"); only
  the trans site (lustdts.com) is live. The rollups exist for all three projections, but
  app changes only matter for live domains until the others launch.
