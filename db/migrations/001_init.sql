CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE providers (
    id          serial PRIMARY KEY,
    slug        text UNIQUE NOT NULL,
    name        text NOT NULL,
    feed_url    text,
    feed_format text,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE raw_videos (
    id             bigserial PRIMARY KEY,
    provider_id    int NOT NULL REFERENCES providers(id),
    external_id    text NOT NULL,
    title          text,
    description    text,
    duration_sec   int,
    target_url     text,
    thumb_url      text,
    tags_raw       text[] NOT NULL DEFAULT '{}',
    performers_raw text[] NOT NULL DEFAULT '{}',
    published_at   timestamptz,
    payload        jsonb NOT NULL,
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider_id, external_id)
);

CREATE TABLE tags (
    id         serial PRIMARY KEY,
    slug       text UNIQUE NOT NULL,
    name       text NOT NULL,
    category   text,
    status     text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tag_aliases (
    normalized text PRIMARY KEY,
    tag_id     int NOT NULL REFERENCES tags(id),
    source     text NOT NULL,
    confidence real,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE unmapped_tags (
    normalized   text PRIMARY KEY,
    raw_examples text[] NOT NULL DEFAULT '{}',
    freq         bigint NOT NULL DEFAULT 0,
    status       text NOT NULL DEFAULT 'pending',
    updated_at   timestamptz NOT NULL DEFAULT now()
);

