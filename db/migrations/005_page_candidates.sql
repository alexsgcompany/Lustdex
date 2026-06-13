CREATE TABLE page_candidates (
    id               bigserial PRIMARY KEY,
    kind             text NOT NULL DEFAULT 'tags',
    member_tag_ids   int[] NOT NULL,
    lexical_count    int NOT NULL,
    slug_provisional text NOT NULL,
    slug_final       text,
    volume           int,
    status           text NOT NULL DEFAULT 'approved',
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (member_tag_ids)
);
CREATE INDEX ON page_candidates USING gin (member_tag_ids);
