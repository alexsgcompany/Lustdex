-- Assign domains to existing projections and add the mix-hetero projection.

UPDATE cat.projections SET domain = 'lustdts.com',   updated_at = now() WHERE slug = 'trans';
UPDATE cat.projections SET domain = 'lustdmilf.com', updated_at = now() WHERE slug = 'milf';

INSERT INTO cat.projections (slug, name, domain, from_verticals, include_tag_ids, exclude_tag_ids)
VALUES
  ('mix', 'Mix (hetero)', 'lustdexxx.com',
   ARRAY['mix'],
   ARRAY[]::int[],
   ARRAY[179, 57]::int[]);  -- shemale, gay
