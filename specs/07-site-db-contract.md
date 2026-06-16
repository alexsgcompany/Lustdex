# specs/07-site-db-contract.md — DB contract for the public site

Scope: the contract between the Lustdex pipeline (this repo) and the public-site
codebase (separate repo, HonoX on Cloudflare Workers). Defines which schema and
tables the site MAY read, the connection mechanism, the projection filter that
MUST wrap every video query, and canonical query patterns for each page type.

All decisions below are FINAL (approved by the owner).

---

## 1. Decisions (fixed)

| #   | Decision | Value |
|-----|----------|-------|
| C1  | Site runtime | Cloudflare Workers (HonoX). One Worker per domain (lustdts.com / lustdmilf.com / lustdexxx.com), shared codebase, env-bound projection slug. |
| C2  | DB connection | Cloudflare Hyperdrive pooler → Postgres on VPS. Site uses `postgres.js` driver via Hyperdrive binding. |
| C3  | Allowed schema | `cat.*` only. Reading `raw.*` from the site is a BUG (violates schema-split per spec 05). |
| C4  | Allowed access mode | SELECT only. The site does NOT INSERT/UPDATE/DELETE in `cat.*`. Tracking goes to CF Analytics Engine, not Postgres. |
| C5  | Projection scoping | Every video-returning query MUST inject the projection WHERE snippet from §4. No exceptions. |
| C6  | Projection config load | Loaded once at Worker cold start by `slug` from `cat.projections`. Cached for the isolate lifetime; refreshed by re-deploy or new isolate. |
| C7  | CDN base | `BUNNY_PULL_ZONE_URL` (env) + `cat.video_assets.path`. The site reads from the public pull-zone URL only — never BunnyCDN storage. |
| C8  | Outbound clicks | NEVER expose `cat.videos.target_url`. The only public link to provider is `/go/{go_token}` → 302 to `target_url`. |
| C9  | Tracking | Impressions and clicks → CF Analytics Engine (§7). Postgres absorbs zero per-render writes. |

---

## 2. Connection (Hyperdrive in prod, local Docker in dev)

### 2.1 Prod (Cloudflare Workers → Hyperdrive → VPS Postgres)

`wrangler.toml`:
```toml
[[hyperdrive]]
binding = "HYPERDRIVE"
id      = "<hyperdrive-id>"

[vars]
PROJECTION_SLUG     = "milf"  # 'trans' / 'milf' / 'mix' — set per-Worker
BUNNY_PULL_ZONE_URL = "https://lustdex.b-cdn.net"
```

Route example:
```ts
import postgres from 'postgres'

const sql = postgres(c.env.HYPERDRIVE.connectionString, {
  max: 5,         // small pool: isolates are short-lived
  prepare: false, // Hyperdrive does not support session-level prepared statements
})
```

### 2.2 Dev (laptop) — local Docker Postgres, no Hyperdrive

Dev uses **the same `c.env.HYPERDRIVE.connectionString` code path**. The
Postgres backing it is the local Docker instance from this repo's
`docker-compose.yml` (the same DB the pipeline writes to). No deploy round-trip
to test queries.

`wrangler dev` routes the `HYPERDRIVE` binding to a local Postgres URL via
this env variable:

```
# .dev.vars (gitignored, same idea as .env)
WRANGLER_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=postgresql://catalog:<password>@127.0.0.1:5432/catalog
```

Then `wrangler dev` connects through to the local container — `postgres.js`
sees a normal connection string, code is identical to prod.

Prerequisites for dev:
- `docker compose up -d` from this repo (Postgres on `127.0.0.1:5432`)
- migrations applied (`python db/migrate.py`)
- pipeline run at least once so `cat.videos` is populated
- one `cat.projections` row per Worker you want to dev against (already seeded by migration 008/009)

### 2.3 Caching

HTTP-level only — set `Cache-Control: public, s-maxage=...` on the Worker
response so CF edge caches it. No DB-level caching.

Recommended TTLs:
- `/tag/:slug` and `/p/:slug` → `s-maxage=3600`
- `/` (homepage) → `s-maxage=300`
- `/go/:token` → `no-store`

---

## 3. Tables and fields the site uses

