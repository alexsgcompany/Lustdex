-- 015_performers.sql — Performers canonicalization + gender (spec 11)
--
-- Mirrors the tags shape (cat.tags / cat.tag_aliases / cat.video_tags):
--   performers          — canonical performer + gender + denormalized n_videos
--   performer_aliases   — normalize(surface name) → performer_id  (dedup layer)
--   video_performers    — junction; video_id == raw_videos.id (shared id-space)
--
-- Deterministic from raw.* → runnable directly on prod (spec 11 §P10). Gender is
-- filled by an LLM batch pass (gender_source='llm') + admin override
-- (gender_source='admin'); never derivable from the catalogue (spec 11 §1).

CREATE TABLE cat.performers (
    id                serial PRIMARY KEY,
    slug              text UNIQUE NOT NULL,
    name              text NOT NULL,
    gender            text NOT NULL DEFAULT 'unknown'
                        CHECK (gender IN ('female', 'male', 'trans', 'unknown')),
    gender_source     text,
    gender_confidence real,
    status            text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'hidden')),
    n_videos          int NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cat.performer_aliases (
    normalized   text PRIMARY KEY,
    performer_id int NOT NULL REFERENCES cat.performers(id) ON DELETE CASCADE,
    source       text NOT NULL,
    confidence   real,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON cat.performer_aliases (performer_id);

CREATE TABLE cat.video_performers (
    video_id     bigint NOT NULL REFERENCES cat.videos(id) ON DELETE CASCADE,
    performer_id int    NOT NULL REFERENCES cat.performers(id) ON DELETE CASCADE,
    PRIMARY KEY (video_id, performer_id)
);
CREATE INDEX ON cat.video_performers (performer_id);
