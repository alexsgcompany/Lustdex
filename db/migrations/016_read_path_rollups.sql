-- 016_read_path_rollups.sql — Read-path pre-resolution rollups (spec 13)
--
-- Pre-resolves projection membership + per-tag / per-page counts so cold-cache
-- public-site routes stop re-scanning the whole vertical on every hit. Activates
-- the materialization deferred in spec 06 §9 / P7 ("revisit if slow") now that the
-- slowness is measured (spec 13 §1).
--
-- These tables are DERIVED and DISPOSABLE — rebuilt each pipeline run via
-- shadow-table + atomic swap by `pipeline.projections.refresh` (spec 13 §4/§5).
-- Pure DDL: creates EMPTY tables + indexes on both local and prod; population is
-- the pipeline's job, shipped local→prod by data-sync. Fresh-DB safe.
--
-- No FK constraints on the rollup tables: integrity comes from the rebuild SELECT,
-- and FKs would have to be re-created on every swap cycle (spec 13 §4). The
-- canonical PK + listing index ARE created here (and on each shadow before swap).

-- projection membership: which videos belong to which projection (spec 06 §4).
-- published_at denormalized so the listing slice is an ordered index walk, not a
-- full sort; the index column order is keyset-pagination ready.
CREATE TABLE cat.projection_videos (
    projection_id int    NOT NULL,
    video_id      bigint NOT NULL,
    published_at  timestamptz,
    PRIMARY KEY (projection_id, video_id)
);
CREATE INDEX projection_videos_listing
    ON cat.projection_videos (projection_id, published_at DESC NULLS LAST, video_id DESC);

-- per-(projection, tag) video counts — drives /tag count + tag-facet chips,
-- replacing the full Seq+Hash scan (spec 13 §1).
CREATE TABLE cat.tag_counts (
    projection_id int NOT NULL,
    tag_id        int NOT NULL,
    n             int NOT NULL,
    PRIMARY KEY (projection_id, tag_id)
);

-- per-(projection, page) video counts — drives /p count + the P11 emission gate.
-- Only rows with n > 0 are ever stored: a page absent from this table is below
-- MIN_VIDEOS_PER_PAGE (MVP 1) → site serves 404 + noindex (spec 06 §5).
-- page_id is bigint to match cat.page_candidates.id.
CREATE TABLE cat.page_counts (
    projection_id int    NOT NULL,
    page_id       bigint NOT NULL,
    n             int    NOT NULL,
    PRIMARY KEY (projection_id, page_id)
);

-- suggest typeahead: kill the leading-wildcard ILIKE seq-scans (spec 13 §1, G).
-- pg_trgm is installed but no trgm index existed on the searched columns.
CREATE INDEX tags_name_trgm
    ON cat.tags USING gin (name gin_trgm_ops);

-- page_candidates has no `name` column; suggest matches the effective slug
-- (COALESCE(slug_final, slug_provisional), same shape as semantic_pages_slug).
CREATE INDEX page_candidates_slug_trgm
    ON cat.page_candidates USING gin ((COALESCE(slug_final, slug_provisional)) gin_trgm_ops);
