ALTER TABLE unmapped_tags
    ADD COLUMN suggested_tag_id     int REFERENCES tags(id),
    ADD COLUMN suggested_source     text,
    ADD COLUMN suggested_confidence real,
    ADD COLUMN providers            int[] NOT NULL DEFAULT '{}';

CREATE TABLE pipeline_runs (
    id          bigserial PRIMARY KEY,
    job         text NOT NULL,
    status      text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    stats       jsonb,
    error       text
);
