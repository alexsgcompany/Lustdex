-- Projections: per-site subsetting of the shared catalog.
-- See specs/06-projections.md.

-- 1. Denormalize feed niche into cat.videos.vertical
ALTER TABLE cat.videos ADD COLUMN vertical text;
CREATE INDEX ON cat.videos (vertical);

-- 2. Backfill from raw.raw_videos -> raw.feeds.niche.
-- Depends on the promote-contract invariant: cat.videos.id == raw.raw_videos.id
-- (enforced in pipeline/promote/videos.py and migration 007's backfill).
UPDATE cat.videos cv
SET vertical = f.niche
FROM raw.raw_videos rv
JOIN raw.feeds f ON f.id = rv.feed_id
WHERE rv.id = cv.id;

-- 3. Projection config table
CREATE TABLE cat.projections (
    id              serial PRIMARY KEY,
    slug            text UNIQUE NOT NULL,
    name            text NOT NULL,
    domain          text,
    from_verticals  text[] NOT NULL DEFAULT '{}',
    include_tag_ids int[]  NOT NULL DEFAULT '{}',
    exclude_tag_ids int[]  NOT NULL DEFAULT '{}',
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- 4. Seed projections (tag IDs re-verified against live cat.tags before commit)
INSERT INTO cat.projections (slug, name, from_verticals, include_tag_ids, exclude_tag_ids)
VALUES
  ('trans', 'Transgender',
   ARRAY['trans'],
   ARRAY[179]::int[],              -- shemale
   ARRAY[]::int[]),
  ('milf',  'MILF',
   ARRAY[]::text[],                -- no MILF-pinned feed: raw.feeds.niche in {'trans','mix'}
   ARRAY[81, 127, 80, 20]::int[],  -- milf, stepmom, mature, cougar
   ARRAY[]::int[]);                -- DO NOT exclude trans/shemale: legitimate trans-MILF
                                   -- videos belong in both projections (P10).
