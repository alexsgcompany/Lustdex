# specs/04-media-cdn.md — Media CDN: thumbnail upload to Bunny.net

Scope: download provider thumbnails, convert to WebP, upload to Bunny.net storage,
record the asset in `video_assets`. Pull-zone serving (Cloudflare → Bunny pull) is
out of scope — this spec stops at "the file is in Bunny storage and DB has a row".

All decisions below are FINAL (approved by the owner).

---

## 1. Decisions (fixed)

| #  | Decision | Value |
|----|----------|-------|
| C1 | Storage provider | Bunny.net Storage zone `lustdex-img` (Frankfurt, DE) |
| C2 | API host | `storage.bunnycdn.com` (Frankfurt = default; no regional prefix) |
| C3 | URL structure on CDN | `i/{provider_id}/{shard}/{slug-60ch}-{id}.webp` where `shard = f"{id // 10000:03d}"`. Provider folder lets us do per-provider operations on Bunny (delete/audit/backup). Shard caps any one directory under ~10k files (ZFS-style storage doesn't like millions per dir). Numbers in path are SEO-neutral; keyword content stays in the filename. |
| C4 | Slug source | slugified `raw_videos.title`; empty/non-ASCII → fallback `v` (so path becomes `i/v-{id}.webp`) |
| C5 | Slug rules | NFKD ASCII-fold, lowercase, `[^a-z0-9]+` → `-`, trim, ≤ 60 chars, no mid-word cut |
| C6 | ID component | `raw_videos.id` (internal, globally unique, short) — NOT `external_id` (collisions across providers) |
| C7 | Image format | WebP, quality **80**, encoder method 6 (slow/best compression) |
| C8 | Resize | max-width **640 px**, aspect preserved, NO upscale (small images left as-is) |
| C9 | Original SHA-256 | stored in `video_assets.sha256` for future cross-provider dedup |
| C10 | DB pattern | separate `video_assets` table (NOT a column on `raw_videos` — preserves raw immutability) |
| C11 | Asset kind | only `'thumb'` for now; schema supports more (`preview`, `poster`) later |
| C12 | Uniqueness | `UNIQUE (video_id, kind)` — one thumb per video, re-runs are no-ops |
| C13 | Failure model | log + skip + count; never abort the batch (FOUNDATION rule 11) |
| C14 | Failures are NOT recorded | absence of a row = "not uploaded"; next run will retry. Add `video_asset_errors` only if retry noise becomes a real problem |
| C15 | Concurrency | `ThreadPoolExecutor` (I/O-bound: HTTP in + WebP encode + HTTP out); default 8 workers |
| C16 | DB inserts | main thread inserts results as futures complete; one insert per success (volume is low — no need for batched COPY here) |

---

## 2. Migration 006_video_assets.sql

```sql
CREATE TABLE video_assets (
    id          bigserial PRIMARY KEY,
    video_id    bigint NOT NULL REFERENCES raw_videos(id) ON DELETE CASCADE,
    kind        text NOT NULL,                      -- 'thumb' | future: 'preview', 'poster'
    path        text NOT NULL,                      -- 'i/2/001/blonde-milf-12345.webp' (no leading slash, no domain)
    sha256      text,                               -- of the ORIGINAL downloaded bytes (pre-resize, pre-webp)
    width       int,                                -- final (post-resize) width in pixels
    height      int,                                -- final (post-resize) height in pixels
    bytes       int,                                -- size of the uploaded webp
    source_url  text NOT NULL,                      -- the provider URL we downloaded from
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (video_id, kind)
);
CREATE INDEX ON video_assets (kind);
CREATE INDEX ON video_assets (sha256) WHERE sha256 IS NOT NULL;
```

The `UNIQUE (video_id, kind)` lets us `ON CONFLICT DO NOTHING` for idempotent re-runs.

Demo render formula (NOT applied in this spec — out of scope; documented for future):
`final_url = BUNNY_PULL_ZONE_URL + '/' + video_assets.path`

---

## 3. Slug algorithm (`_slug(title) -> str`)

```
1. unicodedata.normalize("NFKD", title)
2. encode("ascii", "ignore").decode("ascii")
3. lower()
4. re.sub(r"[^a-z0-9]+", "-", s)
5. strip("-")
6. if len(s) > 60: cut at last "-" before 60 (no mid-word cuts); else keep
7. if s == "": return "v" (fallback)
```

Final path: `f"i/{provider_id}/{video_id // 10000:03d}/{slug}-{video_id}.webp"`.
The `-{video_id}` suffix guarantees global uniqueness even when titles collide;
the shard directory caps any one directory at ~10k files; the provider folder
allows per-provider operations on Bunny storage.

---

## 4. Upload algorithm (per video)

```
1. SELECT id, title, thumb_url, provider_slug
   FROM raw_videos LEFT JOIN video_assets WHERE asset IS NULL AND thumb_url IS NOT NULL
   LIMIT N
2. for each (concurrent, ThreadPoolExecutor):
   a. httpx.get(thumb_url, timeout=10, follow_redirects=True)
      - max 5 MB body (read in stream, abort if exceeded)
      - non-2xx → fail
   b. sha256 of raw bytes
   c. Pillow: open → convert("RGB") if mode not in ("RGB", "RGBA")
      - if width > 640: resize to (640, round(640*h/w)) with LANCZOS
      - else: leave as-is
   d. encode WebP quality=80 method=6 → bytes
   e. path = f"i/{provider_id}/{id // 10000:03d}/{slug}-{id}.webp"
      (slug + provider_id come from cat.videos; thumb_url stays in raw.raw_videos)
   f. PUT https://storage.bunnycdn.com/lustdex-img/{path}
      - headers: AccessKey, Content-Type: image/webp
      - 2xx → success; non-2xx → fail
3. main thread: as_completed
   - success → INSERT INTO video_assets (...) ON CONFLICT DO NOTHING
   - failure → log + counter
4. final report: ok=N, failed=M, skipped=K
```

---

## 5. CLI

```
python -m pipeline.media.upload_thumbs                # all pending, 8 workers
python -m pipeline.media.upload_thumbs --limit 500    # first 500 pending
python -m pipeline.media.upload_thumbs --workers 16   # more parallelism
```

Logging: INFO progress every 50 successes, ERROR per failure with provider+id+reason.

---

## 6. Environment variables (added to `.env.example`)

```
BUNNY_STORAGE_ZONE=lustdex-img
BUNNY_STORAGE_KEY=
BUNNY_STORAGE_HOST=storage.bunnycdn.com
BUNNY_PULL_ZONE_URL=                                  # e.g. https://i.lustdex.com — used by future demo
```

`BUNNY_PULL_ZONE_URL` is unused by this module but added now so the demo can pick it up
without a second `.env` migration round.
