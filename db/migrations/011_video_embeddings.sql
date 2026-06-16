-- Video embeddings (one row per video, one model in MVP).
-- See specs/08-embeddings.md.
--
-- pgvector >= 0.7 ships halfvec + halfvec_cosine_ops. The bundled
-- pgvector/pgvector:pg17 image satisfies this; the `vector` extension is
-- already enabled in migration 001.

CREATE TABLE cat.video_embeddings (
    video_id   bigint PRIMARY KEY REFERENCES cat.videos(id) ON DELETE CASCADE,
    model      text NOT NULL,
    embedding  halfvec(384) NOT NULL,
    input_hash bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX video_embeddings_hnsw
    ON cat.video_embeddings
    USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);
