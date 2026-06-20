-- Make the milf projection hetero-only.
-- Overrides migration 008's choice to keep trans-MILF in both projections:
-- milf now excludes shemale + gay, matching the mix projection's hetero rule.
-- See specs/06-projections.md.
-- Slug-keyed (not PK) so it is safe on a fresh DB after 008/009.

UPDATE cat.projections
SET exclude_tag_ids = ARRAY[179, 57]::int[],  -- shemale, gay
    updated_at = now()
WHERE slug = 'milf';
