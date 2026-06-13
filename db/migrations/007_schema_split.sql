CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA raw;
CREATE SCHEMA cat;

-- Move serving-side dimension tables first (referenced by raw.raw_videos cross-schema)
ALTER TABLE providers   SET SCHEMA cat;
ALTER TABLE tags        SET SCHEMA cat;
ALTER TABLE tag_aliases SET SCHEMA cat;

-- Move raw ingest tables
ALTER TABLE raw_videos    SET SCHEMA raw;
ALTER TABLE unmapped_tags SET SCHEMA raw;
ALTER TABLE sites         SET SCHEMA raw;
ALTER TABLE feeds         SET SCHEMA raw;
ALTER TABLE pipeline_runs SET SCHEMA raw;

-- Lean serving projection of raw.raw_videos
CREATE TABLE cat.videos (
    id              bigint PRIMARY KEY,
    provider_id     int NOT NULL REFERENCES cat.providers(id),
    external_id     text NOT NULL,
    title           text,
    slug            text NOT NULL,
    go_token        text NOT NULL UNIQUE,
    duration_sec    int,
    target_url      text NOT NULL,
    has_thumb       boolean NOT NULL DEFAULT false,
    previews_count  smallint NOT NULL DEFAULT 0,
    published_at    timestamptz,
    promoted_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider_id, external_id)
);
CREATE INDEX ON cat.videos (provider_id);

-- Backfill cat.videos from raw.raw_videos.
-- slug: lowercase, replace [^a-z0-9]+ with '-', trim '-', max 60 chars, fallback 'v'.
--       NOTE: this does NOT ascii-fold accents (no unaccent dep). Cyrillic / heavily-accented
--       titles end up with slug='v'. The Python promote step uses NFKD ASCII fold for new rows;
--       existing rows keep whatever the SQL backfill picked — slug is pinned at first promote.
-- go_token: 16 hex chars from 8 random bytes (pgcrypto).
INSERT INTO cat.videos (
    id, provider_id, external_id, title, slug, go_token,
    duration_sec, target_url, published_at
)
SELECT
    rv.id,
    rv.provider_id,
    rv.external_id,
    rv.title,
    COALESCE(
        NULLIF(
            substring(
                trim(both '-' from regexp_replace(lower(coalesce(rv.title, '')), '[^a-z0-9]+', '-', 'g'))
                from 1 for 60
            ),
            ''
        ),
        'v'
    ),
    encode(gen_random_bytes(8), 'hex'),
    rv.duration_sec,
    coalesce(rv.target_url, ''),
    rv.published_at
FROM raw.raw_videos rv;

-- Move + rewire video_tags FK to cat.videos
ALTER TABLE video_tags SET SCHEMA cat;
ALTER TABLE cat.video_tags DROP CONSTRAINT video_tags_video_id_fkey;
ALTER TABLE cat.video_tags ADD CONSTRAINT video_tags_video_id_fkey
    FOREIGN KEY (video_id) REFERENCES cat.videos(id) ON DELETE CASCADE;

-- Move page_candidates (no FK to raw_videos)
ALTER TABLE page_candidates SET SCHEMA cat;

-- Move + rewire video_assets FK to cat.videos
ALTER TABLE video_assets SET SCHEMA cat;
ALTER TABLE cat.video_assets DROP CONSTRAINT video_assets_video_id_fkey;
ALTER TABLE cat.video_assets ADD CONSTRAINT video_assets_video_id_fkey
    FOREIGN KEY (video_id) REFERENCES cat.videos(id) ON DELETE CASCADE;