| Table | Why the site reads it | Fields the site may use |
|-------|----------------------|-------------------------|
| `cat.projections` | Load own config at startup. | `slug, from_verticals, include_tag_ids, exclude_tag_ids, brand_tag_id, active` |
| `cat.videos` | Listing rows. | `id, slug, go_token, title, duration_sec, has_thumb, published_at, vertical` |
| `cat.video_tags` | JOIN/filter by tag. | `video_id, tag_id` |
| `cat.tags` | Tag pages, navigation. Filter `status = 'active'`. | `id, slug, name, category` |
| `cat.page_candidates` | SEO landings. Filter `status = 'approved'`. | `id, member_tag_ids, slug_final, slug_provisional, volume` |
| `cat.video_assets` | Thumbnails (and future previews). | `video_id, kind, path` |
| `cat.providers` | Optional, rarely needed. | `id, slug, name` |

**Forbidden — NEVER read from the site:**
- `raw.*` (any table) — schema-split contract; raw vocabulary is unbounded and unstable
- `cat.tag_aliases` — pipeline-internal canonicalization map; the site uses canonical `cat.tags` only
- `cat.videos.target_url` — provider URL stays server-side; surfaced only via `/go/{token}` redirect
- `cat.videos.vertical` as a display field — it's a projection inclusion signal, not a user-visible label
- Rows where `cat.tags.status != 'active'` or `cat.page_candidates.status != 'approved'`

Current asset reality (June 2026): only `kind = 'thumb'` rows exist in
`cat.video_assets`. `kind = 'preview'` is reserved for future preview clips.

---

## 4. Projection WHERE helper (universal)

Every query returning videos MUST inject this. Compose it once per request from
the loaded projection row and inline it as a SQL fragment.

```sql
-- Bind: $1 = from_verticals (text[]), $2 = include_tag_ids (int[]), $3 = exclude_tag_ids (int[])
(
    v.vertical = ANY($1)
    OR EXISTS (
        SELECT 1 FROM cat.video_tags vt
        WHERE vt.video_id = v.id AND vt.tag_id = ANY($2)
    )
)
AND NOT EXISTS (
    SELECT 1 FROM cat.video_tags vt
    WHERE vt.video_id = v.id AND vt.tag_id = ANY($3)
)
```

Aliasing: the videos table MUST be aliased as `v` so the snippet drops in. If
you need a different alias, rewrite the snippet at startup — don't sprinkle
multiple snippet variants.

`postgres.js` composition pattern:
```ts
function projectionWhere(sql, p) {
  return sql`
    (
      v.vertical = ANY(${p.from_verticals}::text[])
      OR EXISTS (SELECT 1 FROM cat.video_tags vt
                 WHERE vt.video_id = v.id AND vt.tag_id = ANY(${p.include_tag_ids}::int[]))
    )
    AND NOT EXISTS (SELECT 1 FROM cat.video_tags vt
                    WHERE vt.video_id = v.id AND vt.tag_id = ANY(${p.exclude_tag_ids}::int[]))
  `
}
```

### 4.1 Brand-tag filter for `cat.page_candidates`

Every query that reads `cat.page_candidates` MUST also inject the brand filter
below, so off-brand SEO candidates (e.g. `asian-milf` on a trans-brand site)
never appear in listings, sitemap, or as resolvable `/p/:slug` targets.
Page_candidates table MUST be aliased as `pc`.

```sql
-- Bind: $1 = brand_tag_id (int, nullable)
( $1::int IS NULL OR $1::int = ANY(pc.member_tag_ids) )
```

`postgres.js` composition pattern:
```ts
function brandTagFilter(sql, p) {
  return sql`( ${p.brand_tag_id}::int IS NULL OR ${p.brand_tag_id}::int = ANY(pc.member_tag_ids) )`
}
```

If a projection has `brand_tag_id IS NULL` the filter is a no-op (all approved
candidates pass). Setting `brand_tag_id` on a projection is what makes the site
brand-coherent.

**Why these are WHERE snippets and not DB views:** views can't take params
without writing one view per projection (3 now, N later). The WHERE snippets
keep `cat.videos` and `cat.page_candidates` truly canonical.

---

