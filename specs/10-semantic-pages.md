# specs/10-semantic-pages.md — Semantic landing pages (vector-driven)

Scope: a second type of SEO landing page, complementing `cat.page_candidates`
(spec 03). Source of truth is a stored search query (operator-supplied or
analytics-derived) plus a frozen, periodically-refreshed snapshot of the top-N
similar videos pulled from `cat.video_embeddings` (spec 08). Renders through
the same site contract as spec 07, but with its own URL prefix and storage.

All decisions below are FINAL unless explicitly marked as **OPEN**.

---

## 1. Why a separate table from `cat.page_candidates`

| Aspect | `cat.page_candidates` | `cat.semantic_pages` |
|--------|-----------------------|----------------------|
| Input  | Set of tag ids (`member_tag_ids int[]`) | Free-form query text |
| Retrieval | SQL join on `cat.video_tags` (HAVING COUNT) | Frozen video-id snapshot from vector top-K |
| Lifecycle | Manually curated; rarely changes | Periodically refreshed against current embeddings |
| Editorial signal | `status='approved'` once | `status` + `refreshed_at` + `embedding_model` (stale signal) |
| Projection scope | Brand-tag filter (spec 07 §4.1) | Render-time projection filter on `cat.videos` |

Forcing both into one table would mean nullable `member_tag_ids` *and* nullable
`video_ids`, plus a `kind` discriminator — different retrieval, different
lifecycle. Cost of a second table is the right cost.

---

## 2. Decisions (fixed)

| #   | Decision | Value |
|-----|----------|-------|
| S1  | Storage model | Frozen snapshot of video ids, refreshable on demand. Trades freshness for determinism, SEO stability, and immunity to encoder swaps. Refresh is manual in MVP (S6). |
| S2  | `TOP_K` (snapshot pull) | `1000`. Bigger pool buys headroom for projection filtering + future page-depth without re-running search. |
| S3  | `MAX_DIST` (snapshot cutoff) | `0.40` cosine distance (= similarity > 0.6). Matches spec 09 §V6 — same model, same expected quality cliff. |
| S4  | `MIN_VIDEOS` (render gate) | `60`. After projection filter on `cat.videos`, fewer than 60 surviving items → `404` + `noindex`. Mirrors spec 06 §P11 thin-content avoidance. |
| S5  | Encoder | `BAAI/bge-small-en-v1.5` via `pipeline.embeddings` (same model as spec 08 §E1). Query-side prefix `"Represent this sentence for searching relevant passages: " + q` (spec 09 §V3). |
| S6  | Refresh strategy | Manual CLI in MVP. No cron, no worker. Operator runs the script after a batch of edits or model swap. Auto-refresh worker is Post-MVP (§10). |
| S7  | Stale detection | Row is stale when `embedding_model != current` OR `refreshed_at < threshold`. CLI exposes `--stale-only` to refresh just stale rows. No automatic action — operator triggers. |
| S8  | Projection scope | NOT stored on the row. Render-time `PROJ_WHERE` (spec 07 §4) filters `cat.videos` after expanding `video_ids`. One row serves every projection that has matching content. |
| S9  | Sources | `source` scalar enum: `operator` / `gsc` / `site_search` / `serpstat`. One source per row (the dominant one); no separate `cat.semantic_page_sources` join table in MVP. Multiple inputs converging on the same query are deduped by `UNIQUE(query_text)`. |
| S10 | Aliases | `aliases text[]` — SEO synonyms / morphological variants rendered as `<h2>` / sub-headings / related-search block. Aliases are NOT separately embedded; they're text decoration around the same vector. |
| S11 | URL shape | `/s/:slug` on the site. Distinct prefix from `/p/:slug` (page_candidates) so the two never collide. |
| S12 | Slug derivation | `slug_provisional = kebab(query_text)`. `slug_final` overrides if set. Same pattern as page_candidates. |
| S13 | Snapshot ordering | `video_ids` stored ORDER BY distance ASC. Render order = `array_position(video_ids, v.id)` so SEO and user perception of "most relevant first" survive projection filtering. |
| S14 | Pagination | `LIMIT/OFFSET` on the surviving (projection-filtered) ordered set. `PER_PAGE=60`. Snapshot caps natural depth at `TOP_K / PER_PAGE ≈ 16 pages`. |
| S15 | Editorial status | `status` enum: `draft` / `approved` / `rejected`. Only `approved` rows render on the site. Newly-created rows default to `draft`. |
| S16 | No site-search ingestion | `/search` (spec 09) does NOT auto-create `semantic_pages` rows. Operator aggregates GSC/Serpstat/site-search data externally and inserts deliberately. |
| S17 | Spec 07 compliance | C3 (cat-only schema), C5 (PROJ_WHERE), C7 (CDN composition), C8 (no `target_url` leakage; use `/go/:token`) all apply unchanged. |
| S18 | Forward-compat with model swap | `embedding_model` column captures which encoder produced the snapshot. CLI compares against current default; mismatched rows are flagged stale and re-snapshot when refresh runs against them. |

