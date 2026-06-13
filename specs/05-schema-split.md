# specs/05-schema-split.md — Schema split: raw.* (ingest) and cat.* (serving)

Scope: physically separate the ingest/pipeline tables from the serving catalog tables.
Done now (before DB grows large) to avoid migrating millions of rows later.

All decisions below are FINAL (approved by the owner).

---

## 1. Decisions (fixed)

| #  | Decision | Value |
|----|----------|-------|
| S1 | Schema names | `raw` (ingest, full payload) and `cat` (catalog, lean serving) |
| S2 | Physical separation | both schemas live in the same Postgres for now — split to separate instances only when RAM pressure forces it |
| S3 | What goes to `raw.*` | tables we WRITE during ingest and READ during pipeline processing; users never see these |
| S4 | What goes to `cat.*` | tables we READ during serving (Flask, future SSR); must be lean for RAM-resident operation |
| S5 | `cat.videos` is a lean projection | NOT a view; physical copy of fields needed for serving + computed fields (slug, go_token) |
| S6 | `slug` is pinned at first promote | NEVER recomputed on title change — guarantees CDN URL stability |
| S7 | `go_token` masks outbound provider URLs | random 16 hex chars (`gen_random_bytes(8)`); Flask `/go/{token}` → 302 to `target_url`; users never see the provider host |
| S8 | Initial backfill happens INSIDE migration 007 | uses simple SQL slugifier (lowercase, `[^a-z0-9]+ → -`, trim, max 60, fallback `'v'`); does NOT ASCII-fold accents — early Cyrillic/accented titles get `v-{id}` slugs; acceptable for the existing ~11k rows |
| S9 | `cat.providers` is the shared dimension | both schemas reference it; small table, lives in `cat` |
| S10 | `pipeline_runs` lives in `raw` | operational log, not user-facing |

---

## 2. Table mapping

### `raw.*` (full payload, immutable, disk-OK)
- `raw_videos` — full feed row, `payload jsonb`, `description`, `tags_raw`, `performers_raw`, `thumb_url` (source)
- `unmapped_tags`
- `sites`
- `feeds`
- `pipeline_runs`

### `cat.*` (lean, hot, RAM-friendly)
- `providers` — shared dimension
- `videos` — NEW (lean projection)
- `tags`
- `tag_aliases`
- `video_tags` (FK → `cat.videos`)
- `page_candidates`
- `video_assets` (FK → `cat.videos`)

### `public.*` (system)
- `schema_migrations` — migration tracker, untouched

---

## 3. `cat.videos` schema

```sql
CREATE TABLE cat.videos (
    id              bigint PRIMARY KEY,            -- = raw.raw_videos.id (promote keeps the id)
    provider_id     int NOT NULL REFERENCES cat.providers(id),
    external_id     text NOT NULL,
    title           text,
    slug            text NOT NULL,                 -- pinned at first promote, never updated
    go_token        text NOT NULL UNIQUE,          -- 16 hex chars, opaque outbound mask
    duration_sec    int,
    target_url      text NOT NULL,                 -- the provider URL — never exposed to users; reached via /go/{go_token}
    has_thumb       boolean NOT NULL DEFAULT false,
    previews_count  smallint NOT NULL DEFAULT 0,
    published_at    timestamptz,
    promoted_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider_id, external_id)
);
CREATE INDEX ON cat.videos (provider_id);
```

Row size on 6M videos × ~150 bytes ≈ **0.9 GB**, fits in RAM.

Fields deliberately NOT in `cat.videos` (they stay in `raw.raw_videos`): `description`, `tags_raw`,
`performers_raw`, `payload`, `fetched_at`, source `thumb_url`. The pipeline reads them from `raw`
when processing; serving never needs them.

---

## 4. `pipeline.promote.videos`

New module. Promotes (upserts) rows from `raw.raw_videos` into `cat.videos`.

