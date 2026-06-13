CREATE TABLE video_assets (
    id          bigserial PRIMARY KEY,
    video_id    bigint NOT NULL REFERENCES raw_videos(id) ON DELETE CASCADE,
    kind        text NOT NULL,
    path        text NOT NULL,
    sha256      text,
    width       int,
    height      int,
    bytes       int,
    source_url  text NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (video_id, kind)
);
CREATE INDEX ON video_assets (kind);
CREATE INDEX ON video_assets (sha256) WHERE sha256 IS NOT NULL;
