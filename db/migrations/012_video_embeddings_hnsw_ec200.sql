-- Re-tune HNSW index on cat.video_embeddings to ef_construction=200.
-- See specs/08-embeddings.md §E3 (decision updated after A/B test on 50k:
-- ef_construction=64 vs 200 — recall difference marginal at 50k, but lock in
-- the higher quality before the full 581k+ embed and any future scale).
--
-- DROP + CREATE is safe: embeddings (vectors + hashes) are untouched, only
-- the index is rebuilt. Postgres builds the new HNSW with current row set.

DROP INDEX cat.video_embeddings_hnsw;

CREATE INDEX video_embeddings_hnsw
    ON cat.video_embeddings
    USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200);
