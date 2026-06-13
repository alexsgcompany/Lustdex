CREATE TABLE video_tags (
    video_id  bigint NOT NULL REFERENCES raw_videos(id),
    tag_id    int    NOT NULL REFERENCES tags(id),
    PRIMARY KEY (video_id, tag_id)
);

CREATE INDEX ON video_tags (tag_id);