## 5. Canonical queries (MVP-ready)

### 5.1 Homepage — latest N videos in the projection

```sql
SELECT v.id, v.slug, v.go_token, v.title, v.duration_sec,
       va.path AS thumb_path
FROM cat.videos v
LEFT JOIN cat.video_assets va ON va.video_id = v.id AND va.kind = 'thumb'
WHERE v.has_thumb = true
  AND <projectionWhere>
ORDER BY v.published_at DESC NULLS LAST, v.id DESC
LIMIT 48;
```

CDN URL for the thumb: `${BUNNY_PULL_ZONE_URL}/${thumb_path}` (no leading slash
on `thumb_path`).

### 5.2 Tag page — `/tag/:slug`

```sql
WITH t AS (
    SELECT id FROM cat.tags WHERE slug = $1 AND status = 'active'
)
SELECT v.id, v.slug, v.go_token, v.title, v.duration_sec,
       va.path AS thumb_path
FROM cat.videos v
JOIN cat.video_tags vt ON vt.video_id = v.id AND vt.tag_id = (SELECT id FROM t)
LEFT JOIN cat.video_assets va ON va.video_id = v.id AND va.kind = 'thumb'
WHERE v.has_thumb = true
  AND <projectionWhere>
ORDER BY v.published_at DESC NULLS LAST, v.id DESC
LIMIT 48 OFFSET $2;
```

If `t` is empty → 404. Total count for pagination: same query without
`LIMIT/OFFSET`, wrapped in `SELECT count(*) FROM (...) s`.

### 5.3 SEO landing — `/p/:slug` (from `cat.page_candidates`)

Two-phase per spec 06 §5: resolve, then projection-count, then render.

**Brand-tag filter (mandatory when `projection.brand_tag_id IS NOT NULL`):** the
candidate's `member_tag_ids` MUST contain `brand_tag_id` — see §4.1 for the
snippet. This is what keeps `(asian, milf)` from serving on the trans
projection: a shemale-site SEO page without `shemale` in the tuple has the
wrong search intent. Apply it in the resolve query below AND in the sitemap
query in §5.5. Projections with `brand_tag_id IS NULL` skip this filter.

```sql
-- 1. Resolve the page candidate (brand-tag filter via §4.1).
-- Bind: $1 = slug
SELECT pc.member_tag_ids
FROM cat.page_candidates pc
WHERE COALESCE(pc.slug_final, pc.slug_provisional) = $1
  AND pc.status = 'approved'
  AND <brandTagFilter>;
```

```sql
-- 2. Count projection-scoped videos that have ALL the page's member tags.
-- Bind: $1 = member_tag_ids (int[])
SELECT count(*) FROM cat.videos v
WHERE v.has_thumb = true
  AND v.id IN (
      SELECT vt.video_id FROM cat.video_tags vt
      WHERE vt.tag_id = ANY($1)
      GROUP BY vt.video_id
      HAVING count(DISTINCT vt.tag_id) = cardinality($1)
  )
  AND <projectionWhere>;
```

If count `>= MIN_VIDEOS_PER_PAGE` (MVP: 1) → render. Else → **respond 404 and
set `<meta name="robots" content="noindex">`** so the URL is not eligible for
SEO from this projection's domain.

```sql
-- 3. Page body (same member-tag filter as step 2, full select).
-- Bind: $1 = member_tag_ids (int[]), $2 = offset (int)
SELECT v.id, v.slug, v.go_token, v.title, v.duration_sec,
       va.path AS thumb_path
FROM cat.videos v
LEFT JOIN cat.video_assets va ON va.video_id = v.id AND va.kind = 'thumb'
WHERE v.has_thumb = true
  AND v.id IN (
      SELECT vt.video_id FROM cat.video_tags vt
      WHERE vt.tag_id = ANY($1)
      GROUP BY vt.video_id
      HAVING count(DISTINCT vt.tag_id) = cardinality($1)
  )
  AND <projectionWhere>
ORDER BY v.published_at DESC NULLS LAST, v.id DESC
LIMIT 48 OFFSET $2;
```

`page_candidates.lexical_count` is the GLOBAL count, not projection-scoped —
do not display it as "X videos" on the landing. Use the count from step 2.

