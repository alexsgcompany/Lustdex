"""SQL for the Pages admin section."""

EXISTING_FOR_BASE = """
SELECT member_tag_ids
FROM cat.page_candidates
WHERE member_tag_ids @> %s::int[]
"""

INSERT_CANDIDATE = """
INSERT INTO cat.page_candidates (member_tag_ids, lexical_count, slug_provisional)
VALUES (%s::int[], %s, %s)
ON CONFLICT (member_tag_ids) DO NOTHING
RETURNING id
"""

CANDIDATE_LIST = """
SELECT id, member_tag_ids, lexical_count, slug_provisional, slug_final, status, created_at
FROM cat.page_candidates
ORDER BY created_at DESC
LIMIT 200
"""