---

## 3. Schema (migration 013)

```sql
CREATE TABLE cat.semantic_pages (
    id               bigserial PRIMARY KEY,

    -- input
    query_text       text NOT NULL,
    aliases          text[] NOT NULL DEFAULT '{}',
    source           text NOT NULL
        CHECK (source IN ('operator','gsc','site_search','serpstat')),
    source_volume    int,

    -- routing
    slug_provisional text NOT NULL,
    slug_final       text,

    -- frozen result
    video_ids        bigint[] NOT NULL,    -- ORDER BY dist ASC
    top_k            int  NOT NULL,        -- TOP_K used at snapshot time
    max_dist         real NOT NULL,        -- threshold used at snapshot time
    embedding_model  text NOT NULL,        -- which model produced video_ids
    refreshed_at     timestamptz NOT NULL DEFAULT now(),

    -- editorial
    status           text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','approved','rejected')),
    notes            text,
    created_at       timestamptz NOT NULL DEFAULT now(),

    UNIQUE (query_text)
);

CREATE INDEX semantic_pages_video_ids
    ON cat.semantic_pages USING gin (video_ids);

CREATE INDEX semantic_pages_slug
    ON cat.semantic_pages (COALESCE(slug_final, slug_provisional));

CREATE INDEX semantic_pages_status
    ON cat.semantic_pages (status)
    WHERE status = 'approved';
```

Notes:
- `UNIQUE(query_text)` is the primary dedup. If the operator pastes a slightly
  different form, that's a new row — let the operator merge via `aliases` if
  they want.
- `slug_provisional` is computed by the insert path, not by a generated
  column, because the kebab-from-query algorithm may need refinement and we
  don't want a `STORED` lock-in.
- `GIN(video_ids)` lets us answer "which semantic pages reference video N"
  for future internal linking from `/watch/:slug`.

---

## 4. Snapshot algorithm

```text
1. Pull row: query_text, top_k, max_dist
2. encode = bge.encode("Represent this sentence for searching relevant passages: " + query_text,
                       normalize=True)
3. SET LOCAL hnsw.ef_search = 400;
4. SELECT video_id, embedding <=> $vec AS dist
   FROM cat.video_embeddings e
   JOIN cat.videos v ON v.id = e.video_id AND v.has_thumb = true
   ORDER BY e.embedding <=> $vec
   LIMIT $top_k
   -- captured in Python, then filter dist < max_dist client-side
5. UPDATE cat.semantic_pages
   SET video_ids = $ids, embedding_model = $current_model,
       refreshed_at = now()
   WHERE id = $row_id
```

Notes:
- No `PROJ_WHERE` in the snapshot. Projection is render-time concern (S8); the
  snapshot is the projection-agnostic universe of "videos this query is
  semantically about".
- `has_thumb` filter is in the snapshot — a thumbless video would never render
  anyway, so capturing it wastes a slot.
- `ef_search=400` matches spec 09 §V7. Snapshot is run rarely, so even higher
  would be acceptable; 400 is the proven recall plateau for this set.

---

## 5. Render SQL

```sql
WITH page AS (
    SELECT id, video_ids
    FROM cat.semantic_pages
    WHERE COALESCE(slug_final, slug_provisional) = $1
      AND status = 'approved'
)
SELECT
    v.id, v.slug, v.go_token, v.title, v.duration_sec,
    va.path AS thumb_path,
    array_position(page.video_ids, v.id) AS pos
FROM page
JOIN cat.videos v        ON v.id = ANY(page.video_ids)
JOIN cat.video_assets va ON va.video_id = v.id AND va.kind = 'thumb'
WHERE v.has_thumb = true
  AND (
      v.vertical = ANY($2::text[])
      OR EXISTS (SELECT 1 FROM cat.video_tags vt
                 WHERE vt.video_id = v.id AND vt.tag_id = ANY($3::int[]))
  )
  AND NOT EXISTS (SELECT 1 FROM cat.video_tags vt
                  WHERE vt.video_id = v.id AND vt.tag_id = ANY($4::int[]))
ORDER BY pos
LIMIT $5 OFFSET $6;
```

Companion count query (to gate on `MIN_VIDEOS` and render pagination):

```sql
WITH page AS (
    SELECT video_ids FROM cat.semantic_pages
    WHERE COALESCE(slug_final, slug_provisional) = $1
      AND status = 'approved'
)
SELECT count(*)
FROM page
JOIN cat.videos v ON v.id = ANY(page.video_ids)
WHERE v.has_thumb = true
  AND ( v.vertical = ANY($2::text[])
        OR EXISTS (SELECT 1 FROM cat.video_tags vt
                   WHERE vt.video_id = v.id AND vt.tag_id = ANY($3::int[])) )
  AND NOT EXISTS (SELECT 1 FROM cat.video_tags vt
                  WHERE vt.video_id = v.id AND vt.tag_id = ANY($4::int[]));
```

