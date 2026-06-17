-- Semantic landing pages: vector-driven frozen snapshots.
-- See specs/10-semantic-pages.md §3.
--
-- A second landing-page type alongside cat.page_candidates (spec 03). Source
-- is a stored query_text plus a frozen top-N snapshot of similar video ids
-- pulled from cat.video_embeddings (spec 08). Refresh is manual (spec 10 §S6).

CREATE TABLE cat.semantic_pages (
    id               bigserial PRIMARY KEY,

    -- input
    query_text       text NOT NULL,
    aliases          text[] NOT NULL DEFAULT '{}',
    source           text NOT NULL
        CHECK (source IN ('operator','gsc','site_search','serpstat')),
    source_volume    int,

    -- routing
    slug_provisional text NOT NULL,
    slug_final       text,

    -- frozen result
    video_ids        bigint[] NOT NULL,    -- ORDER BY dist ASC
    top_k            int  NOT NULL,        -- TOP_K used at snapshot time
    max_dist         real NOT NULL,        -- threshold used at snapshot time
    embedding_model  text NOT NULL,        -- which model produced video_ids
    refreshed_at     timestamptz NOT NULL DEFAULT now(),

    -- editorial
    status           text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','approved','rejected')),
    notes            text,
    created_at       timestamptz NOT NULL DEFAULT now(),

    UNIQUE (query_text)
);

CREATE INDEX semantic_pages_video_ids
    ON cat.semantic_pages USING gin (video_ids);

CREATE INDEX semantic_pages_slug
    ON cat.semantic_pages (COALESCE(slug_final, slug_provisional));

CREATE INDEX semantic_pages_status
    ON cat.semantic_pages (status)
    WHERE status = 'approved';
