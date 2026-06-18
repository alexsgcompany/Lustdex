-- Store the query embedding on each semantic page so re-snapshot is pure SQL.
-- See specs/10-semantic-pages.md §S19.
--
-- Split: encoding query_text -> query_vec needs the model (laptop); the
-- snapshot search (query_vec -> video_ids) is plain SQL and runs anywhere,
-- including prod, with no encoder. query_text stays the durable source of
-- truth; query_vec is derived (re-encode on model swap).
--
-- Nullable: pre-existing rows carry NULL until backfilled by
-- `python -m pipeline.semantic_pages.encode --all-missing` (run on the laptop,
-- DATABASE_URL pointed at the target DB).

ALTER TABLE cat.semantic_pages
    ADD COLUMN query_vec halfvec(384);   -- bge-small-en-v1.5 query side, normalized
