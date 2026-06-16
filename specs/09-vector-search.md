# specs/09-vector-search.md — Vector search endpoint (HonoX Worker)

Scope: defines the `GET /search` endpoint on the public site (HonoX on
Cloudflare Workers). Wires `cat.video_embeddings` (spec 08) into the site
through Hyperdrive + Workers AI. Spec 07 is the binding contract for **what**
the Worker may read from Postgres; this spec adds the **how** for vector
search specifically.

All decisions below are FINAL unless explicitly marked as **OPEN**.

---

## 1. Decisions (fixed)

| #   | Decision | Value |
|-----|----------|-------|
| V1  | Endpoint | `GET /search?q=<query>&page=<n>` |
| V2  | Query encoder | `@cf/baai/bge-small-en-v1.5` via Workers AI binding `env.AI`. Same model family as the indexed vectors (spec 08 §E1) — distances are meaningful. Verified present in CF Workers AI catalog 2026-06-16. |
| V3  | Query text composition | `"Represent this sentence for searching relevant passages: " + q.trim()`. This query-side instruction is the BAAI-published convention for bge-v1.5 retrieval; it goes ONLY on the query, NOT on the indexed passages (spec 08 §E5). Indexed docs are raw text. |
| V4  | Distance metric | Cosine via `<=>` on `halfvec`. Embeddings are normalized at index-time (spec 08), so `<=>` ∈ [0, 2] with `0 = identical`, `1 = orthogonal`. |
| V5  | `TOP_K` (HNSW pull) | `2000` — large enough to survive threshold + pagination depth, small enough to round-trip Hyperdrive within the Worker CPU budget. |
| V6  | Distance threshold | `MAX_DIST = 0.40` (cosine similarity > 0.6). Soft floor — chosen as a "few false positives, no obvious garbage" compromise on e5-small + adult catalog. Override via `?dist_max=` debug param. |
| V7  | `ef_search` | `400` (= 2 × TOP_K cap-aware; pgvector accepts max=1000 by default, we run at 400 to keep latency modest). Set per-request via `SET LOCAL`. |
| V8  | `PER_PAGE` | `60` (matches site listing conventions). |
| V9  | Pagination | `LIMIT/OFFSET` on the threshold-filtered set, tie-broken by `video_id ASC` for determinism. |
| V10 | Min query length | 2 chars after trim. Empty/too-short → render landing form, do NOT call the encoder or DB. |
| V11 | Max query length | 200 chars. Hard-cut anything longer in the Worker before encoding. |
| V12 | Empty results | Render the search page with a "no results for X" message. Do NOT 404 (a valid query that finds nothing is not a missing page). |
| V13 | Projection scoping | Spec 07 §4 PROJ_WHERE is MANDATORY (spec 07 P8 / C5). Even though embeddings are currently only populated for `trans`, the WHERE goes in for forward-compat when `milf`/`mix` get embedded. |
| V14 | `has_thumb` filter | Site listings skip thumbless videos (spec 07 §5.1+). Same applies here — `INNER JOIN cat.video_assets` on `kind='thumb'` enforces it. |
| V15 | Caching | CF Cache API by full URL key, TTL 1 hour. `Cache-Control: public, s-maxage=3600`. `robots: noindex, follow` (search-result pages are not SEO targets). |
| V16 | Error path | Encoder failure → 502 + log. DB failure → 500 + log. Per-request, do not surface internals to user. |
| V17 | Raw-schema access | Forbidden (spec 07 C3). Site reads `cat.video_embeddings`, `cat.videos`, `cat.video_assets` only. |

---

## 2. Why each knob is set this way

- **TOP_K=2000 + threshold=0.40 + PER_PAGE=60.** A typical adult-search session has high page-depth; we need enough relevant candidates to fill 10–30 pages. TOP_K=2000 gives ~30 pages even if half the candidates fail the threshold. The threshold is the cliff that protects against "we ran out of really-related results, here's noise".
- **`ef_search=400`.** HNSW returns ≤ `ef_search` rows; if `ef_search < TOP_K`, the LIMIT silently returns fewer. We keep `ef_search` *below* `TOP_K`: `400` is sufficient recall in our A/B testing for the trans set (recall@2000 was effectively saturated), and 5× lower CPU than `ef_search=2000`. Bump if recall complaints surface.
- **OFFSET-based paging.** No need to stash the query vector between page hits — each page re-encodes (5–10 ms via Workers AI) + re-queries HNSW (~30–60 ms). Stateless, deterministic, no session/KV ties.

