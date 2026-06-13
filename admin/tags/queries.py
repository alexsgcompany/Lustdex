"""All SQL for the Tags admin section. No pipeline logic here."""

# Coverage by occurrences.
# unmapped_tags.freq = occurrence count per normalized key (all statuses, including trash).
# Resolved = keys that have an entry in tag_aliases.
# This matches the Python-computed coverage from pipeline.tags.apply.
COVERAGE = """
SELECT
    COALESCE(SUM(ut.freq), 0)                                                       AS total_occ,
    COALESCE(SUM(CASE WHEN ta.tag_id IS NOT NULL THEN ut.freq ELSE 0 END), 0)       AS resolved_occ
FROM unmapped_tags ut
LEFT JOIN tag_aliases ta ON ta.normalized = ut.normalized
"""

COUNTERS = """
SELECT
    (SELECT COUNT(*)   FROM tags)                                 AS canonical_total,
    (SELECT COUNT(*)   FROM tag_aliases)                          AS aliases_total,
    (SELECT COUNT(*)   FROM unmapped_tags WHERE status = 'pending') AS queue_pending,
    (SELECT COUNT(*)   FROM unmapped_tags WHERE status = 'trash')   AS queue_trash
"""

ORPHAN_COUNT = """
SELECT COUNT(*)
FROM raw_videos rv
WHERE NOT EXISTS (SELECT 1 FROM video_tags vt WHERE vt.video_id = rv.id)
"""

ORPHAN_LIST = """
SELECT rv.id, rv.title, rv.tags_raw
FROM raw_videos rv
WHERE NOT EXISTS (SELECT 1 FROM video_tags vt WHERE vt.video_id = rv.id)
ORDER BY rv.id
LIMIT 100
"""

CATEGORY_DISTRIBUTION = """
SELECT COALESCE(category, '(none)') AS category, COUNT(*) AS n
FROM tags
GROUP BY 1
ORDER BY n DESC
"""

REVIEW_QUEUE = """
SELECT
    ut.normalized,
    ut.freq,
    ut.raw_examples,
    ut.suggested_tag_id,
    ut.suggested_source,
    ut.suggested_confidence,
    t.slug  AS suggested_slug,
    t.name  AS suggested_name
FROM unmapped_tags ut
LEFT JOIN tags t ON t.id = ut.suggested_tag_id
WHERE ut.status = 'pending'
  {provider_clause}
  {suggestion_clause}
  {freq_clause}
ORDER BY ut.freq DESC
"""

CANONICAL_SEARCH = """
SELECT id, slug, name
FROM tags
WHERE (slug ILIKE %s OR name ILIKE %s)
ORDER BY name
LIMIT 30
"""

DISTINCT_CATEGORIES = """
SELECT DISTINCT category FROM tags WHERE category IS NOT NULL ORDER BY 1
"""

DICTIONARY_LIST = """
SELECT
    t.id,
    t.slug,
    t.name,
    t.category,
    COALESCE(SUM(ut.freq), 0) AS occ_freq
FROM tags t
LEFT JOIN tag_aliases ta ON ta.tag_id = t.id
LEFT JOIN unmapped_tags ut ON ut.normalized = ta.normalized
{where}
GROUP BY t.id
ORDER BY occ_freq DESC
"""

TAG_ALIASES = """
SELECT normalized, source, confidence
FROM tag_aliases
WHERE tag_id = %s
ORDER BY confidence DESC, normalized
"""

TAG_SAMPLE_VIDEOS = """
SELECT rv.id, rv.title
FROM video_tags vt
JOIN raw_videos rv ON rv.id = vt.video_id
WHERE vt.tag_id = %s
LIMIT 5
"""

PIPELINE_RUNS = """
SELECT
    id,
    job,
    status,
    started_at,
    finished_at,
    EXTRACT(EPOCH FROM (COALESCE(finished_at, now()) - started_at))::int AS duration_sec,
    stats,
    error
FROM pipeline_runs
ORDER BY started_at DESC
LIMIT 50
"""

RUNNING_JOBS = """
SELECT DISTINCT job FROM pipeline_runs WHERE status = 'running'
"""