**Idempotency rules:**
- `slug` and `go_token` are set ONLY on first INSERT — `ON CONFLICT DO UPDATE` does NOT touch them
- All other fields (title, duration, target_url, published_at) are refreshed
- `has_thumb` and `previews_count` are managed by `pipeline.media.*` — promote leaves them alone

**Python slug** (matches `pipeline/media/upload_thumbs.py:_slug`):
NFKD ASCII fold, lowercase, `[^a-z0-9]+ → -`, trim `-`, max 60 chars (no mid-word cut), fallback `'v'`.

**go_token:** `secrets.token_hex(8)` → 16 hex chars.

CLI: `python -m pipeline.promote.videos` (no flags; processes all raw rows missing from cat).

---

## 5. Migration 007 — what it does

1. `CREATE EXTENSION IF NOT EXISTS pgcrypto;` (for `gen_random_bytes`)
2. `CREATE SCHEMA raw; CREATE SCHEMA cat;`
3. `ALTER TABLE ... SET SCHEMA cat;` for `providers`, `tags`, `tag_aliases`
4. `ALTER TABLE ... SET SCHEMA raw;` for `raw_videos`, `unmapped_tags`, `sites`, `feeds`, `pipeline_runs`
5. `CREATE TABLE cat.videos (...)`
6. Backfill `cat.videos` from `raw.raw_videos` using SQL slugifier (S8)
7. Move `video_tags` to `cat`, rewire FK `video_id → cat.videos`
8. Move `page_candidates` to `cat`
9. Move `video_assets` to `cat`, rewire FK `video_id → cat.videos`

After migration: 11k+ existing rows in `raw.raw_videos` and mirrored into `cat.videos`. New ingest
runs need `pipeline.promote.videos` to populate `cat.videos`.

---

## 6. Code-side changes (one-off, mechanical)

| File | Change |
|------|--------|
| `pipeline/ingest/zilla_cash.py` | `raw_videos` → `raw.raw_videos` |
| `pipeline/ingest/adultnext.py` | same |
| `pipeline/tags/collect.py` | `unmapped_tags` → `raw.unmapped_tags`, `tag_aliases` → `cat.tag_aliases`, `raw_videos` → `raw.raw_videos` |
| `pipeline/tags/cascade.py` | `tags` → `cat.tags`, `tag_aliases` → `cat.tag_aliases`, `unmapped_tags` → `raw.unmapped_tags` |
| `pipeline/tags/apply.py` | `tag_aliases` → `cat.tag_aliases`, `raw_videos` → `raw.raw_videos`, `video_tags` → `cat.video_tags` |
| `pipeline/tags/seed_dictionary.py` | `unmapped_tags` → `raw.unmapped_tags` |
| `pipeline/pages/cooccurrence.py` | `video_tags` → `cat.video_tags` |
| `pipeline/pages/slug.py` | `tags` → `cat.tags` |
| `pipeline/media/upload_thumbs.py` | read from `cat.videos JOIN raw.raw_videos`, path uses `provider_id` from `cat.videos` |
| `pipeline/promote/videos.py` | NEW |
| `demo/app.py` | read `cat.videos LEFT JOIN cat.video_assets`; `raw_videos.title/thumb_url` replaced |
| `admin/tags/queries.py` | all references qualified |
| `admin/tags/review.py` | same |
| `admin/tags/dictionary.py` | `tags` → `cat.tags` |
| `admin/page_screens/builder.py` | `tags` → `cat.tags` |
| `scripts/import_dictionary.py` | all references qualified |

---

## 7. Future: `/go/{token}` route

Out of scope for this spec — but the column is here so the masking is ready.
Flask route:
```python
@app.route("/go/<token>")
def go(token):
    row = conn.execute("SELECT target_url FROM cat.videos WHERE go_token = %s", (token,)).fetchone()
    if not row:
        abort(404)
    return redirect(row[0], code=302)
```

To be added when the demo is wired to `cat.videos`.
