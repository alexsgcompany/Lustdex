-- Projection brand-tag: the canonical "what this projection is about" tag.
-- See specs/06-projections.md (P-brand) and specs/07-site-db-contract.md
-- (page_candidates contract). Powers two things:
--   1. pipeline/tags/apply.py auto-attaches brand_tag_id to every cat.videos
--      row whose vertical matches the projection's from_verticals — so feeds
--      that imply the genre (e.g. abtranny.com is all shemale) get the tag
--      explicit in cat.video_tags instead of relying on raw_videos.tags_raw.
--   2. Site queries page_candidates with WHERE brand_tag_id = ANY(member_tag_ids)
--      so a tuple like (asian, milf) without shemale never serves on the trans
--      projection. NULL brand_tag_id = no filter (mix).
ALTER TABLE cat.projections
    ADD COLUMN brand_tag_id int REFERENCES cat.tags(id);

UPDATE cat.projections SET brand_tag_id = 179 WHERE slug = 'trans';  -- shemale
UPDATE cat.projections SET brand_tag_id = 81  WHERE slug = 'milf';   -- milf
-- mix: brand_tag_id stays NULL (no single canonical for hetero).