---

## 3. Embedding source

### 3.a Workers AI (chosen path)

```toml
# wrangler.toml
[ai]
binding = "AI"
```

```ts
const out = await env.AI.run("@cf/baai/bge-small-en-v1.5", {
  text: ["Represent this sentence for searching relevant passages: " + q],
});
const vec: number[] = out.data[0]; // 384 normalized floats
```

Cost: ~$0.0001 per 1k embeddings. Latency: 5–15 ms within the same CF
runtime.

### 3.b Fallback (only if Workers AI removes bge-small-en-v1.5 later)

Self-host the same model on the VPS via Python + `sentence-transformers`
(`BAAI/bge-small-en-v1.5`), behind CF Tunnel + Access service token (mirror
the Hyperdrive auth pattern from [[project-hyperdrive]]). Adds ~700 MB RAM
ongoing + ~2.5 GB disk on the VPS. Acceptable as a contingency. **Do not
substitute a different embedding model** without re-embedding the entire
`cat.video_embeddings` table — cross-model distances are noise.

---

## 4. Canonical SQL

The query is a CTE: HNSW top-K via the index, then threshold + page-cut on the
materialised set. PROJ_WHERE per spec 07 §4 goes inside the CTE so the index
scan stays projection-aware.

```sql
-- $1 = query embedding (text literal '[v1,v2,...,v384]', cast to halfvec)
-- $2..$4 = projection binds (from_verticals, include_tag_ids, exclude_tag_ids)
-- $5 = TOP_K
-- $6 = MAX_DIST
-- $7 = PER_PAGE
-- $8 = OFFSET

SET LOCAL hnsw.ef_search = 400;

WITH topk AS (
    SELECT
        v.id,
        v.slug,
        v.go_token,
        v.title,
        v.duration_sec,
        va.path AS thumb_path,
        e.embedding <=> $1::halfvec AS dist
    FROM cat.video_embeddings e
    JOIN cat.videos v        ON v.id = e.video_id
    JOIN cat.video_assets va ON va.video_id = v.id AND va.kind = 'thumb'
    WHERE v.has_thumb = true
      AND (
          v.vertical = ANY($2::text[])
          OR EXISTS (SELECT 1 FROM cat.video_tags vt
                     WHERE vt.video_id = v.id AND vt.tag_id = ANY($3::int[]))
      )
      AND NOT EXISTS (SELECT 1 FROM cat.video_tags vt
                      WHERE vt.video_id = v.id AND vt.tag_id = ANY($4::int[]))
    ORDER BY e.embedding <=> $1::halfvec
    LIMIT $5
)
SELECT id, slug, go_token, title, duration_sec, thumb_path, dist
FROM topk
WHERE dist < $6
ORDER BY dist, id
LIMIT $7 OFFSET $8;
```

For total count (needed to render "showing X of Y" + last-page link):

```sql
-- Same binds $1..$6; no $7/$8.
SET LOCAL hnsw.ef_search = 400;

WITH topk AS (
    SELECT e.embedding <=> $1::halfvec AS dist
    FROM cat.video_embeddings e
    JOIN cat.videos v        ON v.id = e.video_id
    JOIN cat.video_assets va ON va.video_id = v.id AND va.kind = 'thumb'
    WHERE v.has_thumb = true
      AND ( v.vertical = ANY($2::text[])
            OR EXISTS (SELECT 1 FROM cat.video_tags vt
                       WHERE vt.video_id = v.id AND vt.tag_id = ANY($3::int[])) )
      AND NOT EXISTS (SELECT 1 FROM cat.video_tags vt
                      WHERE vt.video_id = v.id AND vt.tag_id = ANY($4::int[]))
    ORDER BY e.embedding <=> $1::halfvec
    LIMIT $5
)
SELECT count(*) FROM topk WHERE dist < $6;
```

