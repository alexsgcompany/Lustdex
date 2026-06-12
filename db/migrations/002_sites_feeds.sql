CREATE TABLE sites (
    id          serial PRIMARY KEY,
    provider_id int NOT NULL REFERENCES providers(id),
    slug        text UNIQUE NOT NULL,
    domain      text NOT NULL,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE feeds (
    id          serial PRIMARY KEY,
    site_id     int NOT NULL REFERENCES sites(id),
    feed_url    text NOT NULL,
    feed_format text NOT NULL,
    has_header  boolean NOT NULL DEFAULT false,
    max_limit   int NOT NULL,
    niche       text NOT NULL,
    sub_niche   text NOT NULL,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_id)
);

ALTER TABLE raw_videos
    ADD COLUMN feed_id int NOT NULL REFERENCES feeds(id),
    DROP CONSTRAINT raw_videos_provider_id_external_id_key,
    ADD CONSTRAINT raw_videos_feed_external_id_key UNIQUE (feed_id, external_id);