If count `< 60` (S4) → `404` + `X-Robots-Tag: noindex, follow`. Do not render
a partial page — the whole point of this page type is SEO, and thin content
defeats it.

---

## 6. CLI

Lives in `pipeline/semantic_pages/`. Two scripts:

### 6.1 `create.py` — insert a new row from operator input

```
python -m pipeline.semantic_pages.create \
    --query "shemale big dick orgy" \
    --source operator \
    --source-volume 880 \
    --alias "ts big dick orgy" --alias "tranny big cock orgy"
```

Behaviour:
1. Compute `slug_provisional = kebab(query_text)`.
2. If `UNIQUE(query_text)` collision → log and exit non-zero (operator
   decides whether to update aliases manually).
3. Insert with empty `video_ids = '{}'`, `status='draft'`, current model.
4. Immediately call the refresh path (§6.2) on the new row.
5. Print resulting id + slug.

### 6.2 `refresh.py` — re-snapshot one / many rows

```
python -m pipeline.semantic_pages.refresh <id>
python -m pipeline.semantic_pages.refresh --stale-only
python -m pipeline.semantic_pages.refresh --all
```

Flags:
- `<id>` (positional) — single row.
- `--stale-only` — `WHERE embedding_model != $current_default OR refreshed_at < now() - interval '30 days'`.
- `--all` — every row.
- `--top-k` (default S2 `1000`), `--max-dist` (default S3 `0.40`),
  `--ef-search` (default `400`) — overrides for one-off tuning. Stored
  values on each row are updated to the chosen overrides.
- `--model` (default S5 `BAAI/bge-small-en-v1.5`) — change with care; whole
  table goes stale on swap.
- `--dry-run` — print plan, no UPDATE.

Progress to stdout (common/log.py): one line per row (`refreshed id=N
captured=K kept=K_filtered`), final summary.

### 6.3 Status flip

No dedicated CLI in MVP. Operator runs:

```sql
UPDATE cat.semantic_pages SET status = 'approved' WHERE id = ...;
```

Promote / reject via Adminer or direct SQL. Add a CLI when this becomes a
regular workflow.

---

## 7. Cross-spec compliance

- **Spec 07 §3 contract.** Site reads `cat.semantic_pages`, `cat.videos`,
  `cat.video_assets` only — no `raw.*`. Same allow-list as `/p/:slug`.
- **Spec 07 §4 PROJ_WHERE.** Applied at render (S8). Snapshot is global.
- **Spec 07 C8 (`target_url` leakage).** Render goes through `/go/:token`;
  `target_url` MUST NOT appear in HTML or JSON sent to the client.
- **Spec 09 (`/search`).** Co-exists. `/search` is dynamic, `/s/:slug` is the
  frozen, editorially-blessed projection of a chosen query. Both share the
  encoder and the HNSW index; `/search` does not write to `semantic_pages`
  (S16).
- **Spec 06 §P11.** `MIN_VIDEOS=60` gate (S4) follows the same thin-content
  rationale as page_candidates.

---

## 8. Internal linking (read-side)

Not implemented in MVP. Sketch only:

- "More pages mentioning this video" widget on `/watch/:slug`:
  `SELECT * FROM cat.semantic_pages WHERE $video_id = ANY(video_ids) AND status='approved'`
  (uses the `GIN(video_ids)` index).
- "Related searches" on `/s/:slug`: nearest-neighbour over the `query_text`
  embeddings of other approved rows. Requires storing the query embedding,
  which we do NOT do in MVP. Add a second column + small HNSW when this
  feature lands.

---

## 9. Sample size note

At full bge snapshot (581k vectors, trans-only at MVP), `TOP_K=1000` after
`MAX_DIST=0.40` typically yields 200–800 surviving items per query (highly
query-dependent). That gives 4–13 full-PER_PAGE pages, well above the
MIN_VIDEOS threshold for any non-degenerate query. Degenerate queries (one
or two abstract words, or out-of-distribution language) will fall short and
404 — desired behaviour.

---

## 10. Out of scope (Post-MVP)

- **Auto-refresh worker / cron.** Manual is enough while the table is
  hand-curated. Revisit when row count grows or operator wants daily freshness.
- **Page-to-page similarity** (related-searches widget) — needs query
  embeddings stored.
- **Site-search auto-ingestion** — operator-curated only for now (S16).
- **Per-projection snapshots.** Currently one snapshot per query, render-time
  filter. If a projection regularly drops a query below MIN_VIDEOS we may
  want projection-scoped snapshots — measure first.
- **Multi-language queries.** bge-small-en-v1.5 is English-only; non-English
  queries get poor recall. Out-of-scope until the encoder changes.
- **Admin UI.** Adminer + SQL is fine for MVP. A small Flask admin (like
  `/admin/page_candidates`) can land after the schema is stable.
- **Hybrid lexical + vector snapshot.** Combining `pg_trgm` recall with
  vector ranking — defer until measured gaps.