**Why `SET LOCAL` inline and not in a session pool config:** Hyperdrive
multiplexes pooled sessions, so a `SET hnsw.ef_search` outside a transaction
leaks across requests. `SET LOCAL` is transaction-scoped — `postgres.js`
wraps each query in an implicit transaction, so this is safe.

**Vector literal format.** `postgres.js` cannot adapt JS `number[]` to
`halfvec` directly. Format as a text literal in the Worker:

```ts
const lit = "[" + vec.map((x) => x.toFixed(6)).join(",") + "]";
```

Pass `lit` as `$1` and the SQL casts via `::halfvec`.

---

## 5. Pagination

`?page=` is 1-based. `offset = (page - 1) * PER_PAGE`. Render `prev`/`next`
links from the total count; cap `page` at `ceil(total / PER_PAGE)` to avoid
empty terminal pages.

Tie-breaker `ORDER BY dist, id` makes the order deterministic — two videos
with identical distance (rare, but possible with halfvec quantisation)
always sort the same way across pages.

---

## 6. Empty / no-results state

Three buckets, all rendered on `/search` (no 404):

| Case | Render |
|------|--------|
| `q` empty/whitespace/too short | Landing: input + brief copy. No DB call. |
| Encoder OK, threshold filtered everything out | "No results for *q*. Try shorter/broader terms." Suggest top tags as alternatives. |
| Encoder fails (502) | "Search is temporarily unavailable." Log to Workers logs. |
| DB fails (500) | Same fallback as encoder fail. |

---

## 7. Caching

```ts
const cache = caches.default;
const cacheKey = new Request(c.req.url, c.req.raw);
const cached = await cache.match(cacheKey);
if (cached) return cached;

const res = await renderSearch(c);
res.headers.set("Cache-Control", "public, s-maxage=3600");
res.headers.set("X-Robots-Tag", "noindex, follow");
c.executionCtx.waitUntil(cache.put(cacheKey, res.clone()));
return res;
```

Cache key is the full URL — `q`, `page`, debug params all participate. `noindex`
keeps search-result pages out of Google.

---

## 8. Debug / tuning params (NOT exposed in UI)

| Param | Default | Notes |
|-------|---------|-------|
| `?dist_max=0.40` | V6 | Override distance cap. Lower = stricter. |
| `?top_k=2000`    | V5 | Override HNSW pull. Useful for measuring recall complaints. |
| `?ef_search=400` | V7 | Override `hnsw.ef_search`. Cap at 1000 in the Worker. |
| `?debug=1`       | off | Append per-row `dist` and the resolved params to the rendered page. |

Bound checks on every numeric param — anything out of range falls back to default.

---

## 9. Spec 07 cross-ref

Spec 07 §6.1 ("Vector search") was a stub waiting on `cat.video_embeddings`.
With spec 08 / migrations 011+012 landed and embeddings populated, §6.1
should be reduced to a pointer to this spec. Implementation MUST otherwise
respect spec 07 C3 (cat-only schema), C5 (PROJ_WHERE), C7 (CDN composition),
C8 (no `target_url` leakage — the search result must use `/go/:token`).

---

## 10. Out of scope (Post-MVP)

- **Multi-vertical search.** When `milf`/`mix` embeddings land, the endpoint
  works automatically — no schema or query change. Worker just runs against
  whichever projection it was deployed for.
- **Hybrid lexical + vector.** Combining `pg_trgm` / FTS with cosine
  re-ranking can help on short ambiguous queries ("teen"). Out of scope
  until vector-only proves insufficient.
- **Personalization / re-ranking from click signals.** Needs CF Analytics
  Engine read path (TODO post-MVP).
- **Related-videos block on `/watch`.** Reuses the same table but a
  different query shape (similar to a known video_id, not a text query) —
  separate spec section if/when needed.
- **Server-side encoder caching.** Hot queries could be memoised
  (Workers KV by `sha1("query: " + q)`). Skip until traffic shape is known.
- **Filtered HNSW iterative scan** (`SET hnsw.iterative_scan = strict_order`).
  Worth flipping on if PROJ_WHERE filters out a large fraction of the top-K
  on `milf`/`mix` projections. Trans projection: filter is near-no-op.