### 5.4 `/go/:token` — outbound redirect

```sql
SELECT target_url FROM cat.videos WHERE go_token = $1;
```

Not projection-scoped: a click can originate from any page; the `go_token` is
opaque. If row missing → 404. Else → 302 redirect.

Emit a click event to CF Analytics Engine before redirecting (§7).

### 5.5 Sitemap

Sitemap rows MUST apply the same projection filter as their pages — otherwise
the site links to URLs that 404 on render (see spec 06 §5).

- `/tag/{slug}` sitemap: list every `cat.tags.slug` where the projection
  contains ≥ 1 video tagged with it (one count per tag, batchable).
- `/p/{slug}` sitemap: iterate `cat.page_candidates pc` filtered by
  `<brandTagFilter>` (§4.1), then apply the same projection-count check as 5.3
  step 2 per row; skip rows where count = 0.

---

## 6. Future queries (not yet ready — schema gaps)

### 6.1 Vector search

**Blocked on:** `cat.video_embeddings` table (`video_id`, `embedding vector(N)`)
and `pipeline/embeddings/` to populate it. Tracked in TODO post-MVP.

**Planned shape (do not implement until schema exists):**

```sql
SELECT v.id, v.slug, v.go_token, v.title,
       (e.embedding <=> $query_embedding) AS distance
FROM cat.video_embeddings e
JOIN cat.videos v ON v.id = e.video_id
WHERE <projectionWhere>
ORDER BY e.embedding <=> $query_embedding
LIMIT 24;
```

Open: embedding model + dim, index type (HNSW vs IVFFlat), where the query
text is embedded (Worker via OpenRouter call vs VPS-side helper).

### 6.2 Actor pages — `/actor/:slug` and `/actor/:slug/:seo`

**Blocked on:** performers canonicalization. `raw.raw_videos.performers_raw`
is unbounded text; needs `cat.performers` (id, slug, name) +
`cat.video_performers` (video_id, performer_id), analogous to tags.

**Planned shape:** mirror §5.2, joining `cat.video_performers` to
`cat.performers.slug = $1`. The actor+SEO combo page (`/actor/:slug/:seo`)
layers the page-candidate count check from §5.3 on top of the actor filter.

---

## 7. Tracking (CF Analytics Engine, not Postgres)

Impressions (every video tile rendered on every page) and clicks would dwarf
catalog write traffic if sent to Postgres. They go to CF Analytics Engine
instead — purpose-built for high-write, sampled, append-only event data.

`wrangler.toml`:
```toml
[[analytics_engine_datasets]]
binding = "IMPRESSIONS"
dataset = "lustdex_impressions"

[[analytics_engine_datasets]]
binding = "CLICKS"
dataset = "lustdex_clicks"
```

Impression write (in any listing-page route, after rows fetched):
```ts
c.env.IMPRESSIONS.writeDataPoint({
  blobs:   [projectionSlug, pageType, pageSlug],  // 'milf','tag','blonde'
  doubles: [rows.length],
  indexes: [`${projectionSlug}:${pageType}:${pageSlug}`],
})
```

Click write (in `/go/:token`, before the 302):
```ts
c.env.CLICKS.writeDataPoint({
  blobs:   [projectionSlug, goToken, referer ?? ''],
  doubles: [1],
  indexes: [goToken],
})
```

Querying for dashboards: CF Analytics Engine SQL API. Out of scope for this
contract — separate spec when dashboards are built.

---

## 8. Service pages

Routes like `/about`, `/2257`, `/dmca`, `/terms`, `/privacy` do NOT touch the
DB. Content lives as JSX/MDX in the site repo. Listed here only to make
explicit that the site has zero-DB routes.

---

## 9. Out of scope

- CF Analytics Engine querying for dashboards (separate spec when needed)
- Schemas for `cat.video_embeddings`, `cat.performers`, `cat.video_performers`
  (each gets its own spec when prioritized)
- Raising `MIN_VIDEOS_PER_PAGE` above 1 (spec 06 §5)
- Sitemap implementation strategy (XML segmenting, refresh cadence) — deferred to site repo
- Admin UI for projections editing (spec 06 P9; post-MVP)
